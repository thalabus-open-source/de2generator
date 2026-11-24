"""Translation service module for de²generator."""

from .translator import TranslationService, LANG_CODE_TO_NAME
from .merging import merge_overlapping_translations, merged_translation
from .applier import apply_translations

__all__ = [
    "TranslationService",
    "LANG_CODE_TO_NAME",
    "merge_overlapping_translations",
    "merged_translation",
    "apply_translations",
]
