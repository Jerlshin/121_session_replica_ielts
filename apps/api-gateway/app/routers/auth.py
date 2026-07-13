from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends
from jose import jwt
from pydantic import BaseModel, EmailStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import get_db
from app.models import Candidate

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginRequest(BaseModel):
    email: EmailStr
    full_name: str


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    candidate_id: str


def _issue_access_token(candidate_id: str) -> str:
    expires_at = datetime.now(timezone.utc) + timedelta(
        minutes=settings.jwt_access_token_ttl_minutes
    )
    return jwt.encode(
        {"sub": candidate_id, "exp": expires_at},
        settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
    )


@router.post("/login", response_model=LoginResponse)
async def login(body: LoginRequest, db: AsyncSession = Depends(get_db)) -> LoginResponse:
    """Candidate login skeleton: lookup-or-create by email, issue a JWT.

    No exam logic and no ID-verification flow here yet — those are layered
    on in later phases per Spec 04 §2. This exists solely to prove a
    candidate can authenticate and create a session (Phase 0 exit
    criteria).
    """
    candidate = await db.scalar(select(Candidate).where(Candidate.email == body.email))
    if candidate is None:
        candidate = Candidate(email=body.email, full_name=body.full_name)
        db.add(candidate)
        await db.commit()
        await db.refresh(candidate)

    token = _issue_access_token(str(candidate.id))
    return LoginResponse(access_token=token, candidate_id=str(candidate.id))
