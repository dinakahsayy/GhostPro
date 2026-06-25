# app/models/database.py
# SQLAlchemy 2.x models for GhostPro.
#
# Schema follows §6 of the SDLC plan. Tables are owned by Alembic
# (see migrations/) — there is intentionally no create_all() here.
# Build/upgrade the dev database with: alembic upgrade head

import os
from datetime import datetime
from uuid import uuid4

from sqlalchemy import (
    JSON, Boolean, Column, DateTime, Float, ForeignKey, Integer, String, Text,
    create_engine,
)
from flask_login import UserMixin
from sqlalchemy.orm import DeclarativeBase, relationship, scoped_session, sessionmaker

from ..utils.crypto import EncryptedString


class Base(DeclarativeBase):
    pass


def _uuid():
    return str(uuid4())


# ---------------------------------------------------------------------------
# §6.1 Users
# ---------------------------------------------------------------------------
class User(Base, UserMixin):
    __tablename__ = 'users'

    id = Column(String(36), primary_key=True, default=_uuid)
    email = Column(String(255), unique=True, nullable=False)
    name = Column(String(255))
    linkedin_id = Column(String(255))  # LinkedIn member URN

    # Professional profile — auto-filled from LinkedIn where possible, otherwise
    # confirmed/entered during onboarding (§3.1/§3.2). title + industry feed the
    # post-generation system prompt (§8.2).
    headline = Column(String(255))
    title = Column(String(255))                  # current role, e.g. "VP of Sales"
    company = Column(String(255))
    industry = Column(String(255))
    bio = Column(Text)                            # LinkedIn about/summary
    audience_description = Column(Text)           # how they describe their LinkedIn audience
    age_range = Column(String(20))               # optional, helps calibrate tone

    # Fernet-encrypted at rest via EncryptedString; ORM code reads/writes plaintext.
    linkedin_access_token = Column(EncryptedString)
    linkedin_refresh_token = Column(EncryptedString)
    token_expires_at = Column(DateTime, nullable=True)

    posting_mode = Column(String(20), default='manual_approval')      # auto_post | manual_approval
    post_frequency = Column(String(20), default='weekly')             # daily|twice_weekly|weekly|biweekly|custom
    preferred_days = Column(JSON)                                     # array of weekday strings
    preferred_time = Column(String(5))                               # HH:MM
    timezone = Column(String(64), default='UTC')                     # IANA tz, e.g. America/New_York

    notification_email = Column(Boolean, default=True)
    notification_inapp = Column(Boolean, default=True)
    onboarding_complete = Column(Boolean, default=False)

    created_at = Column(DateTime, default=datetime.utcnow)
    deleted_at = Column(DateTime, nullable=True)                      # soft delete (GDPR grace period)

    style_profile = relationship(
        'StyleProfile', back_populates='user', uselist=False,
        cascade='all, delete-orphan',
    )
    inbox_items = relationship('ContentInbox', back_populates='user', cascade='all, delete-orphan')
    followed_sources = relationship('FollowedSource', back_populates='user', cascade='all, delete-orphan')
    posts = relationship('Post', back_populates='user', cascade='all, delete-orphan')
    scheduled_jobs = relationship('ScheduledJob', back_populates='user', cascade='all, delete-orphan')

    @property
    def is_active(self):
        """Flask-Login: soft-deleted users cannot log in."""
        return self.deleted_at is None

    def __repr__(self):
        return f"<User(id={self.id}, email={self.email})>"


# ---------------------------------------------------------------------------
# §6.2 Style Profiles
# ---------------------------------------------------------------------------
class StyleProfile(Base):
    __tablename__ = 'style_profiles'

    id = Column(Integer, primary_key=True)
    user_id = Column(String(36), ForeignKey('users.id', ondelete='CASCADE'), nullable=False)
    tone = Column(String(100))
    avg_post_length = Column(Integer)        # words
    emoji_usage = Column(Integer)            # 1-5 scale
    hashtag_count = Column(Float)            # average hashtags per post
    top_topics = Column(JSON)               # array of topic strings
    avoid_topics = Column(JSON)             # array of topics to exclude
    preferred_length = Column(String(10))   # short | medium | long
    content_goal = Column(Text)             # what the user wants their presence to communicate (§3.2)
    raw_style_summary = Column(Text)        # GPT-generated prose used in system prompts
    sample_posts_analyzed = Column(Integer, default=0)
    last_updated = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    user = relationship('User', back_populates='style_profile')

    def __repr__(self):
        return f"<StyleProfile(id={self.id}, user_id={self.user_id})>"


