"""Abstract model profile — adding a new Runway model = subclass this."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ModelProfile(ABC):
    """Describes a Runway video generation model and how to construct its task body."""

    task_type: str
    display_name: str
    max_prompt_chars: int
    durations: tuple[int, ...]
    aspect_ratios: tuple[str, ...]
    resolutions: tuple[str, ...]
    supports_audio: bool = True
    extra_defaults: dict[str, Any] = field(default_factory=dict)

    @abstractmethod
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
        """Build the `options` dict for POST /v1/tasks for this model."""

    def validate_prompt(self, prompt: str) -> None:
        if len(prompt) > self.max_prompt_chars:
            raise ValueError(
                f"Prompt is {len(prompt)} chars, "
                f"{self.display_name} allows max {self.max_prompt_chars}."
            )

    def validate_duration(self, duration: int) -> None:
        if duration not in self.durations:
            raise ValueError(
                f"{self.display_name} supports durations {self.durations}, got {duration}."
            )

    def validate_aspect_ratio(self, aspect_ratio: str) -> None:
        if aspect_ratio not in self.aspect_ratios:
            raise ValueError(
                f"{self.display_name} supports aspect ratios {self.aspect_ratios}, "
                f"got {aspect_ratio!r}."
            )


# Registry — UI auto-discovers models from here
_REGISTRY: dict[str, ModelProfile] = {}


def register(profile: ModelProfile) -> ModelProfile:
    _REGISTRY[profile.task_type] = profile
    return profile


def all_profiles() -> list[ModelProfile]:
    return list(_REGISTRY.values())


def by_task_type(task_type: str) -> ModelProfile:
    if task_type not in _REGISTRY:
        raise KeyError(f"Unknown taskType: {task_type!r}. Known: {list(_REGISTRY)}")
    return _REGISTRY[task_type]
