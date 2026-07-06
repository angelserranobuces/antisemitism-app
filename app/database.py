"""Database models and CRUD operations using SQLAlchemy + SQLite."""

import os
import json
from datetime import datetime, date
from typing import Optional, Dict, Any, List

from sqlalchemy import (
    create_engine, Column, Integer, String, Float, Date,
    DateTime, Text, UniqueConstraint, event
)
from sqlalchemy.orm import DeclarativeBase, sessionmaker, Session
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./data/antisemitism.db")

# Render gives postgres:// but SQLAlchemy 2.x requires postgresql://
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

# Only create local data dir when using SQLite
if "sqlite" in DATABASE_URL:
    os.makedirs("data", exist_ok=True)

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False} if "sqlite" in DATABASE_URL else {},
)

# Enable WAL mode for SQLite (better concurrency)
if "sqlite" in DATABASE_URL:
    @event.listens_for(engine, "connect")
    def set_sqlite_pragma(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.close()

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


class NewspaperAnalysis(Base):
    __tablename__ = "newspaper_analyses"

    id = Column(Integer, primary_key=True, index=True)
    newspaper = Column(String(100), nullable=False)
    date = Column(Date, nullable=False)
    category_scores = Column(Text, nullable=False)   # JSON string
    overall_rating = Column(Float, nullable=False)
    evidence = Column(Text, nullable=True)            # JSON string
    source_filename = Column(String(255), nullable=True)
    methodology_version = Column(String(50), default="1.0")
    processing_timestamp = Column(DateTime, default=datetime.utcnow)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("newspaper", "date", name="uq_newspaper_date"),
    )


def create_tables():
    Base.metadata.create_all(bind=engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ── CRUD ──────────────────────────────────────────────────────────────────────

def upsert_analysis(
    db: Session,
    newspaper: str,
    analysis_date: date,
    category_scores: Dict[str, Any],
    overall_rating: float,
    evidence: List[Dict],
    source_filename: str,
) -> NewspaperAnalysis:
    """Insert or update a newspaper analysis record."""
    existing = (
        db.query(NewspaperAnalysis)
        .filter(
            NewspaperAnalysis.newspaper == newspaper,
            NewspaperAnalysis.date == analysis_date,
        )
        .first()
    )
    if existing:
        existing.category_scores = json.dumps(category_scores)
        existing.overall_rating = overall_rating
        existing.evidence = json.dumps(evidence)
        existing.source_filename = source_filename
        existing.updated_at = datetime.utcnow()
        existing.processing_timestamp = datetime.utcnow()
        db.commit()
        db.refresh(existing)
        return existing
    else:
        record = NewspaperAnalysis(
            newspaper=newspaper,
            date=analysis_date,
            category_scores=json.dumps(category_scores),
            overall_rating=overall_rating,
            evidence=json.dumps(evidence),
            source_filename=source_filename,
        )
        db.add(record)
        db.commit()
        db.refresh(record)
        return record


def get_all_analyses(db: Session) -> List[NewspaperAnalysis]:
    return db.query(NewspaperAnalysis).order_by(NewspaperAnalysis.date.desc()).all()


def get_analysis_by_id(db: Session, analysis_id: int) -> Optional[NewspaperAnalysis]:
    return db.query(NewspaperAnalysis).filter(NewspaperAnalysis.id == analysis_id).first()


def get_analyses_by_date(db: Session, analysis_date: date) -> List[NewspaperAnalysis]:
    return (
        db.query(NewspaperAnalysis)
        .filter(NewspaperAnalysis.date == analysis_date)
        .all()
    )


def get_historical_data(db: Session) -> List[Dict]:
    """Return all records formatted for the trend chart."""
    records = db.query(NewspaperAnalysis).order_by(NewspaperAnalysis.date).all()
    return [
        {
            "id": r.id,
            "newspaper": r.newspaper,
            "date": r.date.isoformat(),
            "overall_rating": r.overall_rating,
            "category_scores": json.loads(r.category_scores),
        }
        for r in records
    ]
