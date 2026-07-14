# `apps/web`

The candidate-facing browser client for the Virtual IELTS Speaking
Examination Platform. A Next.js 14 (App Router) application whose entire
design constraint is CLAUDE.md rule 1: **the client is thin and purely
reactive.** It renders UI configurations pushed by `apps/api-gateway`,
captures and streams microphone audio, and registers Push-to-Talk input —
it never decides its own phase transitions, timer completions, or
evaluations. Every piece of exam-flow state visible here is either a
direct mirror of a server-pushed WebSocket message or purely local UI
ephemera (button pressed/not-pressed).

## Architectural role

```
Microphone/Camera ──► AudioWorklet (PCM16 @16kHz) ──┐
                                                      │  binary WS frames
Camera+Mic (separate) ──► MediaRecorder (WebM/Opus)  │
        │                                            ▼
        │ presigned PUT (direct to S3, never          apps/api-gateway
        │ through the gateway — CLAUDE.md rule 3)      /ws/exam/{id}
        ▼                                                   │
   Object Storage                                            │ JSON control/
                                                               │ caption/status +
   AudioContext jitter buffer ◄── binary audio deltas ────────┘ binary audio deltas
        │
        ▼
     Speaker
```

Two independent media paths leave the browser (CLAUDE.md rule 3,
enforced structurally, not by convention): the **live PCM tap**
(`audio/pcm-worklet-processor.ts` → `ws/exam-socket-client.ts` →
`/ws/exam/{id}`) feeds the real-time Gemini conversation loop and is
tapped server-side for grading evidence; the **proctoring recorder**
(`audio/proctoring-recorder.ts`, `MediaRecorder`, WebM/Opus) is an
entirely separate `MediaStream` consumer that never touches the WS
connection and is uploaded directly browser→S3 via a presigned URL. There
is no code path by which video bytes could reach the live inference loop
or the grading pipeline, because no code path connects them at all.

## Directory structure

```
apps/web/
├── public/worklets/pcm-worklet-processor.js   Runtime AudioWorklet module (see note below)
├── src/
│   ├── app/
│   │   ├── page.tsx                            Login + session-creation entry point
│   │   ├── layout.tsx                          Root layout
│   │   └── exam/[sessionId]/page.tsx           The live exam room — composes every piece below
│   ├── audio/
│   │   ├── pcm-worklet-processor.ts            TS source for the AudioWorklet (see note below)
│   │   ├── audio-worklet-global.d.ts           Ambient type declarations for the worklet realm
│   │   ├── playback-jitter-buffer.ts           Gapless scheduled playback of incoming Gemini audio deltas
│   │   └── proctoring-recorder.ts              Independent WebM/Opus MediaRecorder capture
│   ├── components/
│   │   ├── exam/                               PTTButton, CueCardPanel, CountdownPanel, ConnectionStatusBanner (+ .test.tsx per component)
│   │   └── ui/                                 Reserved, currently empty (.gitkeep)
│   ├── lib/api.ts                              REST client: login, session creation, presigned video upload
│   ├── persistence/turn-buffer.ts              IndexedDB write-ahead buffer for in-flight PTT turns
│   ├── state/examStore.ts                      Zustand store — server-pushed state only
│   └── ws/exam-socket-client.ts                WebSocket client: reconnect/backoff, ping/RTT, turn-buffer integration
├── vitest.config.ts / vitest.setup.ts          Test runner config (jsdom, fake-indexeddb, jest-axe)
├── .env.local.example                          NEXT_PUBLIC_API_BASE_URL / NEXT_PUBLIC_API_WS_BASE_URL
└── package.json
```

> **Maintenance note on the AudioWorklet:** `public/worklets/pcm-worklet-processor.js`
> is a **hand-synced plain-JS build** of `src/audio/pcm-worklet-processor.ts`
> — Next.js has no built-in AudioWorklet bundling, and `audioContext.audioWorklet.addModule()`
> needs a URL the browser can fetch directly, so the file under `public/`
> is what actually loads at runtime, not the TypeScript source. **Any
> change to the TS source must be manually mirrored into the `public/`
> copy** — this is a known, documented gap (a real build step for this is
> a reasonable follow-up, not implemented since Phase 1 was scoped to
> proving the media plumbing end-to-end, not build tooling).

