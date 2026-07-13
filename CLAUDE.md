
# CLAUDE.md — Virtual IELTS Speaking Platform System Guide

## 🎯 Project Overview
This repository contains the end-to-end Automated IELTS Speaking Examination Platform. The system drives a live, ultra-low-latency 1-to-1 conversation loop using Gemini (via Push-to-Talk) and passes the recorded data to an asynchronous Celery worker pool for comprehensive 4-criteria rubric grading.

## 🗂️ Single Source of Truth
All implementations must strictly align with the engineering specifications located in the `/docs` directory[cite: 2]:
- `docs/SPEC_01_SYSTEM_ARCHITECTURE.md` (Media paths, State, Resiliency, Storage)[cite: 2]
- `docs/SPEC_02_IELTS_FLOW_STATE_MACHINE.md` (IELTS FSM, Part 2 Timers, Prompt Injection)[cite: 2]
- `docs/SPEC_03_ASYNC_GRADING_ENGINE.md` (Celery Pipeline DAG, Metric Formulations)[cite: 2]
- `docs/SPEC_04_REPOSITORY_AND_BUILD_PLAN.md` (Monorepo Structures & Build Phases)[cite: 2]

## 🛠️ Technology Stack & Structure
The codebase is structured as a single monorepo with explicit boundaries between application gateways and independent, pure packages[cite: 2]:


```

ielts-speaking-platform/
├── apps/
│   ├── web/                     # Next.js 14+ Client App (Zustand, AudioWorklet PCM16 @16kHz)
│   ├── api-gateway/             # FastAPI App (Stateful WS Gateway, Gemini Live API Bridge)
│   └── worker/                  # Celery Application (Asynchronous Grading Engine tasks)
├── packages/
│   ├── shared-schemas/          # Pydantic models (Python) + Compiled TypeScript types
│   ├── exam-fsm/                # Pure state machine logic (Exhaustively unit-tested, no I/O)
│   ├── prompt-templates/        # Versioned Gemini system instructions & context directives
│   └── grading-rubric-assets/   # Secret-managed official band descriptor files
├── infra/                       # Docker Compose Dev Stack, K8s Manifests, Terraform
├── migrations/                  # Alembic DB Migrations
└── tests/                       # Unit, Integration (Gemini Replay Fixtures), and E2E

```

## 💻 Core Development Commands
All commands should be executed from their respective root/sub-app context or via Docker hooks[cite: 2]:
- **Scaffold Infrastructure:** `docker-compose -f infra/docker/docker-compose.dev.yml up -d`[cite: 2]
- **Run Backend Gateway:** `cd apps/api-gateway && uvicorn app.main:app --reload`[cite: 2]
- **Run Celery Worker Pool:** `cd apps/worker && celery -A celery_app worker --loglevel=info`[cite: 2]
- **Run Frontend Client:** `cd apps/web && npm run dev`[cite: 2]
- **Execute Pure Unit Tests:** `pytest tests/unit/`[cite: 2]
- **Execute Integration Tests:** `pytest tests/integration/` (Runs against recorded replay fixtures)[cite: 2]

## 📐 Non-Negotiable Coding Rules
1. **Server-Authoritative Control:** The client is thin and purely reactive[cite: 5]. It renders UI configurations pushed by the backend and registers inputs[cite: 5]. It never triggers its own phase transitions, timer completions, or evaluations[cite: 5].
2. **Push-to-Talk over VAD:** Automated Voice Activity Detection (VAD) is explicitly disabled in the Gemini setup parameters[cite: 5]. Turn boundaries are controlled strictly through the frontend PTT interaction (`activityStart` / `activityEnd`)[cite: 5].
3. **Decoupled Video & Audio Paths:** Audio is raw 16-bit PCM mono at 16 kHz[cite: 5]. Video is captured separately by `MediaRecorder` as an WebM/Opus track and pushed directly to S3/MinIO using presigned URLs[cite: 2, 5]. Video must **never** be processed by live inference or backline grading[cite: 5].
4. **Isolate `exam-fsm` Pure Logic:** Code inside `packages/exam-fsm` must remain completely free of network requests, disk I/O, or asynchronous library hooks so that state changes are perfectly deterministic[cite: 2].
5. **Event-Sourced Resiliency:** Current session states are always rebuilt by folding append-only logs from `exam_session_events`[cite: 5]. Pod failovers or browser reloads must query the logs to resume exactly where the user left off[cite: 5].
6. **Evidence Before Judgment:** The backline grading engine (`apps/worker`) must extract deterministic feature metrics first (e.g., Articulation Rate, MTLD lexical density, Syntactic depth trees, GOP pronunciation alignment) before sending a structured JSON payload to the LLM Rubric Judge[cite: 2, 3]. The Judge must explicitly reference pre-calculated numeric tokens inside its written justifications[cite: 3].
7. **Strict Persona Adherence:** Gemini prompts must strictly preserve the neutral, professional, non-praising IELTS Examiner role[cite: 4]. Guardrails are reinforced via dynamic phase directives injected out-of-band as regular turns utilizing `[EXAMINER_DIRECTIVE]` tokens[cite: 4].

## 🚀 Recommended Build Steps for Claude Code
When prompting the Claude Code terminal, execute implementations in sequential, regression-safe increments according to `SPEC_04` Phase boundaries[cite: 2]:
1. **Phase 0:** Spin up foundational infrastructure, Postgres models, and simple session management[cite: 2].
2. **Phase 1:** Build the core media collection spine (AudioWorklet client capture through WebSockets to object storage)[cite: 2, 5].
3. **Phase 2:** Implement the live bidirectional Gemini bridge using Push-to-Talk hooks[cite: 2, 5].
4. **Phase 3 & 4:** Integrate the FSM logic package and complete full session continuity/resiliency tracking[cite: 2].
5. **Phase 5 to 7:** Wire Celery pipelines, compile algorithmic criteria metrics, and configure the offline grading report engine[cite: 2, 3].

