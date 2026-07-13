# shared-schemas

Pydantic models (`python/`) and generated TypeScript types (`typescript/`)
shared between `apps/api-gateway`, `apps/worker`, and `apps/web` — e.g.
`FeatureVector`, WS message envelopes.

Grown incrementally as each phase introduces new cross-app payloads,
starting with WS message schemas in Phase 1/2.
