"""Kling 3.0 4K — taskType=kling_3_0_4k.

Same option shape as Kling 3.0 Pro (mode=pro, cfgScale, providerSettings.sound)
but renders at 4K. Aspect ratio is encoded in the W×H resolution string.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .base import ModelProfile, register


# 4K equivalents of the Pro resolution map (W x H). Kling keeps a constant
# ~8.3 MP budget across ratios, so 1:1 is 2880x2880 (not 2160x2160).
# All three confirmed against real /v1/tasks payloads.
_KLING_4K_RESOLUTIONS: dict[str, str] = {
    "16:9": "3840x2160",
    "9:16": "2160x3840",
    "1:1":  "2880x2880",
}


@dataclass(frozen=True)
class Kling34K(ModelProfile):
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
        api_resolution = _KLING_4K_RESOLUTIONS.get(aspect_ratio, "2160x3840")
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


KLING_3_4K = register(Kling34K(
    task_type="kling_3_0_4k",
    display_name="Kling 3.0 4K",
    max_prompt_chars=2500,
    durations=(3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15),
    aspect_ratios=("16:9", "9:16", "1:1"),
    resolutions=("2160p",),
    supports_audio=True,
))
