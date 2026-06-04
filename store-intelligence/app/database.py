"""
Database setup using SQLAlchemy + SQLite (dev) / PostgreSQL (prod).

SQLite is chosen for challenge submission because:
- Zero external dependencies — runs with just `pip install -r requirements.txt`
- Survives docker-compose without a separate DB container
- Sufficient for realistic demo data and test coverage
- Easy to swap to PostgreSQL via DATABASE_URL env var

PostgreSQL path: set DATABASE_URL=postgresql+asyncpg://user:pass@host/db
"""
from __future__ import annotations

import os
from datetime import datetime, date
from typing import Optional, AsyncGenerator

from sqlalchemy import (
    Boolean, Column, DateTime, Float, Integer, String, Text,
    Index, UniqueConstraint, JSON, event as sa_event, text
)
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "sqlite+aiosqlite:///./store_intelligence.db"
)

# Convert postgres:// → postgresql+asyncpg:// for compatibility
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+asyncpg://", 1)

engine = create_async_engine(
    DATABASE_URL,
    echo=False,
    connect_args={"check_same_thread": False} if "sqlite" in DATABASE_URL else {},
)

AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


class EventModel(Base):
    __tablename__ = "events"

    id = Column(String, primary_key=True)
    store_id = Column(String, nullable=False, index=True)
    camera_id = Column(String, nullable=False)
    event_type = Column(String, nullable=False, index=True)
    timestamp = Column(DateTime, nullable=False, index=True)
    track_id = Column(String, nullable=False)
    visitor_id = Column(String, index=True)
    zone_id = Column(String)
    is_staff = Column(Boolean, nullable=False, default=False)
    confidence = Column(Float, nullable=False, default=1.0)
    group_id = Column(String)
    bbox_x = Column(Float)
    bbox_y = Column(Float)
    bbox_w = Column(Float)
    bbox_h = Column(Float)
    metadata_json = Column(JSON)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("id", name="uq_event_id"),
        Index("ix_events_store_ts", "store_id", "timestamp"),
    )


class SessionModel(Base):
    __tablename__ = "visitor_sessions"

    session_id = Column(String, primary_key=True)
    visitor_id = Column(String, nullable=False, index=True)
    store_id = Column(String, nullable=False, index=True)
    entry_time = Column(DateTime, nullable=False, index=True)
    exit_time = Column(DateTime)
    dwell_seconds = Column(Float)
    zones_visited = Column(JSON, default=list)
    is_converted = Column(Boolean, nullable=False, default=False)
    is_staff = Column(Boolean, nullable=False, default=False)
    re_entry_number = Column(Integer, nullable=False, default=0)
    track_ids = Column(JSON, default=list)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    __table_args__ = (
        Index("ix_sessions_store_entry", "store_id", "entry_time"),
    )


class ZoneDwellModel(Base):
    __tablename__ = "zone_dwells"

    id = Column(String, primary_key=True)
    session_id = Column(String, nullable=False, index=True)
    visitor_id = Column(String, nullable=False, index=True)
    store_id = Column(String, nullable=False, index=True)
    zone_id = Column(String, nullable=False, index=True)
    zone_name = Column(String, nullable=False, default="")
    enter_time = Column(DateTime, nullable=False)
    exit_time = Column(DateTime)
    dwell_seconds = Column(Float)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class AnomalyModel(Base):
    __tablename__ = "anomalies"

    id = Column(String, primary_key=True)
    store_id = Column(String, nullable=False, index=True)
    anomaly_type = Column(String, nullable=False)
    severity = Column(String, nullable=False, index=True)
    detected_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    description = Column(Text, nullable=False)
    suggested_action = Column(Text, nullable=False)
    metric_value = Column(Float)
    threshold_value = Column(Float)
    resolved = Column(Boolean, nullable=False, default=False)
    zone_id = Column(String)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    __table_args__ = (
        Index("ix_anomalies_store_severity", "store_id", "severity"),
    )


class StoreModel(Base):
    __tablename__ = "stores"

    id = Column(String, primary_key=True)
    name = Column(String, nullable=False)
    location = Column(String, nullable=False, default="")
    camera_count = Column(Integer, nullable=False, default=1)
    is_active = Column(Boolean, nullable=False, default=True)
    store_layout_json = Column(JSON)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        yield session


async def init_db() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
