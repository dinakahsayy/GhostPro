# app/models/database.py
# SQLAlchemy 2.x models. Tables created on import for development convenience;
# Phase 1 will introduce Alembic and remove the implicit create_all.

import os
from datetime import datetime

from sqlalchemy import (
    Boolean, Column, DateTime, Float, Integer, String, Text, create_engine,
)
from sqlalchemy.orm import DeclarativeBase, sessionmaker


class Base(DeclarativeBase):
    pass


class LinkedInPost(Base):
    __tablename__ = 'linkedin_posts'

    id = Column(Integer, primary_key=True)
    content = Column(Text, nullable=False)

    company_name = Column(String(255))
    business_type = Column(String(100))
    tone = Column(String(100))

    timestamp = Column(DateTime, default=datetime.utcnow)
    posted = Column(Boolean, default=False)
    posted_at = Column(DateTime, nullable=True)
    scheduled_time = Column(DateTime, nullable=True)

    likes_count = Column(Integer, default=0)
    comments_count = Column(Integer, default=0)
    shares_count = Column(Integer, default=0)
    engagement_rate = Column(Float, default=0.0)

    template_used = Column(String(100), nullable=True)
    category = Column(String(100), nullable=True)

    def __repr__(self):
        return f"<LinkedInPost(id={self.id}, company={self.company_name}, posted={self.posted})>"


class PostTemplate(Base):
    __tablename__ = 'post_templates'

    id = Column(Integer, primary_key=True)
    name = Column(String(255), nullable=False)
    description = Column(Text)
    content = Column(Text, nullable=False)
    category = Column(String(100))
    created_at = Column(DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f"<PostTemplate(id={self.id}, name={self.name})>"


class PostComment(Base):
    __tablename__ = 'post_comments'

    id = Column(Integer, primary_key=True)
    post_id = Column(Integer)
    comment_text = Column(Text, nullable=False)
    reply_text = Column(Text)
    timestamp = Column(DateTime, default=datetime.utcnow)
    replied = Column(Boolean, default=False)
    sentiment = Column(String(50), nullable=True)

    def __repr__(self):
        return f"<PostComment(id={self.id}, post_id={self.post_id}, replied={self.replied})>"


DATABASE_URL = os.getenv('DATABASE_URL', 'sqlite:///ghostpro.db')
engine = create_engine(DATABASE_URL, future=True)
Base.metadata.create_all(engine)
Session = sessionmaker(bind=engine, future=True)
