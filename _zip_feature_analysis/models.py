from __future__ import annotations
import os
from datetime import datetime
from sqlalchemy import create_engine, Column, Integer, String, Float, Boolean, Text, DateTime
from sqlalchemy.orm import declarative_base, sessionmaker

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:////app/library.db")

if DATABASE_URL.startswith("sqlite"):
    engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
else:
    engine = create_engine(DATABASE_URL, pool_recycle=3600)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

class FileRecord(Base):
    __tablename__ = "file_records"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    name = Column(String(255), nullable=False)
    path = Column(String(1024), nullable=False, unique=True)
    file_hash = Column(String(64), nullable=True, index=True)
    extension = Column(String(10), nullable=False)
    size_mb = Column(Float, nullable=False)
    mtime = Column(String(50), nullable=False)
    is_zip = Column(Boolean, nullable=False, default=False)

    writer = Column(String(255), nullable=True, index=True)
    episode_count = Column(Integer, nullable=True, default=0)
    is_complete = Column(Boolean, nullable=False, default=False)
    tags = Column(Text, nullable=True, default="[]")
    thumbnail = Column(String(255), nullable=True)
    is_lock = Column(Boolean, nullable=False, default=False)

    is_favorite = Column(Boolean, nullable=False, default=False)
    series_name = Column(String(255), nullable=True, index=True)
    user_rating = Column(Integer, nullable=True, default=0)

    # ✨ 빼먹었던 핵심 속성 추가: 유저 코멘트 및 읽음 상태
    user_comment = Column(Text, nullable=True)
    is_read = Column(Boolean, nullable=False, default=False)

class ProcessLog(Base):
    __tablename__ = "process_logs"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    timestamp = Column(DateTime, default=datetime.now)
    level = Column(String(20), default="INFO")
    message = Column(Text, nullable=False)