# `packages/prompt-templates`

Versioned Gemini system instructions and `[EXAMINER_DIRECTIVE]` phase
directives — the entire persona-and-behavior contract for the live
Examiner (CLAUDE.md rule 7, Spec 02 §6). This package exists so that
**prompt changes are a reviewable, versioned diff to a plain-text asset,
never an inline string buried in application code**
(`docs/SPEC_04_REPOSITORY_AND_BUILD_PLAN.md` §1). No Python or TypeScript
lives here — it is pure content, loaded and rendered at runtime by
`apps/api-gateway/app/services/gemini_bridge.py`'s `load_base_persona()`
and `load_directive()`.

## Why prompt text is externalized like this

Two enforced properties:

1. **A single system instruction per Live connection.** Gemini's Live API
   accepts a system instruction only at connection setup — it cannot be
   changed mid-connection, only at a session-resumption boundary. That
   makes `base_persona_v1.txt` a fixed, load-once asset per connection,
   not something templated per turn.
2. **All in-conversation steering happens out-of-band**, via
   `[EXAMINER_DIRECTIVE]...[/EXAMINER_DIRECTIVE]`-wrapped turns injected
   as regular `clientContent` messages (`gemini_bridge.py::inject_directive`).
   The base persona's own conversational rule 1 instructs the model to
   treat these as its own next intention — never spoken aloud, never
   acknowledged, never referenced ("as I was just told to say"). This is
   the mechanism CLAUDE.md rule 7 refers to as "dynamic phase directives
   injected out-of-band as regular turns."

## Directory structure

```
packages/prompt-templates/
├── base_persona_v1.txt          The fixed system instruction (loaded once per connection)
├── CHANGELOG.md                  Reviewable history of every persona/directive change
└── directives/
    ├── connectivity_test.txt     Phase 2's scripted "say hello back" audio-link check (superseded as the connect-time directive, kept as a valid standalone asset)
    ├── intro.txt                 Injected on entering INTRO
    ├── part1_topic.txt           Injected on entering PART1_TOPIC_A/B/C — templated with {topic_title}, {questions}
    ├── part2_cuecard_present.txt Injected on entering PART2_CUECARD_PRESENT — templated with {topic}, {bullets}
    ├── part2_warn.txt            Injected ~5s before Part 2's hard cutoff (Spec 02 §3.4)
    ├── part2_hard_stop.txt       Injected at the hard 120s cutoff itself, alongside a forced input mute
    ├── part2_roundoff.txt        Injected on entering PART2_ROUNDOFF
    ├── part3_discussion.txt      Injected on entering PART3_DISCUSSION — templated with {themes} (Part 2's linked_part3_themes)
    ├── close.txt                 Injected on entering CLOSE
    └── reanchor.txt              Periodic guardrail re-anchor — no template variables
```

### `base_persona_v1.txt` — the fixed system instruction

Four sections, each doing distinct work:

- **`[PERSONA]`** — establishes "the Examiner": calm, neutral,
  professionally warm, explicitly *not* the candidate's friend, coach, or
  tutor.
- **`[CONVERSATIONAL RULES]`**, in priority order — directive handling
  (rule 1, described above), one-question-at-a-time discipline, strict
  phase adherence ("do not skip ahead or go back a phase"), and a hard
  ban on ever explaining, correcting, teaching, or defining a word during
  the exam.
- **`[GUARDRAILS]`** — no praise or quality evaluation of any kind (a real
  IELTS examiner gives no in-exam feedback), never breaks character to
  reveal it is an AI, never offers generic assistant helpfulness, never
  discusses how scoring works, and a documented (currently directive-less)
  hook for distress-handling.

