"""Entropy-based degeneration detection."""

import math
from collections import Counter
from typing import Optional

from core.models import DetectionResult
from core.constants import ENTROPY_MIN_HEALTHY, ENTROPY_MAX_HEALTHY, ENTROPY_MIN_CHARS


def calculate_shannon_entropy(text: str) -> Optional[float]:
    """Calculate Shannon entropy of text in bits per byte.
    
    Shannon entropy measures the randomness/information density of text:
    - Low entropy (< 4.0): Highly repetitive, low information content
    - Healthy range (4.0-7.0): Normal natural language
    - High entropy (> 7.0): Too random, possibly corrupted or gibberish
    
    Args:
        text: Text to analyze
    
    Returns:
        Shannon entropy in bits per byte, or None if text is too short.
    """
    if not text or len(text) < ENTROPY_MIN_CHARS:
        return None
    
    # Count byte frequency
    byte_counts = Counter(text.encode('utf-8'))
    total_bytes = sum(byte_counts.values())
    
    # Calculate Shannon entropy: H = -Σ(p_i * log2(p_i))
    entropy = 0.0
    for count in byte_counts.values():
        probability = count / total_bytes
        entropy -= probability * math.log2(probability)
    
    return entropy


class EntropyDetector:
    """Detects text degeneration via Shannon entropy analysis with three-strike rule.
    
    Healthy text has entropy in range 4.0-7.0 bits/byte.
    Anomalies (< 4.0 or > 7.0) trigger strikes.
    Three consecutive strikes → rollback.
    """
    
    def __init__(self) -> None:
        self._strike_count = 0
        self._first_strike_checkpoint_idx: Optional[int] = None
    
    def detect(self, text: str, checkpoint_idx: int) -> DetectionResult:
        """Check entropy and manage strike count.
        
        Args:
            text: Text to analyze
            checkpoint_idx: Current checkpoint index
        
        Returns:
            DetectionResult with score, severity, and reason.
        """
        entropy = calculate_shannon_entropy(text)
        
        if entropy is None:
            # Text too short for entropy calculation
            return DetectionResult(
                score=100.0,
                severity="ok",
                reason="text too short for entropy analysis"
            )
        
        # Check if entropy is in healthy range
        if ENTROPY_MIN_HEALTHY <= entropy <= ENTROPY_MAX_HEALTHY:
            # Reset strikes on healthy checkpoint
            self._strike_count = 0
            self._first_strike_checkpoint_idx = None
            return DetectionResult(
                score=100.0,
                severity="ok",
                reason=f"entropy={entropy:.2f} (healthy range)"
            )
        
        # Anomaly detected
        self._strike_count += 1
        if self._first_strike_checkpoint_idx is None:
            self._first_strike_checkpoint_idx = checkpoint_idx
        
        if self._strike_count >= 3:
            # Third strike → escalate to rejected
            return DetectionResult(
                score=20.0,
                severity="critical",
                reason=f"entropy={entropy:.2f} (3 strikes, rollback to checkpoint {self._first_strike_checkpoint_idx})"
            )
        else:
            # First or second strike → candidate_reject
            return DetectionResult(
                score=50.0,
                severity="warning",
                reason=f"entropy={entropy:.2f} (strike {self._strike_count}/3)"
            )
    
    def get_rollback_target(self) -> Optional[int]:
        """Returns checkpoint index to roll back to (checkpoint before first strike)."""
        if self._first_strike_checkpoint_idx is not None and self._first_strike_checkpoint_idx > 0:
            return self._first_strike_checkpoint_idx - 1
        return None
