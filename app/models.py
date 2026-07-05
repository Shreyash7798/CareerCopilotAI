"""Database models: Jobs, Companies, Recruiters, Applications, Resumes,
User Profile, Settings and Activity Logs (spec section 10)."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ATS platforms the discovery pipeline can monitor. 'oracle', 'sap' and
# 'taleo' are handled through the generic careers-page connector with
# platform-appropriate defaults (see app/company_sources.py).
ATS_TYPES = [
    "greenhouse",
    "lever",
    "workday",
    "smartrecruiters",
    "sap",
    "oracle",
    "taleo",
    "careers_page",
]

COMPANY_PRIORITIES = ["high", "normal", "low"]


class Company(Base):
    """A company is both an employer-intelligence record (auto-created when
    jobs are discovered) and, when `ats_type` is set, a discovery source that
    the pipeline polls. This replaces routine editing of sources.yaml."""

    __tablename__ = "companies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    website: Mapped[str | None] = mapped_column(String(512), nullable=True)
    industry: Mapped[str | None] = mapped_column(String(255), nullable=True)
    locations: Mapped[str | None] = mapped_column(String(512), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_preferred: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    # --- discovery-source configuration (Company Management MVP) ---
    ats_type: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    career_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    # JSON: ATS-specific params (board, host/tenant/site, link_selector, render…)
    ats_config: Mapped[str | None] = mapped_column(Text, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    refresh_interval_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    priority: Mapped[str] = mapped_column(String(16), default="normal")
    country: Mapped[str | None] = mapped_column(String(64), nullable=True)
    sector: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # Comma-separated; when set, only job titles containing one of these terms
    # are kept for this company.
    keywords: Mapped[str | None] = mapped_column(Text, nullable=True)
    recruiter_search_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_run_status: Mapped[str | None] = mapped_column(String(255), nullable=True)

    jobs: Mapped[list["Job"]] = relationship(back_populates="company")
    recruiters: Mapped[list["Recruiter"]] = relationship(back_populates="company")

    @property
    def is_source(self) -> bool:
        return bool(self.ats_type)


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (UniqueConstraint("dedup_key", name="uq_jobs_dedup_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    company_id: Mapped[int | None] = mapped_column(ForeignKey("companies.id"), nullable=True)
    title: Mapped[str] = mapped_column(String(512), index=True)
    location: Mapped[str | None] = mapped_column(String(512), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    source: Mapped[str | None] = mapped_column(String(64), nullable=True)
    external_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    dedup_key: Mapped[str] = mapped_column(String(64), index=True)
    posted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    discovered_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    match_score: Mapped[float] = mapped_column(Float, default=0.0, index=True)
    score_breakdown: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON
    is_high_priority: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    notified_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    company: Mapped[Company | None] = relationship(back_populates="jobs")
    applications: Mapped[list["Application"]] = relationship(back_populates="job")


class Recruiter(Base):
    __tablename__ = "recruiters"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    company_id: Mapped[int | None] = mapped_column(ForeignKey("companies.id"), nullable=True)
    name: Mapped[str] = mapped_column(String(255))
    department: Mapped[str | None] = mapped_column(String(255), nullable=True)
    linkedin_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    public_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    related_requisitions: Mapped[str | None] = mapped_column(Text, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    company: Mapped[Company | None] = relationship(back_populates="recruiters")


APPLICATION_STATUSES = [
    "Planned",
    "Applied",
    "Screening",
    "Interviewing",
    "Offer",
    "Rejected",
    "Withdrawn",
]


class Application(Base):
    __tablename__ = "applications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[int | None] = mapped_column(ForeignKey("jobs.id"), nullable=True)
    company_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    role: Mapped[str | None] = mapped_column(String(512), nullable=True)
    date_applied: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="Planned", index=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    follow_up_date: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    interview_stages: Mapped[str | None] = mapped_column(Text, nullable=True)
    outcome: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    job: Mapped[Job | None] = relationship(back_populates="applications")


class Resume(Base):
    __tablename__ = "resumes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[int | None] = mapped_column(ForeignKey("jobs.id"), nullable=True)
    # master | tailored | cover_letter
    kind: Mapped[str] = mapped_column(String(32), default="tailored")
    file_path: Mapped[str] = mapped_column(String(1024))
    pdf_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    matched_keywords: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class InterviewPrep(Base):
    __tablename__ = "interview_preps"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id"), index=True)
    content_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class UserProfile(Base):
    __tablename__ = "user_profile"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    full_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(64), nullable=True)
    cv_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    profile_json: Mapped[str | None] = mapped_column(Text, nullable=True)  # parsed CV data
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class Setting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(255), primary_key=True)
    value: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class ActivityLog(Base):
    __tablename__ = "activity_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    category: Mapped[str] = mapped_column(String(64), index=True)  # discovery|scoring|notify|export|resume|app
    message: Mapped[str] = mapped_column(Text)
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)


def log_activity(session, category: str, message: str, detail: str | None = None) -> None:
    session.add(ActivityLog(category=category, message=message, detail=detail))