This is the file `gemini_bridge.py::connect()` sends verbatim as
`setup.systemInstruction.parts[0].text` — see
[`apps/api-gateway/README.md`](../../apps/api-gateway/README.md#appservices--the-actual-engineering).

### `directives/` — templated, phase-triggered stage directions

Every directive follows the identical wrapper format:

```
[EXAMINER_DIRECTIVE]
<instruction text, optionally with {python_format_placeholders}>
[/EXAMINER_DIRECTIVE]
```

Rendered by `apps/api-gateway/app/services/exam_orchestrator.py::_inject_template()`
via plain `str.format(**kwargs)` — **not** Jinja2 or any templating
engine, since the substitution need is limited to simple named
placeholders (`{topic_title}`, `{questions}`, `{topic}`, `{bullets}`,
`{themes}`) sourced directly from the FSM's already-selected content
(`CueCard`/`TopicSet` rows). A directive with no placeholders (e.g.
`reanchor.txt`, `close.txt`) is loaded and injected as-is.

Two directives are mechanically special, not just contextually so:

- **`part2_warn.txt` / `part2_hard_stop.txt`** — the only pair injected by
  a *timer watchdog* (`app/services/timers.py::wait_for_long_turn_cutoff`)
  rather than by a normal FSM phase-entry callback. `part2_hard_stop.txt`
  fires alongside `bridge.force_mute_input()` — the one place in the
  entire exam where the backend forcibly interrupts the model rather than
  waiting for it to yield the turn (Spec 02 §3.3/§3.4).
- **`reanchor.txt`** — injected periodically (every
  `settings.reanchor_every_n_turns` turns, default `6`) within any long
  phase, and also once immediately on every session **resume**
  (`exam_orchestrator.py::start()`), so persona drift never silently
  accumulates over a 15–20 minute session and a reconnecting session
  re-establishes guardrails before any further directive fires.

## Versioning discipline

`CHANGELOG.md` is the authoritative record of every persona/directive
addition or change — update it in the same commit as any content change
here, per the "reviewable diff, never buried in application code"
principle this package exists to enforce. `base_persona_v1.txt`'s `_v1`
suffix is deliberate: a future persona revision ships as `base_persona_v2.txt`
alongside the old file (which `gemini_bridge.py::load_base_persona()`
would then be pointed at via a config change), not as an in-place edit —
consistent with the "system instructions can't change mid-connection"
constraint above meaning a persona version is effectively pinned per
connection, and any in-flight session using `_v1` must keep resolving to
exactly that text even after `_v2` ships.

## Consumers

Loaded exclusively by
`apps/api-gateway/app/services/gemini_bridge.py`:

```python
def load_base_persona(prompt_templates_dir) -> str:
    return (prompt_templates_dir / "base_persona_v1.txt").read_text()

def load_directive(prompt_templates_dir, name: str) -> str:
    return (prompt_templates_dir / "directives" / f"{name}.txt").read_text()
```

`prompt_templates_dir` is `settings.prompt_templates_dir`
(`apps/api-gateway/app/config.py`), defaulting to this package's path
relative to the repo root — no separate deployment/copy step is needed in
local dev; production deployments should treat this directory the same
way as any other versioned application asset (not a secret, unlike
`packages/grading-rubric-assets`).

## Testing

There is no dedicated test suite for this package's content itself (plain
text has nothing to unit-test in isolation); its correctness is verified
indirectly by the integration tests that drive a full exam session against
the fixture-replay Gemini server and assert the right directive fires at
the right phase transition — most directly
`tests/integration/test_full_exam_session_flow.py` (walks every phase in
order) and `tests/integration/test_exam_room_gemini_relay.py`. A missing
or renamed directive file surfaces immediately as a `FileNotFoundError`
from `load_directive()`, not a silent no-op.

## Cross-references

- Phase-directive injection mechanism, guardrail re-anchoring cadence, Part 2's hard-cutoff pair: `docs/SPEC_02_IELTS_FLOW_STATE_MACHINE.md` §6
- Persona adherence requirement (CLAUDE.md rule 7) and why raw WebSocket JSON (not the `google-genai` SDK) carries these messages: [`docs/adr/0001-raw-websocket-gemini-bridge.md`](../../docs/adr/0001-raw-websocket-gemini-bridge.md)
- The sole consumer: [`apps/api-gateway/README.md`](../../apps/api-gateway/README.md)
