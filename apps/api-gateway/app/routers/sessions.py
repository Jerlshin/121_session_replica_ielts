import secrets
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.deps import get_current_candidate
from app.models import (
    Candidate,
    ExamSession,
    ExamSessionEvent,
    SessionStatus,
    VideoSegment,
    VideoSegmentStatus,
)
from app.services.presigned_upload import create_video_upload_url

router = APIRouter(prefix="/sessions", tags=["sessions"])


class SessionResponse(BaseModel):
    id: str
    status: str
    current_phase: str | None
    resume_token: str


@router.post("", response_model=SessionResponse, status_code=status.HTTP_201_CREATED)
async def create_session(
    candidate: Candidate = Depends(get_current_candidate),
    db: AsyncSession = Depends(get_db),
) -> SessionResponse:
    """Creates the durable session identity row. No exam FSM logic runs
    here yet (Phase 3) — this only proves the auth + session-creation path
    required by Phase 0's exit criteria."""
    session = ExamSession(
        candidate_id=candidate.id,
        status=SessionStatus.CREATED,
        resume_token=secrets.token_urlsafe(32),
    )
    db.add(session)
    await db.flush()

    db.add(
        ExamSessionEvent(
            session_id=session.id,
            seq=1,
            event_type="SESSION_CREATED",
            payload={"candidate_id": str(candidate.id)},
        )
    )
    await db.commit()
    await db.refresh(session)

    return SessionResponse(
        id=str(session.id),
        status=session.status,
        current_phase=session.current_phase,
        resume_token=session.resume_token,
    )


class VideoUploadURLResponse(BaseModel):
    upload_url: str
    storage_key: str


@router.post(
    "/{session_id}/video-upload-url",
    response_model=VideoUploadURLResponse,
    status_code=status.HTTP_201_CREATED,
)
async def get_video_upload_url(
    session_id: uuid.UUID,
    candidate: Candidate = Depends(get_current_candidate),
    db: AsyncSession = Depends(get_db),
) -> VideoUploadURLResponse:
    """Issues a presigned URL for the proctoring video. The blob is PUT
    directly from the browser to object storage — it never transits this
    API pod (CLAUDE.md rule 3), which is why this endpoint only hands back
    a URL rather than accepting an upload body."""
    session = await db.get(ExamSession, session_id)
    if session is None or session.candidate_id != candidate.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")

    upload = await run_in_threadpool(create_video_upload_url, session_id)
    db.add(
        VideoSegment(
            session_id=session_id,
            storage_key=upload["storage_key"],
            status=VideoSegmentStatus.PENDING,
        )
    )
    await db.commit()

    return VideoUploadURLResponse(**upload)


@router.post("/{session_id}/video-upload-complete", status_code=status.HTTP_204_NO_CONTENT)
async def confirm_video_upload(
    session_id: uuid.UUID,
    candidate: Candidate = Depends(get_current_candidate),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Client calls this once the direct-to-storage PUT succeeds, since
    MinIO/S3 event notifications aren't wired up yet — the pointer row is
    the source of truth for "has this video landed," not an assumption."""
    session = await db.get(ExamSession, session_id)
    if session is None or session.candidate_id != candidate.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")

    video_segment = await db.scalar(
        select(VideoSegment)
        .where(VideoSegment.session_id == session_id)
        .order_by(VideoSegment.created_at.desc())
        .limit(1)
    )
    if video_segment is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No pending upload")

    video_segment.status = VideoSegmentStatus.UPLOADED
    await db.commit()


@router.get("/{session_id}", response_model=SessionResponse)
async def get_session(
    session_id: uuid.UUID,
    candidate: Candidate = Depends(get_current_candidate),
    db: AsyncSession = Depends(get_db),
) -> SessionResponse:
    session = await db.get(ExamSession, session_id)
    if session is None or session.candidate_id != candidate.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")

    return SessionResponse(
        id=str(session.id),
        status=session.status,
        current_phase=session.current_phase,
        resume_token=session.resume_token,
    )
