from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from config import settings

# api-gateway and this worker share one DATABASE_URL env var pointed at the
# same Postgres instance, but read it with different drivers (asyncpg vs.
# psycopg2) since Celery tasks run outside an event loop — normalize the
# scheme here rather than requiring a second, worker-specific env var.
_sync_database_url = settings.database_url.replace(
    "postgresql+asyncpg://", "postgresql+psycopg2://"
)

engine = create_engine(_sync_database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


@contextmanager
def session_scope() -> Session:
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
