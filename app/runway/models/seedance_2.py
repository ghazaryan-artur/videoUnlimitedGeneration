"""Seedance 2.0 — taskType=seedance_2."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .base import ModelProfile, register


@dataclass(frozen=True)
class Seedance2(ModelProfile):
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
            "textPrompt": prompt,
            "duration": duration,
            "aspectRatio": aspect_ratio,
            "resolution": resolution,
            "generateAudio": audio,
            "exploreMode": True,
            "creationSource": "tool-mode",
            "assetGroupId": asset_group_id,
        }


SEEDANCE_2 = register(Seedance2(
    task_type="seedance_2",
    display_name="Seedance 2.0",
    max_prompt_chars=3500,
    durations=(5, 10, 15),
    aspect_ratios=("16:9", "9:16", "1:1"),
    resolutions=("720p",),
    supports_audio=True,
))
