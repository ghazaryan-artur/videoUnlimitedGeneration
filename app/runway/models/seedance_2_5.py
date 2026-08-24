"""Seedance 2.5 — taskType=seedance_2_5.

Option shape confirmed against the app.runwayml.com bundle
(`createTaskOptions` for model id `seedance-2-5`):

    {name, textPrompt, duration, aspectRatio, resolution,
     generateAudio, exploreMode, creationSource, assetGroupId}

Differences from Seedance 2.0:
  - much longer prompt budget (15 000 chars)
  - per-second durations (the model itself goes 4…30s, but exploreMode
    filters out anything above 15s — that is the free/unlimited cap)
  - extra aspect ratios: 21:9, 4:3, 3:4
  - 1080p is credits-only; in exploreMode Runway silently rewrites it
    to 720p, so we clamp to 720p ourselves.
  - `output_format` is omitted on purpose — Runway defaults to mp4 and
    only sends the field for "mov", which our downloader doesn't want.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .base import ModelProfile, register


@dataclass(frozen=True)
class Seedance25(ModelProfile):
    def build_options(
        self,
        *,
        name: str,
        prompt: str,
        duration: int,
        aspect_ratio: str,
        resolution: str,
        audio: bool,
        asset_group_id: str,
    ) -> dict[str, Any]:
        # exploreMode has no access to 1080p — anything unknown falls to 720p.
        api_resolution = resolution if resolution in self.resolutions else "720p"
        return {
            "name": name,
            "textPrompt": prompt,
            "duration": duration,
            "aspectRatio": aspect_ratio,
            "resolution": api_resolution,
            "generateAudio": audio,
            "exploreMode": True,
            "creationSource": "tool-mode",
            "assetGroupId": asset_group_id,
        }


SEEDANCE_2_5 = register(Seedance25(
    task_type="seedance_2_5",
    display_name="Seedance 2.5",
    max_prompt_chars=15000,
    # 4…15s — exploreMode (unlimited) drops every option above 15s.
    durations=tuple(range(4, 16)),
    aspect_ratios=("16:9", "9:16", "1:1", "21:9", "4:3", "3:4"),
    resolutions=("720p", "480p"),
    supports_audio=True,
))