## Component & module reference

### Audio pipeline (`src/audio/`)

- **`pcm-worklet-processor.ts`** — encodes captured mic audio to 16-bit
  PCM, little-endian, mono, framed at exactly 20ms (320 samples) per Spec
  01 §4.1's client→Gemini contract. Requires the owning `AudioContext` to
  be constructed with `{ sampleRate: 16000 }` so no resampling happens
  inside the processor — it only frames and quantizes.
- **`playback-jitter-buffer.ts`** (`PlaybackJitterBuffer`) — schedules
  incoming Gemini audio deltas (24kHz PCM16, Spec 01 §4.1) back-to-back
  via `AudioBufferSourceNode.start(startAt)` rather than at
  `currentTime`, which prevents overlap/glitching under network jitter.
- **`proctoring-recorder.ts`** (`ProctoringRecorder`) — `start()`/`stop()`
  around a `MediaRecorder(stream, { mimeType: "video/webm;codecs=vp8,opus" })`,
  returning a single `Blob` for direct presigned upload. Independent of
  the PCM tap by construction.

### State (`src/state/examStore.ts`)

A `zustand` store holding **only** what the server has told the client —
consistent with CLAUDE.md rule 1:

| Field | Source |
|---|---|
| `connectionStatus` | `idle` \| `connecting` \| `connected` \| `reconnecting` \| `disconnected` — driven by `ExamSocketClient` callbacks |
| `isPttActive` | Local UI state (button press/release) |
| `lastTurnId` | `activity_start_ack` message |
| `cueCard` | `cue_card` message (topic, bullets, cue_card_id) |
| `timerDeadline` | `timer_deadline` message (`{ name, deadlineEpochMs }`) — pushed by `exam_orchestrator.py` on Part 2 prep/long-turn entry **and** on reconnect resume, so a rejoining client renders the real remaining time instead of guessing |

### WebSocket client (`src/ws/exam-socket-client.ts`)

`ExamSocketClient` owns the entire `/ws/exam/{sessionId}` protocol:

- **Auto-reconnect with exponential backoff** (500ms base, 8s cap) on any
  unexpected close — never on a deliberate `.close()` call. Fires
  `onReconnecting()` so the UI can show a `reconnecting` banner instead of
  silently going dark.
- **RTT measurement**: sends `{type: "client_ping", client_ts}` every 10s;
  on the server's `{type: "pong", client_ts, server_ts}` echo, computes
  round-trip time client-side and reports it back via
  `{type: "rtt_report", rtt_ms}` for the gateway's
  `client_gateway_rtt_ms` histogram (see `apps/api-gateway/README.md`'s
  Observability section) — the server cannot measure this hop
  unilaterally.
- **Turn-buffer integration**: every `sendAudioFrame()` call is mirrored
  into `TurnBuffer` (below) before hitting the socket; `activityEnd()`/
  the server's `turn_complete` ack clears the buffered turn. On a fresh
  connection, `replayIncompleteTurn()` checks for a turn that was
  buffered but never ack'd and replays it as a **new** turn
  (`activity_start` → buffered frames → `activity_end`).

### Durable turn buffer (`src/persistence/turn-buffer.ts`)

`TurnBuffer`, backed by `idb` (IndexedDB), is the concrete, honestly-scoped
implementation of "the client-side IndexedDB buffer replays missing
chunks reliably upon automatic session resumption": there is no
backend partial-turn-resume protocol (a fresh `activity_start` always
mints a brand-new server-side `turn_id`), so a turn interrupted by a
socket drop **cannot** be spliced back in mid-stream. What this buffer
guarantees instead is that such a turn is never silently lost — every
frame sent during an active PTT hold is durably persisted first, and an
interrupted turn is replayed in full, from the top, against the
reconnected socket.

### Exam-room components (`src/components/exam/`)

All four ship with a co-located `.test.tsx` asserting both behavior and
zero serious/critical `jest-axe` accessibility violations:

