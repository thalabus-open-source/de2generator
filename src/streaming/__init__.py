"""Streaming modules for de²generator."""

from .vllm_client import stream_from_vllm

__all__ = [
    "stream_from_vllm",
]