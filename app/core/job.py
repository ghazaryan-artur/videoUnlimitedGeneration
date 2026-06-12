"""Job — one row in the batch table; the unit the queue moves around."""
from __future__ import annotations

import re
import secrets
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


_WORD_RE = re.compile(r"\w+", re.UNICODE)


def _first_words(text: str, n: int = 5) -> str:
    matches = _WORD_RE.findall(text or "")
    return " ".join(matches[:n])


def safe_folder_name(s: str, *, max_len: int = 80) -> str:
    """Sanitise a string for use as a folder name on any filesystem.

    Keeps letters/digits/spaces/dashes/dots/underscores, collapses runs of
    whitespace, length-caps to max_len. Returns "untitled" if nothing left.
    """
    cleaned = re.sub(r"[^\w\s.\-]", "", s or "", flags=re.UNICODE).strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    if len(cleaned) > max_len:
        cleaned = cleaned[:max_len].rstrip()
    return cleaned or "untitled"


class JobStatus(str, Enum):
    """Lifecycle states. Terminal: DONE, FAILED, CANCELLED."""

    PENDING = "PENDING"          # in our local queue, not yet submitted
    SUBMITTING = "SUBMITTING"    # POST /v1/tasks in flight
    QUEUED = "QUEUED"            # accepted by Runway, awaiting capacity (THROTTLED)
    GENERATING = "GENERATING"    # Runway: RUNNING
    DOWNLOADING = "DOWNLOADING"  # have URL, streaming bytes
    DONE = "DONE"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"

    @property
    def is_terminal(self) -> bool:
        return self in (JobStatus.DONE, JobStatus.FAILED, JobStatus.CANCELLED)

    @property
    def is_active(self) -> bool:
        return self in (JobStatus.PENDING, JobStatus.SUBMITTING, JobStatus.QUEUED,
                        JobStatus.GENERATING, JobStatus.DOWNLOADING)


def _new_id() -> str:
    return secrets.token_hex(8)


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Job(BaseModel):
    """A single video to generate. Persisted to SQLite, sent to UI via Flet."""

    # Identity
    id: str = Field(default_factory=_new_id)
    batch_id: str = ""

    # Inputs (set by user, locked at submit time)
    model_task_type: str
    prompt: str
    duration: int
    aspect_ratio: str
    resolution: str
    audio: bool
    name: str | None = None
    output_dir: str | None = None  # destination folder; None → app default
    # Author who started this job — drives Completed/Downloads filtering.
    # None means a legacy job from before authors existed.
    author: str | None = None

    # Server-side identifiers (set after submission)
    runway_task_id: str | None = None
    runway_session_id: str | None = None
    runway_asset_group_id: str | None = None

    # Live state
    status: JobStatus = JobStatus.PENDING
    progress_ratio: float = 0.0
    estimated_start_seconds: int | None = None
    error_reason: str | None = None

    # Position in the local pre-submit queue. Lower = picked up sooner.
    # Assigned monotonically by BatchRunner.add_jobs() for fresh jobs;
    # mutated by the user via the ▲/▼ buttons on PENDING cards.
    queue_order: int = 0

    # Output
    output_path: str | None = None
    file_size_bytes: int | None = None
    download_bytes_done: int = 0

    # Timestamps
    created_at: datetime = Field(default_factory=_now)
    submitted_at: datetime | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    downloaded_at: datetime | None = None

    # ── derived helpers ──────────────────────────────────────────────

    @property
    def queue_seconds(self) -> float | None:
        if self.submitted_at and self.started_at:
            return (self.started_at - self.submitted_at).total_seconds()
        return None

    @property
    def generation_seconds(self) -> float | None:
        if self.started_at and self.completed_at:
            return (self.completed_at - self.started_at).total_seconds()
        return None

    @property
    def total_seconds(self) -> float | None:
        if self.created_at and self.downloaded_at:
            return (self.downloaded_at - self.created_at).total_seconds()
        return None

    @property
    def display_name(self) -> str:
        return self.name or (self.prompt[:60].strip() or "Untitled job")

    # ── transitions (called by the queue runner) ─────────────────────

    def transition_to(self, status: JobStatus, **fields: Any) -> None:
        self.status = status
        for k, v in fields.items():
            setattr(self, k, v)


class Batch(BaseModel):
    """A grouping of jobs submitted together."""

    id: str = Field(default_factory=_new_id)
    name: str = ""
    created_at: datetime = Field(default_factory=_now)
    job_ids: list[str] = Field(default_factory=list)


class PromptDraft(BaseModel):
    """Editable draft in the UI before clicking Start Batch.

    A draft expands into N Jobs (one per video to generate).
    """

    id: str = Field(default_factory=_new_id)

    # Generation params (mirror Job)
    prompt: str = ""
    model_task_type: str = "seedance_2"
    duration: int = 5
    aspect_ratio: str = "9:16"
    resolution: str = "720p"
    audio: bool = True
    name: str | None = None

    # Card-level
    count: int = 1                    # how many videos to generate from this prompt
    output_dir: str | None = None     # per-card destination folder

    def make_name_for(self, index: int) -> str:
        base = (self.name or self.prompt[:60]).strip() or "Untitled"
        if self.count > 1:
            return f"{base} #{index + 1}"
        return base

    def clone(self) -> "PromptDraft":
        """Return an editable copy of this draft with a fresh id.

        Used by the UI "Duplicate" button — same prompt, model, duration,
        aspect, audio, count, output_dir; new id so it's a distinct draft.
        """
        return self.model_copy(update={"id": _new_id()}, deep=True)

    def auto_folder(self) -> str:
        """Folder name to group all videos from this draft under, when the
        user didn't specify one explicitly. Prefers the user-set name,
        falls back to the first 5 words of the prompt."""
        base = (self.name or "").strip()
        if not base:
            base = _first_words(self.prompt, 5)
        return safe_folder_name(base)

    def expand(self, *, author: str | None = None) -> list[Job]:
        """Materialize into one Job per video to generate.

        Web mode only: if the user didn't set output_dir, all videos from
        this draft are grouped into a folder derived from `name` / first 5
        prompt words. Desktop mode behaviour is unchanged (output_dir is
        passed through as-is — None means the default Videos folder).

        If `author` is given, it becomes the FIRST segment of output_dir
        (so Downloads view groups by creator).
        """
        from app.paths import is_web_mode  # local to avoid circular import
        if self.output_dir and self.output_dir.strip():
            folder = self.output_dir
        elif is_web_mode():
            folder = self.auto_folder()
        else:
            folder = None

        if author:
            author_seg = safe_folder_name(author)
            if author_seg:
                folder = f"{author_seg}/{folder}" if folder else author_seg

        jobs: list[Job] = []
        for i in range(max(1, self.count)):
            jobs.append(Job(
                model_task_type=self.model_task_type,
                prompt=self.prompt,
                duration=self.duration,
                aspect_ratio=self.aspect_ratio,
                resolution=self.resolution,
                audio=self.audio,
                name=self.make_name_for(i),
                output_dir=folder,
                author=author or None,
            ))
        return jobs
