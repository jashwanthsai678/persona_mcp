"""SQLAlchemy models + session for user accounts and persona metadata.

This DB holds accounts and persona metadata/tokens only. Persona memory
itself lives in the per-persona Chroma store under personas/<persona_id>/
(see persona_mcp.storage) - kept separate so the vector store never needs to
know about users/auth at all.
"""

import os
import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, String, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship, sessionmaker

from persona_mcp.config import REPO_ROOT

DB_PATH = os.environ.get("PERSONA_MCP_DB_PATH", str(REPO_ROOT / "webapp" / "app.db"))
engine = create_engine(f"sqlite:///{DB_PATH}", connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def _uuid() -> str:
    return uuid.uuid4().hex


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    email: Mapped[str] = mapped_column(String, unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    personas: Mapped[list["Persona"]] = relationship(back_populates="owner", cascade="all, delete-orphan")


class Persona(Base):
    __tablename__ = "personas"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(String)
    mcp_token: Mapped[str] = mapped_column(String, unique=True, index=True, default=lambda: uuid.uuid4().hex + uuid.uuid4().hex)
    status: Mapped[str] = mapped_column(String, default="created")  # created | processing | ready | error
    status_message: Mapped[str] = mapped_column(String, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)

    owner: Mapped[User] = relationship(back_populates="personas")


def init_db() -> None:
    Base.metadata.create_all(engine)


def get_session() -> Session:
    return SessionLocal()
