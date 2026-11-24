"""Core data structures and configuration for de²generator."""

from .models import DetectionResult, Checkpoint, GenerationSession
from .config import (
    load_config,
    get_config_value,
    set_config_value,
    delete_config_value,
    get_config_snapshot,
)
from .endpoints import get_vllm_base_url, set_vllm_base_url, resolve_endpoint
from . import constants

__all__ = [
    "DetectionResult",
    "Checkpoint",
    "GenerationSession",
    "load_config",
    "get_config_value",
    "set_config_value",
    "delete_config_value",
    "get_config_snapshot",
    "get_vllm_base_url",
    "set_vllm_base_url",
    "resolve_endpoint",
    "constants",
]
