"""Import prompts from CSV / TXT files into PromptDraft objects.

CSV format (header row required):
    prompt, model, duration, aspect, resolution, audio, count, output_dir, name

  - Only `prompt` is mandatory.
  - `model` accepts task_type or display_name (e.g. "seedance_2" or "Seedance 2.0").
  - `audio` parses true/false/yes/no/1/0.
  - Missing or blank cells fall back to defaults.

TXT format:
  - One prompt per non-empty, non-comment line. Lines starting with `#` ignored.
  - All other params take defaults.

Example CSV:
    prompt,model,duration,aspect,count
    "A cat on the beach",seedance_2,5,9:16,3
    "Old typewriter on a desk",kling_3_0_pro,10,16:9,1

Example TXT:
    # nature scenes
    A peaceful lake at dawn
    A forest after rain
"""
from __future__ import annotations

import csv
from dataclasses import dataclass
from io import StringIO
from pathlib import Path

from app.core.job import PromptDraft
from app.runway.models import all_profiles, by_task_type


# Map between display name and task_type — accept either in the `model` column
def _model_lookup(value: str | None) -> str | None:
    if not value:
        return None
    v = value.strip()
    for profile in all_profiles():
        if v == profile.task_type or v.lower() == profile.display_name.lower():
            return profile.task_type
    return None


_TRUE = {"true", "yes", "y", "1", "on"}
_FALSE = {"false", "no", "n", "0", "off"}


def _parse_bool(s: str | None, default: bool = True) -> bool:
    if s is None or not s.strip():
        return default
    v = s.strip().lower()
    if v in _TRUE:
        return True
    if v in _FALSE:
        return False
    return default


def _parse_int(s: str | None, default: int) -> int:
    try:
        return int(str(s).strip()) if s else default
    except (TypeError, ValueError):
        return default


@dataclass
class ImportResult:
    drafts: list[PromptDraft]
    errors: list[str]   # one entry per skipped row
    source: str         # path or "csv-text" / "txt-text"


def import_csv(text: str, *, source: str = "csv") -> ImportResult:
    drafts: list[PromptDraft] = []
    errors: list[str] = []
    reader = csv.DictReader(StringIO(text))
    if reader.fieldnames is None:
        return ImportResult(drafts=[], errors=["CSV is empty or has no header"], source=source)
    fields_lower = {(f or "").strip().lower(): f for f in reader.fieldnames}

    if "prompt" not in fields_lower:
        return ImportResult(
            drafts=[],
            errors=["CSV must have a 'prompt' column"],
            source=source,
        )

    def get(row: dict, name: str) -> str | None:
        col = fields_lower.get(name.lower())
        if col is None:
            return None
        v = row.get(col)
        return v.strip() if isinstance(v, str) else None

    for i, row in enumerate(reader, start=2):  # row 1 = header
        prompt = get(row, "prompt") or ""
        if not prompt:
            errors.append(f"row {i}: empty prompt — skipped")
            continue

        model_value = _model_lookup(get(row, "model"))
        if model_value is None:
            model_value = "seedance_2"  # default

        try:
            profile = by_task_type(model_value)
        except KeyError:
            errors.append(f"row {i}: unknown model {model_value!r} — using seedance_2")
            profile = by_task_type("seedance_2")
            model_value = profile.task_type

        duration = _parse_int(get(row, "duration"), profile.durations[0])
        if duration not in profile.durations:
            errors.append(f"row {i}: duration {duration} not allowed for {profile.display_name} — clamped to {profile.durations[0]}")
            duration = profile.durations[0]

        aspect = (get(row, "aspect") or get(row, "aspect_ratio") or profile.aspect_ratios[0]).strip()
        if aspect not in profile.aspect_ratios:
            errors.append(f"row {i}: aspect {aspect!r} not allowed for {profile.display_name} — using {profile.aspect_ratios[0]}")
            aspect = profile.aspect_ratios[0]

        resolution = (get(row, "resolution") or profile.resolutions[0]).strip()

        audio = _parse_bool(get(row, "audio"), True)
        count = max(1, min(50, _parse_int(get(row, "count"), 1)))
        output_dir_value = get(row, "output_dir") or get(row, "output") or None
        name = get(row, "name") or None

        drafts.append(PromptDraft(
            prompt=prompt,
            model_task_type=model_value,
            duration=duration,
            aspect_ratio=aspect,
            resolution=resolution,
            audio=audio,
            count=count,
            output_dir=output_dir_value,
            name=name,
        ))

    return ImportResult(drafts=drafts, errors=errors, source=source)


def import_txt(text: str, *, source: str = "txt") -> ImportResult:
    drafts: list[PromptDraft] = []
    errors: list[str] = []
    for i, line in enumerate(text.splitlines(), start=1):
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        drafts.append(PromptDraft(prompt=s))
    return ImportResult(drafts=drafts, errors=errors, source=source)


def import_file(path: Path) -> ImportResult:
    text = path.read_text(encoding="utf-8-sig")  # tolerate BOM
    suffix = path.suffix.lower()
    if suffix == ".csv":
        return import_csv(text, source=str(path))
    if suffix in (".txt", ".text", ".md"):
        return import_txt(text, source=str(path))
    # Auto-detect: if first line looks like CSV header (contains "prompt" + comma)
    first_line = text.split("\n", 1)[0].lower()
    if "prompt" in first_line and "," in first_line:
        return import_csv(text, source=str(path))
    return import_txt(text, source=str(path))
