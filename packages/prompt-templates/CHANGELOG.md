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
- Added `directives/part1_wrap_up.txt` and `directives/part1_extend.txt` —
  the graceful-close/continue pair for Part 1's new hard 4-5 minute
  combined window (Spec 02 §4), mirroring Part 2's warn/hard-stop pattern:
  `part1_wrap_up` fires from the ceiling watchdog when Part 1 is still
  running at 5 minutes; `part1_extend` fires when the topic-rotation budget
  would otherwise end Part 1 before the 4-minute floor.
- Added `directives/part3_wrap_up.txt` — fires when Part 3's dynamically
  computed time budget (Spec 02 §4: whatever's left of the 11-14 minute
  total, clamped to ~4-5 minutes) expires before the turn budget does.
- Tightened `directives/part2_roundoff.txt` to ask exactly one optional
  follow-up question (previously "one or two"), matching the product
  requirement that round-off — especially after the 120s hard cutoff — stay
  a single brief wrap-up, not a mini follow-up interview.