| Component | Purpose | Accessibility notes |
|---|---|---|
| `PTTButton.tsx` | The sole turn-boundary authority (CLAUDE.md rule 2) | `aria-pressed`, dynamic `aria-label`; Space/Enter press-and-hold drive the identical `onPress`/`onRelease` pair as pointer events (auto-repeat guarded so a held key doesn't re-fire `onPress`) |
| `CueCardPanel.tsx` | Renders the server-pushed Part 2 cue card as semantic `<h2>` + `<ul>` | `aria-live="polite"`, moves DOM focus to itself on mount (WCAG 4.1.3 — it appears mid-session with no user-initiated action) |
| `CountdownPanel.tsx` | Renders `timerDeadline - now`, ticking locally against the server-authoritative deadline (never inventing or extending it) | `role="timer"`; screen-reader announcements fire only at meaningful boundaries (60/30/10/5/4/3/2/1/0s), not every tick — continuous live-region updates are a known anti-pattern |
| `ConnectionStatusBanner.tsx` | Visible banner across `connecting`/`reconnecting`/`disconnected` (silent for the steady `connected` state) | `role="status"`, `aria-live="assertive"` |

### `src/app/exam/[sessionId]/page.tsx` — composition root

Wires every piece above together: acquires `getUserMedia({audio, video})`,
constructs the 16kHz `AudioContext` + worklet node, constructs the
`PlaybackJitterBuffer` (24kHz) and `ExamSocketClient`, dispatches every
`ServerMessage` variant into `examStore`, and forwards worklet frames to
the socket only while `isPttActive` — the worklet itself emits
continuously, but PTT (not the worklet) is the sole turn boundary.

## Configuration

`.env.local.example` (copy to `.env.local`):

```
NEXT_PUBLIC_API_BASE_URL=http://localhost:8000
NEXT_PUBLIC_API_WS_BASE_URL=ws://localhost:8000
```

Read via `src/lib/api.ts`'s `wsBaseUrl()` and the `API_BASE_URL` constant;
both default to `localhost:8000` if unset, matching `apps/api-gateway`'s
default `uvicorn` port.

## Dependencies

**Runtime**: `next@^14.2`, `react`/`react-dom@^18.3`, `zustand@^4.5`
(state), `idb@^8` (the turn buffer's IndexedDB wrapper).

**Development/testing** (added Phase 8): `typescript`, `eslint` +
`eslint-config-next`, `vitest@^2.1` + `jsdom` (test runner/DOM),
`@testing-library/react` + `@testing-library/jest-dom` +
`@testing-library/user-event` (component testing), `jest-axe` +
`@types/jest-axe` (accessibility assertions), `fake-indexeddb`
(in-memory IndexedDB for `TurnBuffer` tests).

## Running locally

```bash
cd apps/web
npm install
cp .env.local.example .env.local   # adjust if the gateway isn't on localhost:8000
npm run dev                        # http://localhost:3000
```

Requires `apps/api-gateway` running (see that app's README) for login and
the exam WebSocket to do anything.

## Testing

```bash
npm run lint    # next lint (eslint-config-next)
npx tsc --noEmit
npm test        # vitest run — jsdom + jest-axe + fake-indexeddb
```

The Vitest suite (`vitest.config.ts`) aliases `@/*` to `src/*` (matching
`tsconfig.json`), sets `esbuild.jsx: "automatic"` (React 18's automatic
JSX runtime — Vitest's esbuild transform needs this told explicitly,
since no Babel/Next.js compiler is in the test pipeline), and loads
`vitest.setup.ts` (`fake-indexeddb/auto`, `jest-axe`'s
`toHaveNoViolations` matcher). **Not currently wired into the repo's
Python-only `.github/workflows/ci.yml`** — run manually or add an
`npm test` step if PR-gating this suite is desired (see
[`tests/README.md`](../../tests/README.md) for the overall testing
posture across the monorepo).

## Cross-references

- Audio format contract, hop-by-hop latency budget, video/audio path separation: `docs/SPEC_01_SYSTEM_ARCHITECTURE.md` §3.1, §4
- Cue card / Part 2 timer UX semantics this client renders: `docs/SPEC_02_IELTS_FLOW_STATE_MACHINE.md`
- The WebSocket route and server-side orchestration this client talks to: [`apps/api-gateway/README.md`](../api-gateway/README.md)
