import asyncio
import json
import logging
import time
import uuid

from exam_fsm import ExamPhase
from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect, WebSocketException, status
from fastapi.concurrency import run_in_threadpool
from jose import JWTError, jwt
from sqlalchemy.dialects.postgresql import insert
from websockets.exceptions import ConnectionClosed

from app.config import settings
from app.db import AsyncSessionLocal
from app.models import AudioSegment, ExamSession
from app.services.exam_orchestrator import ExamOrchestrator
from app.services.gemini_bridge import (
    AudioDelta,
    GeminiLiveBridge,
    GoAway,
    Interrupted,
    SessionResumptionUpdate,
    TranscriptDelta,
    TurnComplete,
    load_base_persona,
)
from app.services import observability
from app.services.media_tap import persist_turn_audio

router = APIRouter(tags=["ws"])
logger = logging.getLogger("app.ws_exam")

# Spec 01 §4.4 total P95 budget (PTT-release -> first audible reply) across
# all 7 hops. We can only measure hops 3-5 (gateway->Gemini->gateway) from
# here; hops 1/2/6/7 are client/network and not observable server-side.
_LATENCY_P95_BUDGET_MS = 980


async def _authenticate(token: str, session_id: uuid.UUID) -> str | None:
    """Validates the token against the session and returns any stored
    Gemini resumption handle (Spec 01 §5.3) so the caller can attempt a
    transparent resume instead of starting context from scratch."""
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
        candidate_id = uuid.UUID(payload["sub"])
    except (JWTError, KeyError, ValueError) as exc:
        raise WebSocketException(code=status.WS_1008_POLICY_VIOLATION) from exc

    async with AsyncSessionLocal() as db:
        session = await db.get(ExamSession, session_id)
        if session is None or session.candidate_id != candidate_id:
            raise WebSocketException(code=status.WS_1008_POLICY_VIOLATION)
        return session.gemini_resumption_handle


async def _persist_resumption_handle(session_id: uuid.UUID, handle: str) -> None:
    async with AsyncSessionLocal() as db:
        session = await db.get(ExamSession, session_id)
        if session is not None:
            session.gemini_resumption_handle = handle
            await db.commit()


async def _flush_turn(
    session_id: uuid.UUID, turn_id: uuid.UUID, pcm_bytes: bytes, exam_phase: ExamPhase
) -> dict:
    seq = 1  # single flush per turn (Spec 01 §4.2); retries reuse the same
    # seq so the insert below is idempotent rather than a duplicate write.
    metadata = await run_in_threadpool(persist_turn_audio, session_id, turn_id, seq, pcm_bytes)

    async with AsyncSessionLocal() as db:
        stmt = (
            insert(AudioSegment)
            .values(
                session_id=session_id,
                turn_id=turn_id,
                seq=seq,
                storage_key=metadata["storage_key"],
                checksum=metadata["checksum"],
                byte_size=metadata["byte_size"],
                exam_phase=exam_phase.value,
            )
            .on_conflict_do_nothing(constraint="uq_audio_segment_key")
        )
        await db.execute(stmt)
        await db.commit()

    return metadata


async def _flush_turn_in_background(
    outbox: asyncio.Queue,
    session_id: uuid.UUID,
    turn_id: uuid.UUID,
    pcm_bytes: bytes,
    orchestrator: ExamOrchestrator,
    exam_phase: ExamPhase,
) -> None:
    """Runs off the relay's critical path (Spec 01 §4.2: "async append...
    non-blocking") so persisting the candidate's turn audio never delays
    Gemini's reply reaching the client. Always reports back to the
    orchestrator (success or failure) so FINALIZING's flush-drain watchdog
    (Spec 02 §1) can never hang on a turn that failed to persist."""
    try:
        metadata = await _flush_turn(session_id, turn_id, pcm_bytes, exam_phase)
        await outbox.put(
            {
                "type": "turn_complete",
                "turn_id": str(turn_id),
                "byte_size": metadata["byte_size"],
                "checksum": metadata["checksum"],
            }
        )
    except Exception:
        logger.exception("media flush failed session=%s turn=%s", session_id, turn_id)
    finally:
        orchestrator.on_turn_flush_complete(turn_id)


