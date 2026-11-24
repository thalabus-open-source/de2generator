"""Logging utilities for de²generator."""

import json
import logging
import os
import threading
import time
from typing import Optional


# LLM interaction logging
_LlmInteractionSeqNo = 0
_LlmInteractionSeqNoLock = threading.Lock()
_LLM_INTERACTION_PREFIX = "log_llm_interaction"


def setup_logging():
    """Configure logging for de²generator."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    )


def get_logger(name: str = "de2generator") -> logging.Logger:
    """Get a logger instance.
    
    Args:
        name: Logger name (default: "de2generator")
    
    Returns:
        Logger instance.
    """
    return logging.getLogger(name)


def log_llm_interaction(request_data: dict, overthinking_text: str, final_answer: str, request_id: str) -> Optional[str]:
    """Write one LLM interaction (request + overthinking + response) to a dedicated numbered file.
    
    Files are named log_llm_interaction_YYYYMMDD_HHMMSS_mmm_XXXX.txt (with timestamp and milliseconds).
    The sequence number is generated atomically so concurrent threads never collide.
    Each file is written fresh — never appended to.
    
    Args:
        request_data: The full request dict sent to the LLM (including all parameters).
        overthinking_text: The thinking/reasoning content that exceeded the budget.
        final_answer: The final response content delivered to the client.
        request_id: The unique request ID for this interaction.
    
    Returns:
        The filename of the log file (without path), or None on error.
    """
    global _LlmInteractionSeqNo
    log = get_logger()
    
    with _LlmInteractionSeqNoLock:
        _LlmInteractionSeqNo += 1
        seq = _LlmInteractionSeqNo
    
    # Generate timestamp with milliseconds
    now = time.time()
    dt = time.localtime(now)
    ms = int((now % 1) * 1000)
    timestamp = time.strftime('%Y%m%d_%H%M%S', dt)
    
    filename_base = f"{_LLM_INTERACTION_PREFIX}_{timestamp}_{ms:03d}_{seq:04d}.txt"
    log_dir = os.path.join(os.getcwd(), "logs")
    os.makedirs(log_dir, exist_ok=True)
    filename = os.path.join(log_dir, filename_base)
    
    try:
        with open(filename, "w", encoding="utf-8") as f:
            # Write separator and metadata
            f.write("="*80 + "\n")
            f.write(f"LLM INTERACTION LOG #{seq:04d}\n")
            f.write(f"Request ID: {request_id}\n")
            f.write(f"Timestamp: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write("="*80 + "\n\n")
            
            # Write the full request in JSON format
            f.write("-"*80 + "\n")
            f.write("CLIENT REQUEST (JSON)\n")
            f.write("-"*80 + "\n")
            f.write(json.dumps(request_data, indent=2, ensure_ascii=False))
            f.write("\n\n")
            
            # Write the overthinking content
            f.write("-"*80 + "\n")
            f.write("OVERTHINKING (FULL TEXT)\n")
            f.write("-"*80 + "\n")
            f.write(overthinking_text if overthinking_text else "(empty)")
            f.write("\n\n")
            
            # Write the final answer
            f.write("-"*80 + "\n")
            f.write("FINAL ANSWER (FULL TEXT)\n")
            f.write("-"*80 + "\n")
            f.write(final_answer if final_answer else "(empty)")
            f.write("\n\n")
            
            f.write("="*80 + "\n")
            f.write("END OF LOG\n")
            f.write("="*80 + "\n")
        
        log.info(f"[{request_id}] LLM interaction logged to {filename}")
        return filename_base  # Return just the filename, not the full path
    except Exception as exc:
        log.error(f"[{request_id}] Failed to write LLM interaction log: {exc}")
        return None
