"""Detection modules for de²generator."""

from .entropy import EntropyDetector, calculate_shannon_entropy
from .repetition import RepetitionDetector
from .semantic import SemanticProgressDetector, SemanticLLMAvailabilityTracker
from .content_type import detect_content_type, HTML_CHECKPOINT_TAGS, RE_CLOSING_TAG

__all__ = [
    "EntropyDetector",
    "calculate_shannon_entropy",
    "RepetitionDetector",
    "SemanticProgressDetector",
    "SemanticLLMAvailabilityTracker",
    "detect_content_type",
    "HTML_CHECKPOINT_TAGS",
    "RE_CLOSING_TAG",
]