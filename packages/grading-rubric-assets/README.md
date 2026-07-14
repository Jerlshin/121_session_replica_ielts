# `packages/grading-rubric-assets`

The runtime location for the **licensed, official IELTS band-descriptor
text** the LLM Rubric Judge scores against (Spec 03 §5.1). This directory
is **deliberately empty in source control** — it contains only a
`.gitkeep` — and must stay that way. The real descriptor content is
Cambridge/IDP/British Council intellectual property; this repository
defines the schema and the injection point for it, never the content
itself.

> **If you are looking for the actual band-descriptor wording, it is not
> in this repository and never will be.** It must be procured under a
> proper license and deployed to this path (or an equivalent secret-store
> location) by an operations/legal process outside this codebase.

## Contract

`apps/worker/rubric_assets.py::load_rubric_reference(assets_dir, version="v1")`
is the single reader of this directory, called fresh on every judge
invocation (`apps/worker/tasks/scoring.py::_build_judge_input`) — **never
cached across process lifetimes, never hardcoded into a prompt template.**
It expects exactly one file:

```
{assets_dir}/band_descriptors_v{version}.json
```

### Expected JSON shape

```jsonc
{
  "criteria": {
    "fluency_coherence": {
      "9": "<band 9 descriptor text>",
      "8": "<band 8 descriptor text>",
      "...": "...",
      "1": "<band 1 descriptor text>"
    },
    "lexical_resource": { "9": "...", "...": "...", "1": "..." },
    "grammatical_range_accuracy": { "9": "...", "...": "...", "1": "..." },
    "pronunciation": { "9": "...", "...": "...", "1": "..." }
  }
}
```

- Top-level `criteria` key, containing an entry for each of the four
  `CRITERION_ORDER` criteria (`fluency_coherence`, `lexical_resource`,
  `grammatical_range_accuracy`, `pronunciation`) — a criterion missing
  from the file is simply omitted from the rendered reference text (not
  an error), so a partial rollout of updated descriptors degrades
  gracefully rather than failing closed.
- Each criterion maps band-level string keys (conventionally `"1"`
  through `"9"`, whole or half bands as the licensed text defines them) to
  the descriptor prose for that band.
- `load_rubric_reference()` renders this into the judge's
  `<<OFFICIAL_BAND_DESCRIPTORS>>...<<END_OFFICIAL_BAND_DESCRIPTORS>>` prompt
  block (see `apps/worker/providers/scoring_llm.py::JUDGE_SYSTEM_PROMPT`),
  one section per criterion, **descending** band order.

### Failure behavior

If `band_descriptors_v{version}.json` is missing, or present but
malformed (invalid JSON, or missing the `criteria` key),
`load_rubric_reference()` raises `RubricAssetError` **loudly** — this is
treated as "the licensed asset hasn't been deployed yet," a real
operational gap, never silently papered over with placeholder text in a
production code path. `synthesize_band_scores` lets that exception
propagate and retries (bounded, `max_retries=3`) — it does not catch it
and substitute anything.

## Versioning

The `_v{n}` suffix in the filename mirrors `packages/prompt-templates`'
persona-versioning convention: a revised licensed descriptor set ships as
`band_descriptors_v2.json` alongside the old file, not as an in-place
edit, so any already-graded session's audit trail
(`band_score_reports.judge_input.rubric_reference`, persisted verbatim —
see [`apps/worker/README.md`](../../apps/worker/README.md#the-llm-rubric-judge-phase-7--providersscoring_llmpy-tasksscoringpy))
remains reconstructable against the exact text the judge actually saw,
even after a later version is deployed. `load_rubric_reference`'s
`version` parameter selects which file to read; `apps/worker`'s
`Settings` does not currently expose a version override (defaults to
`"v1"`) — adding one is a straightforward follow-up if a version rollout
needs to be staged.

## Deployment posture

Per `docs/SPEC_01_SYSTEM_ARCHITECTURE.md` §7's object storage topology
table, this is conceptually the same class of asset as `rubric-assets/`
in S3 — "server-side secret store only, never web-accessible." In this
repository's current local-dev/CI form it is read directly off the local
filesystem (`apps/worker/config.py::Settings.rubric_assets_dir`, default
`packages/grading-rubric-assets` relative to the repo root); a real
deployment should treat populating this path (or redirecting
`rubric_assets_dir` to a mounted secret volume) as part of the secret
provisioning process for `apps/worker`, with the same access controls as
`ANTHROPIC_API_KEY`, `AZURE_SPEECH_KEY`, and `DEEPGRAM_API_KEY` — never
committed, never logged, never returned in any API response.

## Testing

Because the real asset can never be committed, every test that needs a
rubric reference points `settings.rubric_assets_dir` at a clearly-labeled
**test-only placeholder** instead:
[`tests/fixtures/rubric_assets/band_descriptors_v1.json`](../../tests/fixtures/rubric_assets/band_descriptors_v1.json) —
generic, self-authored descriptor text carrying an explicit `_notice`
field stating it is not official IELTS wording and must never be treated
as such. `tests/integration/test_synthesize_band_scores.py` and
`tests/integration/test_calibration_pilot.py` both redirect
`settings.rubric_assets_dir` there via `monkeypatch.setattr` before
exercising the judge pipeline. `apps/worker/tasks/calibration.py`'s
dry-run CLI mode goes one step further and skips loading a rubric
reference entirely (`CorpusScriptedScoringLLM` never reads it), so a
bare-repo local calibration run needs neither the real asset nor the test
fixture.

## Cross-references

- Licensing rationale and the S3 `rubric-assets/` deployment analog: `docs/SPEC_01_SYSTEM_ARCHITECTURE.md` §7
- Judge input schema and prompt template that consume this text: `docs/SPEC_03_ASYNC_GRADING_ENGINE.md` §5.1, §5.5
- The sole reader (`load_rubric_reference`) and its caller: [`apps/worker/README.md`](../../apps/worker/README.md)