# ---------------------------------------------------------------------------
# §6.3 Content Inbox
# ---------------------------------------------------------------------------
class ContentInbox(Base):
    __tablename__ = 'content_inbox'

    id = Column(Integer, primary_key=True)
    user_id = Column(String(36), ForeignKey('users.id', ondelete='CASCADE'), nullable=False)
    content_type = Column(String(20), nullable=False)   # text_note|url|quote_stat|company_update|suggested
    raw_content = Column(Text, nullable=False)
    parsed_content = Column(Text)                       # fetched/summarized content when content_type is url
    context_note = Column(Text)                         # optional angle/audience emphasis
    priority = Column(String(15), default='use_whenever')  # post_soon | use_whenever
    status = Column(String(20), default='pending')      # pending|in_progress|used|skipped|deleted
    source_label = Column(String(255))                  # short label shown in notifications
    suggested_by = Column(String(500))                  # source url/name if auto-discovered (else null)
    # Logical FK to posts.id. Left unconstrained to avoid a circular FK with
    # posts.inbox_item_id (set once the generated post is published).
    used_in_post_id = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    used_at = Column(DateTime, nullable=True)

    user = relationship('User', back_populates='inbox_items')

    def __repr__(self):
        return f"<ContentInbox(id={self.id}, type={self.content_type}, status={self.status})>"


# ---------------------------------------------------------------------------
# §6.4 Followed Sources
# ---------------------------------------------------------------------------
class FollowedSource(Base):
    __tablename__ = 'followed_sources'

    id = Column(Integer, primary_key=True)
    user_id = Column(String(36), ForeignKey('users.id', ondelete='CASCADE'), nullable=False)
    source_type = Column(String(20), nullable=False)    # linkedin_page | rss_feed | website
    source_url = Column(String(1000), nullable=False)
    source_name = Column(String(255))
    last_checked_at = Column(DateTime, nullable=True)
    active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    user = relationship('User', back_populates='followed_sources')

    def __repr__(self):
        return f"<FollowedSource(id={self.id}, type={self.source_type})>"


# ---------------------------------------------------------------------------
# §6.5 Posts
# ---------------------------------------------------------------------------
class Post(Base):
    __tablename__ = 'posts'

    id = Column(Integer, primary_key=True)
    user_id = Column(String(36), ForeignKey('users.id', ondelete='CASCADE'), nullable=False)
    content = Column(Text, nullable=False)
    version = Column(Integer, default=1)                            # 1 = original, 2+ = regeneration
    parent_post_id = Column(Integer, ForeignKey('posts.id'), nullable=True)  # version grouping
    status = Column(String(20), default='draft')                   # draft|scheduled|approved|published|discarded|error
    source_type = Column(String(20))                               # content_inbox|user_topic|news_api|seasonal
    inbox_item_id = Column(Integer, ForeignKey('content_inbox.id'), nullable=True)
    scheduled_at = Column(DateTime, nullable=True)
    published_at = Column(DateTime, nullable=True)
    posted_at = Column(DateTime, nullable=True)                    # display alias for post history
    linkedin_post_id = Column(String(255), nullable=True)
    source_topic = Column(Text)                                    # topic string or article URL used as inspiration
    generation_prompt = Column(Text)                               # purged after 90 days
    likes_count = Column(Integer, default=0)
    comments_count = Column(Integer, default=0)
    shares_count = Column(Integer, default=0)
    notification_sent_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    user = relationship('User', back_populates='posts')
    parent_post = relationship('Post', remote_side=[id], backref='versions')
    inbox_item = relationship('ContentInbox', foreign_keys=[inbox_item_id])

    def __repr__(self):
        return f"<Post(id={self.id}, status={self.status}, source={self.source_type})>"


# ---------------------------------------------------------------------------
# §6.6 Scheduled Jobs
# ---------------------------------------------------------------------------
class ScheduledJob(Base):
    __tablename__ = 'scheduled_jobs'

    id = Column(Integer, primary_key=True)
    user_id = Column(String(36), ForeignKey('users.id', ondelete='CASCADE'), nullable=False)
    job_id = Column(String(255))               # APScheduler / Celery job identifier
    next_run_at = Column(DateTime, nullable=True)
    last_run_at = Column(DateTime, nullable=True)
    status = Column(String(20), default='active')  # active | paused | error
    retry_count = Column(Integer, default=0)       # resets to 0 on success

    user = relationship('User', back_populates='scheduled_jobs')

    def __repr__(self):
        return f"<ScheduledJob(id={self.id}, status={self.status})>"


# ---------------------------------------------------------------------------
# Legacy prototype table.
# Retained so the current company-name generation routes keep working until
# they are rewritten against User/Post in the onboarding+auth PR. Slated for
# removal then.
# ---------------------------------------------------------------------------
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


DATABASE_URL = os.getenv('DATABASE_URL', 'sqlite:///ghostpro.db')
engine = create_engine(DATABASE_URL, future=True)

# Session() -> a fresh, independent session for discrete `with Session() as s:` blocks.
Session = sessionmaker(bind=engine, future=True)

# Request-scoped session registry. Used for anything tied to the request lifecycle
# (Flask-Login's current_user and routes that read its relationships). Removed in
# create_app()'s teardown_appcontext so each request gets a clean session.
db_session = scoped_session(Session)
