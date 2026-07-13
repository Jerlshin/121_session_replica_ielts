import uuid

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import get_db
from app.models import Candidate

bearer_scheme = HTTPBearer()


async def get_current_candidate(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    db: AsyncSession = Depends(get_db),
) -> Candidate:
    unauthorized = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or expired access token",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(
            credentials.credentials, settings.jwt_secret, algorithms=[settings.jwt_algorithm]
        )
        candidate_id = uuid.UUID(payload["sub"])
    except (JWTError, KeyError, ValueError):
        raise unauthorized

    candidate = await db.scalar(select(Candidate).where(Candidate.id == candidate_id))
    if candidate is None:
        raise unauthorized
    return candidate
