import uuid

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import get_db
from app.models import ExamSession, GradingJob, Transcript

router = APIRouter(prefix="/internal", tags=["internal-debug"])


def _require_internal_token(x_internal_debug_token: str | None = Header(default=None)) -> None:
    """Ops/QA debug surface (Spec 04 §2 Phase 5) — not candidate-facing, so
    gated by a shared token rather than the candidate JWT auth every other
    router uses."""
    if not settings.internal_debug_token or x_internal_debug_token != settings.internal_debug_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid internal debug token"
        )


class TranscriptWordResponse(BaseModel):
    turn_id: str
    seq: int
    word: str
    start_ms: int
    end_ms: int
    confidence: float
    speaker: str
    source: str


class SessionDebugResponse(BaseModel):
    session_id: str
    status: str
    finalize_media: dict | None
    transcribe_full_session: dict | None
    canonical_audio_key: str | None
    word_count: int
    words: list[TranscriptWordResponse]


@router.get(
    "/sessions/{session_id}/transcript",
    response_model=SessionDebugResponse,
    dependencies=[Depends(_require_internal_token)],
)
async def get_session_debug(
    session_id: uuid.UUID, db: AsyncSession = Depends(get_db)
) -> SessionDebugResponse:
    """Phase 5 exit criterion: the canonical stitched audio + word-level
    transcript for a closed session, visible via an internal endpoint."""
    session = await db.get(ExamSession, session_id)
    if session is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="session not found")

    jobs = (
        await db.scalars(select(GradingJob).where(GradingJob.session_id == session_id))
    ).all()
    jobs_by_name = {job.task_name: job for job in jobs}
    finalize_job = jobs_by_name.get("finalize_media")
    transcribe_job = jobs_by_name.get("transcribe_full_session")

    words = (
        await db.scalars(
            select(Transcript)
            .where(Transcript.session_id == session_id)
            .order_by(Transcript.start_ms)
        )
    ).all()

    return SessionDebugResponse(
        session_id=str(session_id),
        status=session.status,
        finalize_media=finalize_job.result if finalize_job else None,
        transcribe_full_session=transcribe_job.result if transcribe_job else None,
        canonical_audio_key=(
            finalize_job.result.get("canonical_audio_key")
            if finalize_job and finalize_job.result
            else None
        ),
        word_count=len(words),
        words=[
            TranscriptWordResponse(
                turn_id=str(w.turn_id),
                seq=w.seq,
                word=w.word,
                start_ms=w.start_ms,
                end_ms=w.end_ms,
                confidence=w.confidence,
                speaker=w.speaker,
                source=w.source,
            )
            for w in words
        ],
    )
