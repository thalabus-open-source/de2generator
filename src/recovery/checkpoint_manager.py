"""Checkpoint management for recovery."""

from typing import Optional

from core.models import DetectionResult
from core.constants import PLAIN_TEXT_CHECKPOINT_INTERVAL
from detection import EntropyDetector, RepetitionDetector, calculate_shannon_entropy, detect_content_type
from utils import get_logger

log = get_logger()


class CheckpointManager:
    """Decides when to take a checkpoint based on content structure.
    
    Strategies (in priority order):
      1. Double newline (\\n\\n)  — generic structural break, if >20 chars since last checkpoint
      2. JSON  — at each top-level item/key completion (closing `}` or `]`)
      3. XML   — at every correctly closed tag at any nesting level
      4. HTML  — at closing of semantic elements: </p>, </li>, </tr>, </div>
      5. Text  — every PLAIN_TEXT_CHECKPOINT_INTERVAL tokens (fallback)
    """
    
    def __init__(self) -> None:
        self._detector = RepetitionDetector()
        self._entropy_detector = EntropyDetector()
        self._content_type: Optional[str] = None
        self._checkpoint_count = 0
    
    def _ensure_content_type(self, text: str) -> None:
        """Detect content type on first non-empty text."""
        if self._content_type is None and text.strip():
            self._content_type = detect_content_type(text)
            log.info(f"Content type detected: {self._content_type}")
    
    def should_checkpoint(
        self,
        full_text: str,
        new_chunk: str,
        token_count: int,
        last_checkpoint_token: int,
        last_checkpoint_char_len: int = 0,
    ) -> bool:
        """Determine if a checkpoint should be created based on content structure.
        
        Args:
            full_text: Complete generated text so far
            new_chunk: Most recently generated chunk
            token_count: Total tokens generated
            last_checkpoint_token: Token count at last checkpoint
            last_checkpoint_char_len: Character length at last checkpoint
        
        Returns:
            True if a checkpoint should be created.
        """
        # A checkpoint must contain new generated text. The trailing usage-only
        # chunk (empty delta, exact token counts) carries none and must not
        # trigger one, even though it can bump token_count past the interval.
        if not new_chunk:
            return False

        self._ensure_content_type(full_text)
        ct = self._content_type
        
        # Double newline — generic paragraph/section break (all content types)
        if "\n\n" in new_chunk:
            delta_chars = len(full_text) - last_checkpoint_char_len
            if delta_chars > 20:  # avoid micro-checkpoints
                return True
        
        if ct == "json":
            return "}" in new_chunk or "]" in new_chunk
        
        if ct == "xml" or ct == "html":
            # Look for `</` — indicates a closing tag (structural boundary).
            # More robust than `>\n` because the newline can arrive in a separate chunk.
            if "</" in new_chunk:
                delta_chars = len(full_text) - last_checkpoint_char_len
                if delta_chars > 20:  # avoid micro-checkpoints on rapid tags
                    return True
            return False
        
        # Plain-text fallback
        return (token_count - last_checkpoint_token) >= PLAIN_TEXT_CHECKPOINT_INTERVAL
    
    def health_check(self, text: str) -> DetectionResult:
        """Run both repetition and entropy detection.
        
        Args:
            text: Text to analyze
        
        Returns:
            DetectionResult from detectors (critical failures take priority).
        """
        # First check repetition (Phase 1)
        repetition_result = self._detector.detect(text)
        if repetition_result.severity == "critical":
            return repetition_result
        
        # Then check entropy (Phase 2) - only on plain text
        if self._content_type == "text" or self._content_type is None:
            entropy_result = self._entropy_detector.detect(text, self._checkpoint_count)
            if entropy_result.severity == "critical":
                return entropy_result
            elif entropy_result.severity == "warning":
                # Return warning but don't fail yet
                return entropy_result
        
        return repetition_result
    
    def repetition_critical(self, text: str) -> bool:
        """Repetition check only (no entropy), for text that is not content, e.g. thinking."""
        return self._detector.detect(text).severity == "critical"

    def get_last_checkpoint_entropy(self, text: str) -> Optional[float]:
        """Calculate entropy for the last checkpoint text.
        
        Args:
            text: Text to calculate entropy for
        
        Returns:
            Shannon entropy value or None if text too short.
        """
        return calculate_shannon_entropy(text)
    
    def increment_checkpoint_count(self) -> None:
        """Track checkpoint count for entropy detector."""
        self._checkpoint_count += 1
