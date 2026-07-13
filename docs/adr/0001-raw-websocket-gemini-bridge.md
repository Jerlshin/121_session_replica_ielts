# ADR 0001 — `gemini_bridge.py` speaks raw WebSocket JSON, not the `google-genai` SDK

**Status:** Accepted
**Related:** SPEC_01 §3 (Tech Stack), SPEC_01 §4 (Live Pipeline Routing), SPEC_04 §2 (Phase 2), SPEC_04 §3 (Anti-Regression Discipline)

## Context

SPEC_01 §3 names the `google-genai` SDK as the live orchestration bridge
technology. Phase 2 was explicitly directed to instead implement the bridge
against Google's `BidiGenerateContent` WebSocket API using raw `websockets`
(already a Phase 1 dependency) and hand-rolled JSON framing.

## Decision

`apps/api-gateway/app/services/gemini_bridge.py` opens a raw
`websockets.connect(...)` to the `BidiGenerateContent` endpoint and encodes/
decodes the `setup` / `realtimeInput` / `serverContent` / `sessionResumptionUpdate`
/ `goAway` JSON messages directly, rather than going through the SDK's client
object.

## Why this doesn't fight the spec's actual goals

SPEC_04 §3 makes CI-vendor-decoupling a hard requirement: *"The Live bridge
is tested against recorded session fixtures in CI on every PR... kept
deliberately out of the PR-blocking path so vendor flakiness never blocks a
merge."* A recorded-fixture replay harness is straightforward to build
against a raw WebSocket JSON protocol — `tests/integration/_fake_gemini_live_server.py`
is a ~60-line local `websockets.serve()` that replays
`tests/fixtures/gemini_live_replay/*.json` verbatim. Achieving the same
determinism against the SDK would mean mocking the SDK's internal async
generator/session-object surface, which is both more fragile (breaks silently
on SDK version bumps that don't change the wire protocol at all) and harder
to keep as a reviewable, versioned fixture.

The wire protocol itself (`setup`, `realtimeInput.activityStart/activityEnd`,
`serverContent.modelTurn`, `sessionResumptionUpdate`, `goAway`) is Google's
public contract either way — the SDK is a convenience wrapper over exactly
these messages, not a different capability. Nothing in SPEC_01's architecture
(server-authoritative control, PTT-driven turn boundaries, session
resumption, model-ID configurability) depends on which client library sends
that JSON.

## Consequences

- One more protocol surface for the team to keep in sync with Google's API
  evolution, instead of getting that for free from SDK upgrades. Mitigated
  by pinning the wire-message shapes in one module (`gemini_bridge.py`) and
  the replay fixtures acting as a contract test against our own assumptions.
- If Google ships a wire-incompatible protocol change, we feel it directly
  rather than through an SDK changelog. Given the Live API is still evolving
  (`v1alpha`), this is a real risk to monitor, not a theoretical one.
- Re-adopting `google-genai` later is a localized change (this one file plus
  its tests), not a rearchitecture — the rest of the system talks to
  `gemini_bridge.py`'s typed event/method surface, not to WebSocket frames
  directly.
