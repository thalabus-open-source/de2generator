"""Assembling per-checkpoint translations back into one answer.

Translations arrive per checkpoint, and a checkpoint can end mid-tag: the
translator then closes the tag, while the next checkpoint starts with that same
closing tag.  Joining the pieces naively duplicates it (``</b></b>``), so the
merge detects a small boundary overlap and removes it.
"""

from typing import Optional


def merge_overlapping_translations(translations: list[str], max_overlap: int = 10) -> str:
    """Join translated checkpoint texts, collapsing duplicated boundary characters.

    Example::

        ["<b> ... a Marseille</b>", "/b> ... "]  ->  "<b> ... a Marseille</b> ... "

    Args:
        translations: translated checkpoint texts, in checkpoint order
        max_overlap: how many trailing/leading characters to compare

    Returns:
        The merged text (empty string for an empty list).
    """
    if not translations:
        return ""
    if len(translations) == 1:
        return translations[0]

    result = translations[0]
    for next_translation in translations[1:]:
        overlap_len = 0
        check_len = min(max_overlap, len(result), len(next_translation))
        # longest first: the most specific match is the one that represents real overlap
        for i in range(check_len, 0, -1):
            if result[-i:] == next_translation[:i]:
                overlap_len = i
                break
        result += next_translation[overlap_len:]

    return result


def content_checkpoint_indices(checkpoints: list) -> list[int]:
    """Indices of content checkpoints (thinking checkpoints are never translated)."""
    return [
        i for i, cp in enumerate(checkpoints)
        if not (getattr(cp, "metrics_snapshot", None) or {}).get("thinking_checkpoint")
    ]


def merged_translation(checkpoints: list, translated: dict, *,
                      max_overlap: int = 10) -> Optional[str]:
    """Merge ``translated`` for every content checkpoint, or ``None`` if incomplete.

    Returning ``None`` means "fall back to the original text": a partial answer
    must never silently replace a complete one.
    """
    indices = content_checkpoint_indices(checkpoints)
    if not indices or not all(i in translated for i in indices):
        return None
    return merge_overlapping_translations([translated[i] for i in indices], max_overlap)
