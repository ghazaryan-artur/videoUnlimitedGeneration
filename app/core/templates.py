"""Prompt templates — saved presets the user can re-apply when composing new prompts.

Stored as JSON at <user_data>/templates.json.
A template captures the *non-prompt* fields of a PromptDraft:
model, duration, aspect, resolution, audio, count, output_dir, name pattern.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.core.job import PromptDraft
from app.paths import user_data_root


@dataclass
class Template:
    name: str
    model_task_type: str
    duration: int
    aspect_ratio: str
    resolution: str
    audio: bool
    count: int
    output_dir: str | None
    name_pattern: str | None  # default for PromptDraft.name; supports {prompt:.40}
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds"))

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "model_task_type": self.model_task_type,
            "duration": self.duration,
            "aspect_ratio": self.aspect_ratio,
            "resolution": self.resolution,
            "audio": self.audio,
            "count": self.count,
            "output_dir": self.output_dir,
            "name_pattern": self.name_pattern,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Template":
        return cls(
            name=d["name"],
            model_task_type=d["model_task_type"],
            duration=int(d["duration"]),
            aspect_ratio=d["aspect_ratio"],
            resolution=d["resolution"],
            audio=bool(d["audio"]),
            count=int(d.get("count", 1)),
            output_dir=d.get("output_dir"),
            name_pattern=d.get("name_pattern"),
            created_at=d.get("created_at") or datetime.now(timezone.utc).isoformat(timespec="seconds"),
        )

    @classmethod
    def from_draft(cls, name: str, draft: PromptDraft) -> "Template":
        return cls(
            name=name,
            model_task_type=draft.model_task_type,
            duration=draft.duration,
            aspect_ratio=draft.aspect_ratio,
            resolution=draft.resolution,
            audio=draft.audio,
            count=draft.count,
            output_dir=draft.output_dir,
            name_pattern=draft.name,
        )

    def apply_to(self, draft: PromptDraft) -> None:
        """Mutate `draft` in place — apply this template's settings (prompt is preserved)."""
        draft.model_task_type = self.model_task_type
        draft.duration = self.duration
        draft.aspect_ratio = self.aspect_ratio
        draft.resolution = self.resolution
        draft.audio = self.audio
        draft.count = self.count
        draft.output_dir = self.output_dir
        if self.name_pattern is not None:
            draft.name = self.name_pattern


def _file() -> Path:
    return user_data_root() / "templates.json"


def list_templates() -> list[Template]:
    f = _file()
    if not f.exists():
        return []
    try:
        raw = json.loads(f.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    out: list[Template] = []
    for d in raw if isinstance(raw, list) else []:
        try:
            out.append(Template.from_dict(d))
        except (KeyError, ValueError, TypeError):
            continue
    return out


def save_templates(templates: list[Template]) -> None:
    f = _file()
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(
        json.dumps([t.to_dict() for t in templates], indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def add_template(template: Template) -> list[Template]:
    existing = list_templates()
    # replace by name if exists
    existing = [t for t in existing if t.name != template.name]
    existing.append(template)
    save_templates(existing)
    return existing


def delete_template(name: str) -> list[Template]:
    existing = [t for t in list_templates() if t.name != name]
    save_templates(existing)
    return existing
