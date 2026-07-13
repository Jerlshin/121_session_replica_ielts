from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.db import engine
from app.routers import auth, internal_debug, sessions, ws_exam


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    # Disposes all pooled connections on shutdown. Without this, a pooled
    # asyncpg connection can outlive the event loop it was opened on (e.g.
    # each `TestClient(app)` context spins its own loop) and the next
    # checkout fails with "attached to a different loop".
    await engine.dispose()


app = FastAPI(title="IELTS Speaking Platform — API Gateway", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(sessions.router)
app.include_router(ws_exam.router)
app.include_router(internal_debug.router)


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}
