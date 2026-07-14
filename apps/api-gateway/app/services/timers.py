"""Redis-backed absolute-deadline watchdogs for Part 2 (Spec 02 §3.3): a
hard 60s silent prep window and a hard 120s speaking cutoff, enforced
server-side regardless of what the client or the model "think" is
happening. Deadlines are stored as an absolute epoch timestamp (not "seconds
remaining"), which is what lets a resumed connection pick up the same
watchdog from wherever it left off (Spec 01 §5.3/§5.4) instead of resetting.

These functions only *detect* expiry (and, for the long-turn cutoff, perform
the bridge-level mute/directive side effects that don't touch FSM state).
They deliberately never call `fsm_engine.transition` themselves — the
caller (`exam_orchestrator.py`) is the single place that mutates FSM state,
so its in-memory phase cache and the durable event log can never disagree
about who's driving a transition.
"""
import asyncio
import logging
import time
import uuid

import redis.asyncio as aioredis
from exam_fsm import ExamPhase

from app.config import settings
from app.db import AsyncSessionLocal
from app.services import fsm_engine
from app.services.gemini_bridge import GeminiLiveBridge, load_directive

logger = logging.getLogger("app.timers")

_redis: aioredis.Redis | None = None
_redis_loop: asyncio.AbstractEventLoop | None = None


def _get_redis() -> aioredis.Redis:
    """Lazily creates the shared Redis client against whichever event loop
    is currently running, recreating it if that loop has changed. In
    production there is exactly one event loop for the life of the
    gateway process, so this only ever runs once; it exists so a client
    created under one loop's connection pool is never handed to a
    different (possibly already-closed) loop — which is exactly what
    happens when a test suite opens several independent TestClient
    sessions, each with its own event loop, in the same process."""
    global _redis, _redis_loop
    current_loop = asyncio.get_running_loop()
    if _redis is None or _redis_loop is not current_loop:
        _redis = aioredis.from_url(settings.redis_url, decode_responses=True)
        _redis_loop = current_loop
    return _redis

# Small buffer over the nominal duration so a watchdog that's briefly
# unavailable (e.g. mid pod-failover, Phase 4) still finds the key when it
# comes back, rather than racing its own deadline's expiry.
_TTL_BUFFER_SECONDS = 30

_POLL_INTERVAL_SECONDS = 0.25


def _deadline_key(session_id: uuid.UUID, name: str) -> str:
    return f"exam:{session_id}:timer:{name}"


async def set_deadline(session_id: uuid.UUID, name: str, duration_s: float) -> float:
    deadline = time.time() + duration_s
    await _get_redis().set(
        _deadline_key(session_id, name), deadline, ex=int(duration_s) + _TTL_BUFFER_SECONDS
    )
    return deadline


async def get_deadline(session_id: uuid.UUID, name: str) -> float | None:
    raw = await _get_redis().get(_deadline_key(session_id, name))
    return float(raw) if raw is not None else None


async def clear_deadline(session_id: uuid.UUID, name: str) -> None:
    await _get_redis().delete(_deadline_key(session_id, name))


async def mark_phase_start(session_id: uuid.UUID, name: str, ttl_seconds: float) -> float:
    """Persists a wall-clock *start* instant (as opposed to set_deadline's
    future instant) — Part 1's 4-5 minute floor/ceiling and Part 3's
    dynamic remainder budget both need "how long has it actually been
    since X began", which must survive a reconnect the same way Part 2's
    deadlines do (a fresh per-connection ExamOrchestrator can't just keep
    this in an instance variable)."""
    now = time.time()
    await _get_redis().set(_deadline_key(session_id, name), now, ex=int(ttl_seconds))
    return now


async def get_phase_start(session_id: uuid.UUID, name: str) -> float | None:
    raw = await _get_redis().get(_deadline_key(session_id, name))
    return float(raw) if raw is not None else None


async def wait_for_phase_group_deadline(
    session_id: uuid.UUID, name: str, phases: frozenset[ExamPhase]
) -> bool:
    """Generalizes wait_for_prep_expiry's pattern beyond a single phase:
    polls until the named deadline passes or the session leaves the whole
    `phases` group out from under us (e.g. Part 1's ceiling watchdog must
    keep watching across A/B/C, not just whichever sub-phase was active
    when it started). Returns True only if the deadline actually expired
    while still somewhere in `phases`."""
    deadline = await get_deadline(session_id, name)
    if deadline is None:
        logger.warning(
            "wait_for_phase_group_deadline: no deadline set for session=%s name=%s",
            session_id,
            name,
        )
        return False

    while time.time() < deadline:
        async with AsyncSessionLocal() as db:
            phase = await fsm_engine.get_current_phase(db, session_id)
        if phase not in phases:
            return False
        await asyncio.sleep(_POLL_INTERVAL_SECONDS)
    return True


async def wait_for_prep_expiry(session_id: uuid.UUID) -> bool:
    """Polls until the `part2_prep` deadline passes or the session leaves
    `PART2_PREP` out from under us. Returns True if the deadline actually
    expired (caller should fire PREP_TIMER_EXPIRED), False if the phase
    changed underneath us (e.g. resumed elsewhere) — a clean, silent exit,
    mirroring the spec pseudocode's "phase changed underneath us — bail"."""
    deadline = await get_deadline(session_id, "part2_prep")
    if deadline is None:
        logger.warning("wait_for_prep_expiry: no deadline set for session=%s", session_id)
        return False

    while time.time() < deadline:
        async with AsyncSessionLocal() as db:
            phase = await fsm_engine.get_current_phase(db, session_id)
        if phase != ExamPhase.PART2_PREP:
            return False
        await asyncio.sleep(_POLL_INTERVAL_SECONDS)
    return True


async def wait_for_long_turn_cutoff(
    session_id: uuid.UUID,
    bridge: GeminiLiveBridge,
    *,
    warn_at_s: float,
    hard_cutoff_s: float,
) -> bool:
    """This is the one place in the whole exam where the backend forcibly
    interrupts the model rather than waiting for it to yield the turn (Spec
    02 §3.3/§3.4). Injects the warn/hard-stop directives and mutes input
    itself (bridge-level side effects, safe here); returns True only once
    the hard cutoff has actually fired, so the caller knows to advance the
    FSM to PART2_ROUNDOFF."""
    deadline = await get_deadline(session_id, "part2_long_turn")
    if deadline is None:
        logger.warning("wait_for_long_turn_cutoff: no deadline set for session=%s", session_id)
        return False

    warned = False
    while True:
        remaining = deadline - time.time()

        async with AsyncSessionLocal() as db:
            phase = await fsm_engine.get_current_phase(db, session_id)
        if phase != ExamPhase.PART2_LONG_TURN:
            return False  # candidate finished early — normal exit

        if remaining <= (hard_cutoff_s - warn_at_s) and not warned:
            await bridge.inject_directive(
                load_directive(settings.prompt_templates_dir, "part2_warn")
            )
            warned = True

        if remaining <= 0:
            bridge.force_mute_input()  # stop forwarding candidate audio even if PTT is
            # still physically held
            await bridge.inject_directive(
                load_directive(settings.prompt_templates_dir, "part2_hard_stop")
            )
            return True

        await asyncio.sleep(_POLL_INTERVAL_SECONDS)
