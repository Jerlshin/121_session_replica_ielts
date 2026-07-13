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
  phase; used once per fresh (non-resumed) Gemini Live connection to prove
  the bridge is wired correctly end-to-end.
