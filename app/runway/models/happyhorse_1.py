"""HappyHorse 1.0 — taskType=happyhorse_1_0.

Differs from Seedance/Kling in option shape:
  - field is `ratio` (not `aspectRatio`)
  - resolution is `"1080P"` (capital P)
  - explicit `route: "t2v"` and `model` echoed in options
  - no audio control
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .base import ModelProfile, register


@dataclass(frozen=True)
class HappyHorse1(ModelProfile):
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
        return {
            "name": name,
            "model": "happyhorse_1_0",
            "textPrompt": prompt,
            "duration": duration,
            "resolution": "1080P",
            "ratio": aspect_ratio,
            "exploreMode": True,
            "route": "t2v",
            "creationSource": "tool-mode",
            "assetGroupId": asset_group_id,
        }


HAPPYHORSE_1 = register(HappyHorse1(
    task_type="happyhorse_1_0",
    display_name="HappyHorse 1.0",
    max_prompt_chars=3500,
    durations=tuple(range(3, 16)),
    aspect_ratios=("16:9", "9:16", "1:1", "3:4", "4:3"),
    resolutions=("1080p",),
    supports_audio=False,
))
