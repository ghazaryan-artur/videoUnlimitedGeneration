"""Kling 3.0 Pro — taskType=kling_3_0_pro."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .base import ModelProfile, register


# Kling encodes aspect ratio in the resolution dimensions (W x H).
# These map UI choice → API resolution string.
_KLING_RESOLUTIONS: dict[str, str] = {
    "16:9": "1920x1080",
    "9:16": "1080x1920",
    "1:1":  "1080x1080",
}


@dataclass(frozen=True)
class Kling3Pro(ModelProfile):
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
        # `resolution` arg here is logical (e.g. "1080p"); we map ar→W×H.
        api_resolution = _KLING_RESOLUTIONS.get(aspect_ratio, "1080x1920")
        return {
            "name": name,
            "mode": "pro",
            "textPrompt": prompt,
            "duration": duration,
            "resolution": api_resolution,
            "cfgScale": 0.5,
            "providerSettings": {"sound": audio},
            "exploreMode": True,
            "creationSource": "tool-mode",
            "assetGroupId": asset_group_id,
        }


KLING_3_PRO = register(Kling3Pro(
    task_type="kling_3_0_pro",
    display_name="Kling 3.0 Pro",
    max_prompt_chars=2500,
    durations=(5, 10, 15),
    aspect_ratios=("16:9", "9:16", "1:1"),
    resolutions=("1080p",),
    supports_audio=True,
))
