"""Kling O3 4K — taskType=kling_o3_4k.

Same shape as Kling 3.0 4K (mode=pro, 4K W×H resolution, providerSettings.sound)
but WITHOUT the cfgScale field.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .base import ModelProfile, register


# 4K resolution map (W x H), constant ~8.3 MP budget across ratios.
_KLING_O3_4K_RESOLUTIONS: dict[str, str] = {
    "16:9": "3840x2160",
    "9:16": "2160x3840",
    "1:1":  "2880x2880",
}


@dataclass(frozen=True)
class KlingO34K(ModelProfile):
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
        api_resolution = _KLING_O3_4K_RESOLUTIONS.get(aspect_ratio, "2160x3840")
        return {
            "name": name,
            "mode": "pro",
            "textPrompt": prompt,
            "duration": duration,
            "resolution": api_resolution,
            "providerSettings": {"sound": audio},
            "exploreMode": True,
            "creationSource": "tool-mode",
            "assetGroupId": asset_group_id,
        }


KLING_O3_4K = register(KlingO34K(
    task_type="kling_o3_4k",
    display_name="Kling O3 4K",
    max_prompt_chars=2500,
    durations=(3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15),
    aspect_ratios=("16:9", "9:16", "1:1"),
    resolutions=("2160p",),
    supports_audio=True,
))
