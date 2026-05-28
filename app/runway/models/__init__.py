"""Runway model profiles. Importing this package registers all known models."""
from .base import ModelProfile, all_profiles, by_task_type, register
from .seedance_2 import SEEDANCE_2
from .kling_3_pro import KLING_3_PRO
from .kling_3_4k import KLING_3_4K
from .kling_o3_4k import KLING_O3_4K
from .happyhorse_1 import HAPPYHORSE_1

__all__ = [
    "ModelProfile",
    "all_profiles",
    "by_task_type",
    "register",
    "SEEDANCE_2",
    "KLING_3_PRO",
    "KLING_3_4K",
    "KLING_O3_4K",
    "HAPPYHORSE_1",
]