@router.websocket("/ws/exam/{session_id}")
async def exam_media_loopback(
    websocket: WebSocket, session_id: uuid.UUID, token: str = Query(...)
) -> None:
    """The live exam room (Spec 04 §2 Phases 2-3): PTT press/hold/release
    drives activityStart/audio/activityEnd against a real Gemini Live
    connection (VAD disabled, Spec 01 §4.1 / CLAUDE.md rule 2); Gemini's
    audio + caption deltas are relayed back to the client as they arrive.
    Candidate audio is still tapped to object storage (Spec 01 §4.2/§5.5),
    but that write now runs in the background so it can never add to the
    PTT-release -> first-audible-reply latency budget (Spec 01 §4.4). An
    `ExamOrchestrator` (Phase 3) drives phase transitions, Part 2 timer
    watchdogs, and directive injection on top of this relay.
    """
    resumption_handle = await _authenticate(token, session_id)
    await websocket.accept()

    outbox: asyncio.Queue = asyncio.Queue()

    bridge = GeminiLiveBridge(
        session_id=session_id,
        api_key=settings.gemini_api_key,
        model_id=settings.live_model_id,
        ws_url=settings.gemini_live_ws_url,
        system_instruction=load_base_persona(settings.prompt_templates_dir),
        resumption_handle=resumption_handle,
    )
    await bridge.connect()

    await outbox.put({"type": "connected", "session_id": str(session_id)})
    if resumption_handle:
        await outbox.put({"type": "resumed", "session_id": str(session_id)})

    # Phase 3 (Spec 04 §2): drives the real exam FSM — auto-passing the
    # not-yet-built device-check/ID-verification UI into INTRO on a fresh
    # session, or re-anchoring guardrails and resuming any live Part 2
    # watchdog if the session already has phase history.
    orchestrator = ExamOrchestrator(session_id=session_id, bridge=bridge, outbox=outbox)
    await orchestrator.start()

    turn_id: uuid.UUID | None = None
    buffer = bytearray()
    # t0/t1 bracket hop 3 (gateway->Gemini send, Spec 01 §4.4) -- captured
    # here since activity_end handling and first-AudioDelta handling live
    # in two different coroutines below; see observability.py's docstring
    # for the full t0-t3 hop breakdown.
    turn_t0: float | None = None
    turn_t1: float | None = None
    awaiting_first_audio = False

    async def writer() -> None:
        while True:
            item = await outbox.get()
            if item is None:
                return
            if isinstance(item, bytes):
                await websocket.send_bytes(item)
            else:
                await websocket.send_json(item)

    async def pump_client_to_gemini() -> None:
        nonlocal turn_id, buffer, turn_t0, turn_t1, awaiting_first_audio
        while True:
            message = await websocket.receive()
            if message["type"] == "websocket.disconnect":
                return

            text_payload = message.get("text")
            bytes_payload = message.get("bytes")

            if text_payload is not None:
                control = json.loads(text_payload)
                control_type = control.get("type")

                if control_type == "activity_start":
                    turn_id = uuid.uuid4()
                    buffer = bytearray()
                    await bridge.send_activity_start()
                    await outbox.put({"type": "activity_start_ack", "turn_id": str(turn_id)})

                elif control_type == "activity_end":
                    if turn_id is None:
                        continue
                    turn_t0 = time.time()
                    await bridge.send_activity_end()
                    turn_t1 = time.time()
                    # Must run before on_activity_end(): it captures the
                    # phase this turn was actually spoken in, before that
                    # same activity_end potentially advances the phase
                    # (Spec 03 §4 per-phase bucketing — see migration
                    # 0005's docstring for why the ordering matters).
                    turn_phase = orchestrator.on_turn_flush_started(turn_id)
                    await orchestrator.on_activity_end()
                    awaiting_first_audio = True
                    asyncio.create_task(
                        _flush_turn_in_background(
                            outbox, session_id, turn_id, bytes(buffer), orchestrator, turn_phase
                        )
                    )
                    turn_id = None
                    buffer = bytearray()

                elif control_type == "cue_card_ack":
                    await orchestrator.on_cue_card_ack()

                elif control_type == "client_ping":
                    # Client<->gateway RTT (Spec 01 §4.4 hop 2 proxy) can't
                    # be measured unilaterally server-side — echoed back so
                    # the client can compute it and report it via
                    # rtt_report (see observability.py's module docstring).
                    await outbox.put(
                        {
                            "type": "pong",
                            "client_ts": control.get("client_ts"),
                            "server_ts": time.time(),
                        }
                    )

                elif control_type == "rtt_report":
                    rtt_ms = control.get("rtt_ms")
                    if isinstance(rtt_ms, (int, float)):
                        observability.record_client_rtt(session_id=session_id, rtt_ms=float(rtt_ms))

            elif bytes_payload is not None:
                if turn_id is None:
                    # A frame arriving outside an active turn is dropped —
                    # PTT activity_start/activity_end is the sole turn
                    # boundary authority (CLAUDE.md rule 2).
                    continue
                buffer.extend(bytes_payload)
                await bridge.send_audio_frame(bytes_payload)

    async def pump_gemini_to_client() -> None:
        nonlocal turn_t0, turn_t1, awaiting_first_audio
        async for event in bridge.receive_events():
            if isinstance(event, AudioDelta):
                if awaiting_first_audio and turn_t0 is not None and turn_t1 is not None:
                    t2 = time.time()
                    await outbox.put(event.pcm_bytes)
                    t3 = time.time()
                    awaiting_first_audio = False
                    values = observability.record_ptt_turn(
                        session_id=session_id, t0=turn_t0, t1=turn_t1, t2=t2, t3=t3
                    )
                    elapsed_ms = values["ptt_release_to_first_audio_ms"]
                    log = logger.warning if elapsed_ms > _LATENCY_P95_BUDGET_MS else logger.info
                    log(
                        "turn_latency session=%s server_hop_ms=%.1f budget_p95_ms=%d "
                        "(gateway->Gemini->gateway only; excludes client/network hops)",
                        session_id,
                        elapsed_ms,
                        _LATENCY_P95_BUDGET_MS,
                    )
                else:
                    await outbox.put(event.pcm_bytes)
            elif isinstance(event, TranscriptDelta):
                await outbox.put({"type": "transcript_delta", "text": event.text})
            elif isinstance(event, TurnComplete):
                await outbox.put({"type": "gemini_turn_complete"})
                await orchestrator.on_gemini_turn_complete()
            elif isinstance(event, Interrupted):
                await outbox.put({"type": "interrupted"})
            elif isinstance(event, SessionResumptionUpdate):
                if event.resumable:
                    await _persist_resumption_handle(session_id, event.handle)
            elif isinstance(event, GoAway):
                await outbox.put({"type": "server_going_away", "time_left_ms": event.time_left_ms})

    writer_task = asyncio.create_task(writer())
    client_task = asyncio.create_task(pump_client_to_gemini())
    gemini_task = asyncio.create_task(pump_gemini_to_client())

    try:
        done, pending = await asyncio.wait(
            {client_task, gemini_task}, return_when=asyncio.FIRST_COMPLETED
        )
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        for task in done:
            exc = task.exception()
            # A clean browser disconnect or Gemini closing the WS after a
            # GoAway are expected end-of-session paths here, not bugs — full
            # reconnect/resume orchestration on top of them is Phase 4
            # (Spec 01 §5.3/§5.6).
            if exc is not None and not isinstance(exc, (WebSocketDisconnect, ConnectionClosed)):
                raise exc
    except (WebSocketDisconnect, ConnectionClosed):
        pass
    finally:
        await outbox.put(None)
        await asyncio.gather(writer_task, return_exceptions=True)
        await orchestrator.close()
        await bridge.close()
