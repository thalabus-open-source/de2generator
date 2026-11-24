"""Repetition-based degeneration detection."""

from core.models import DetectionResult
from core.constants import REPETITION_WINDOW_TOKENS, REPETITION_WARNING_SCORE, REPETITION_CRITICAL_SCORE


class RepetitionDetector:
    """Analyses a rolling window of tokens for repetition patterns.
    
    Metrics:
      - distinct token ratio    (weight 0.40)
      - repeated bigram ratio   (weight 0.35)
      - repeated trigram ratio  (weight 0.25)
    
    Returns a health_score in [0, 100] where 100 is fully healthy.
    """
    
    def __init__(self, window_size: int = REPETITION_WINDOW_TOKENS) -> None:
        self.window_size = window_size
    
    def detect(self, text: str) -> DetectionResult:
        """Detect repetition patterns in text.
        
        Args:
            text: Text to analyze
        
        Returns:
            DetectionResult with health score, severity, and detailed metrics.
        """
        if not text or len(text.strip()) < 20:
            return DetectionResult(score=100.0, severity="ok", reason="insufficient text")
        
        tokens = text.split()
        window = tokens[-self.window_size:] if len(tokens) > self.window_size else tokens
        
        if len(window) < 4:
            return DetectionResult(score=100.0, severity="ok", reason="too few tokens in window")
        
        # Distinct token ratio
        distinct_ratio = len(set(window)) / len(window)
        
        # Repeated bigram ratio
        bigrams = [(window[i], window[i + 1]) for i in range(len(window) - 1)]
        bigram_counts: dict = {}
        for bg in bigrams:
            bigram_counts[bg] = bigram_counts.get(bg, 0) + 1
        repeated_bigrams = sum(1 for c in bigram_counts.values() if c > 1)
        bigram_ratio = repeated_bigrams / max(len(bigrams), 1)
        
        # Repeated trigram ratio
        trigrams = [(window[i], window[i + 1], window[i + 2]) for i in range(len(window) - 2)]
        trigram_counts: dict = {}
        for tg in trigrams:
            trigram_counts[tg] = trigram_counts.get(tg, 0) + 1
        repeated_trigrams = sum(1 for c in trigram_counts.values() if c > 1)
        trigram_ratio = repeated_trigrams / max(len(trigrams), 1)
        
        health_score = (
            distinct_ratio        * 100 * 0.40
            + (1.0 - bigram_ratio)  * 100 * 0.35
            + (1.0 - trigram_ratio) * 100 * 0.25
        )
        
        if health_score >= REPETITION_WARNING_SCORE:
            severity = "ok"
        elif health_score >= REPETITION_CRITICAL_SCORE:
            severity = "warning"
        else:
            severity = "critical"
        
        reason = (
            f"distinct_ratio={distinct_ratio:.2f}  "
            f"bigram_rep={bigram_ratio:.2f}  "
            f"trigram_rep={trigram_ratio:.2f}"
        )
        return DetectionResult(score=health_score, severity=severity, reason=reason)
