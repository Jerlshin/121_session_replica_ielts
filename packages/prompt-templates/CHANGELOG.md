# Prompt Template Changelog

Every change to a persona or directive asset in this package is a reviewable
diff, per Spec 04 §1 — prompt changes must never be buried as inline strings
in application code.

## Unreleased

- Added `base_persona_v1.txt` — the fixed system instruction (Spec 02 §6.1).
  System instructions cannot change mid Live-API-connection, only at
  session-resumption boundaries, so this file is the stable v1 persona.
- Added `directives/connectivity_test.txt` — the Phase 2 scripted "say hello
  back" audio-link check (Spec 04 §2, Phase 2 build item). Not a real exam
  phase; superseded as the gateway's connect-time directive by `intro.txt`
  in Phase 3, but left in place as a still-valid standalone asset.
- Added the Phase 3 phase-directive set (Spec 02 §6.2), one per FSM phase
  transition, injected by `app/services/exam_orchestrator.py`:
  `intro.txt`, `part1_topic.txt`, `part2_cuecard_present.txt`,
  `part2_warn.txt` / `part2_hard_stop.txt` (Part 2's hard-cutoff pair, Spec
  02 §3.4), `part2_roundoff.txt`, `part3_discussion.txt`, `close.txt`.
- Added `directives/reanchor.txt` — the periodic guardrail re-anchor (Spec
  02 §6.3), injected every `settings.reanchor_every_n_turns` turns within a
  long phase so persona drift doesn't accumulate over a 15-20 minute
  session.
