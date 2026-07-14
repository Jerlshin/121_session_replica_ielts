# `infra`

Infrastructure-as-config for local development and observability. This
directory currently implements the **local dev stack** and the **Phase 8
latency-observability stack** (Prometheus + Grafana) in full; production
deployment tooling (Kubernetes manifests, Terraform) is scaffolded per
`CLAUDE.md`'s intended tree but was never built out across Phases 0–9 —
see **Status of `k8s/` and `terraform/`** below for the honest accounting
of what exists versus what's aspirational.

## What's actually implemented

```
infra/
├── docker/
│   └── docker-compose.dev.yml      Postgres, Redis, RabbitMQ, MinIO, Prometheus, Grafana
├── prometheus/
│   └── prometheus.yml              Scrape config for the api-gateway's /metrics
└── grafana/
    └── provisioning/
        ├── datasources/prometheus.yml
        └── dashboards/
            ├── dashboards.yml
            └── json/
                └── latency-budget.json     Pre-built Spec 01 §4.4 latency dashboard
```

### `docker/docker-compose.dev.yml` — the shared dev stack

Every app in this monorepo (`apps/api-gateway`, `apps/worker`) reads a
`DATABASE_URL`/`REDIS_URL`/`CELERY_BROKER_URL`/`S3_*` pointed at this
stack by default (see each app's `config.py`), so `docker-compose up -d`
here is the standard first step before running either service.

| Service | Image | Ports | Purpose |
|---|---|---|---|
| `postgres` | `postgres:16` | `5432` | Shared schema — durable session identity, event log, transcripts, feature vectors, band score reports (see [`migrations/README.md`](../migrations/README.md)) |
| `redis` | `redis:7-alpine` | `6379` | Session snapshot cache + Part 2 timer absolute deadlines (`apps/api-gateway/app/services/timers.py`) |
| `rabbitmq` | `rabbitmq:3-management-alpine` | `5672` (AMQP), `15672` (management UI) | Celery broker for the grading pipeline |
| `minio` | `minio/minio:latest` | `9000` (S3 API), `9001` (console) | S3-compatible object storage — raw audio segments, canonical FLAC, proctoring video |
| `prometheus` | `prom/prometheus:latest` | `9090` | Scrapes `apps/api-gateway`'s `/metrics` (Phase 8) |
| `grafana` | `grafana/grafana:latest` | `3001` (host) → `3000` (container) | Pre-provisioned latency dashboard; anonymous viewer access for local dev **only** (`GF_AUTH_ANONYMOUS_ENABLED=true` — never set this in a real deployment) |

Named volumes (`postgres-data`, `redis-data`, `rabbitmq-data`,
`minio-data`, `prometheus-data`, `grafana-data`) persist state across
`docker-compose down`/`up` cycles; nothing here is bind-mounted from the
host, so `.gitignore` has nothing Docker-related to exclude.

**Note on port `3001`**: Grafana is mapped to host port `3001`, not the
image's default `3000`, because `apps/web`'s Next.js dev server already
claims `3000` — both can run simultaneously without a port collision.

**Note on `prometheus`/`grafana` and the gateway process**: the api-gateway
itself is **not** containerized here — per `CLAUDE.md`'s documented dev
command (`cd apps/api-gateway && uvicorn app.main:app --reload`), it runs
directly on the host. `prometheus/prometheus.yml`'s scrape target is
therefore `host.docker.internal:8000`, not a compose service name; the
`prometheus` service's `extra_hosts: host.docker.internal:host-gateway`
entry is what makes that resolve on Linux (Docker Desktop on macOS/Windows
resolves it natively, so the entry is a no-op there but harmless).

```bash
docker-compose -f infra/docker/docker-compose.dev.yml up -d
docker-compose -f infra/docker/docker-compose.dev.yml down          # stop, keep volumes
docker-compose -f infra/docker/docker-compose.dev.yml down -v       # stop, discard all data
```

### `prometheus/prometheus.yml`

A single scrape job (`ielts-api-gateway`) at a 5s interval against
`host.docker.internal:8000/metrics` — the histograms defined in
`apps/api-gateway/app/services/observability.py`
(`gateway_to_gemini_send_ms`, `gemini_response_ms`,
`gateway_relay_enqueue_ms`, `ptt_release_to_first_audio_ms`,
`client_gateway_rtt_ms`). See
[`apps/api-gateway/README.md`](../apps/api-gateway/README.md#observability-appservicesobservabilitypy)
for what each metric actually measures and its documented precision
limits (hops 4+5 are combined, not separable server-side).

### `grafana/`

- **`provisioning/datasources/prometheus.yml`** — auto-registers the
  `prometheus` compose service as Grafana's default datasource on first
  boot; zero manual UI setup required.
- **`provisioning/dashboards/dashboards.yml`** — points Grafana's
  dashboard provider at `provisioning/dashboards/json/`, which the
  container sees at `/etc/grafana/provisioning/dashboards/json` — a
  subdirectory of the single `../grafana/provisioning:/etc/grafana/provisioning:ro`
  bind mount in `docker-compose.dev.yml`, not a second, separately-mounted
  volume (an earlier layout split this into two nested bind mounts, which
  fails at container start: runc has to create the inner mountpoint
  inside an already-mounted read-only filesystem — "OCI runtime create
  failed ... read-only file system").
- **`provisioning/dashboards/json/latency-budget.json`** — one pre-built
  dashboard, `"IELTS Platform — Latency Budget (Spec 01 §4.4)"`, with a
  P50/P95 panel per histogram above (including annotated reference lines
  at the Spec 01 §4.4 target budget: ~380ms P50, ~980ms P95 for the total
  server-observable leg).

Once the stack is up, `http://localhost:3001` shows this dashboard
immediately — no manual datasource or panel configuration needed.

### Verifying the observability stack end-to-end

```bash
docker-compose -f infra/docker/docker-compose.dev.yml up -d
cd apps/api-gateway && uvicorn app.main:app --reload &
# ... drive one real PTT turn through the exam room UI or an integration test ...
curl http://localhost:8000/metrics | grep ptt_release_to_first_audio_ms
open http://localhost:3001   # Grafana — "IELTS Platform — Latency Budget" dashboard
```

## Status of `k8s/` and `terraform/`

`CLAUDE.md`'s repository tree lists `infra/` as covering "Docker Compose
Dev Stack, K8s Manifests, Terraform." Only the first of those three was
built. As of the end of Phase 9, `infra/k8s/` and
`infra/terraform/{networking,secrets,storage}/` exist on disk as **empty,
untracked directories** — no manifests, no `.tf` files, not even a
`.gitkeep`, and nothing in `.github/workflows/ci.yml` references them.
This is stated plainly here rather than left for someone to discover by
finding nothing.

**Why**: `docs/SPEC_04_REPOSITORY_AND_BUILD_PLAN.md`'s nine build phases
(§2) are entirely scoped to proving the product works — media plumbing,
the live Gemini bridge, the exam FSM, resiliency, the async grading
pipeline, and pre-pilot hardening/calibration. None of the nine phases'
"Build" or "Exit criteria" lines mention Kubernetes or Terraform; Phase
8's hardening work explicitly targeted latency profiling, load testing,
security/compliance auditing, and frontend accessibility — production
container orchestration and cloud infrastructure provisioning were never
in scope for the phases actually executed.

**What a real production deployment would still need**, none of which
exists in this repository today:

- **`k8s/`**: Deployment manifests for `apps/api-gateway` (respecting
  Spec 01 §5.6's connection-draining requirement — a stateful WS-holding
  pod needs a `preStop` hook or equivalent that stops accepting new
  sessions before terminating, not a bare rolling-update default) and
  `apps/worker` (per-queue `Deployment`/`HorizontalPodAutoscaler`
  resources matching the `media`/`asr`/`nlp`/`pronunciation`/`scoring`
  queue split in `apps/worker/celery_app.py`), plus `Service`/`Ingress`
  resources and a real Postgres/Redis/RabbitMQ/S3 topology (managed
  services, not the dev stack's single-node containers).
- **`terraform/networking/`**: VPC, subnets, and — per Spec 01 §4.4's
  hop 3 latency note — **co-locating gateway compute in the same cloud
  region as the Gemini Live API endpoint**, which materially dominates
  the latency budget if misplaced.
- **`terraform/secrets/`**: A real secret-manager binding for
  `GEMINI_API_KEY`, `DEEPGRAM_API_KEY`, `AZURE_SPEECH_KEY`,
  `OPENAI_API_KEY`, `JWT_SECRET`, `INTERNAL_DEBUG_TOKEN`, and the
  licensed rubric asset (see
  [`packages/grading-rubric-assets/README.md`](../packages/grading-rubric-assets/README.md)) —
  every one of these currently ships with an insecure, clearly-marked
  local-dev default that **must** be overridden per-environment.
- **`terraform/storage/`**: A managed S3 bucket (replacing MinIO) with
  the lifecycle rules `apps/api-gateway/app/services/media_tap.py` and
  `apps/worker/storage.py`'s `configure_bucket_lifecycle()` already
  express at the application level (`raw-video/` retention,
  `raw_video_retention_days`), SSE-KMS encryption at rest (Spec 01 §8),
  and bucket policy enforcement matching Spec 01 §7's per-prefix access
  table.

## Cross-references

- Full component diagram, deployment/scaling targets, security & compliance notes: `docs/SPEC_01_SYSTEM_ARCHITECTURE.md` §2, §8, §9
- Phased build plan (why `k8s/`/`terraform/` were never reached): `docs/SPEC_04_REPOSITORY_AND_BUILD_PLAN.md`
- The metrics this stack scrapes/visualizes: [`apps/api-gateway/README.md`](../apps/api-gateway/README.md)
- CI's use of equivalent service containers (not this compose file — GitHub Actions `services:` + a manual MinIO step) for integration tests: [`tests/README.md`](../tests/README.md)
