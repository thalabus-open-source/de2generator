"""Content type detection for checkpointing strategies."""

import re


# HTML semantic elements that trigger a checkpoint
HTML_CHECKPOINT_TAGS = {"p", "li", "tr", "div"}

# Regex for detecting closing tags
RE_CLOSING_TAG = re.compile(r'</[a-zA-Z][^>]*>')


def detect_content_type(text: str) -> str:
    """Infer the output format from the first non-whitespace characters.
    
    Returns one of: "json", "xml", "html", "text".
    
    HTML detection includes explicit DOCTYPE/html tags AND common HTML-only tags
    like <h1>, <h2>, <p>, <div class>, <body>, etc. (to catch HTML that starts
    with semantic tags instead of wrappers).
    
    Args:
        text: Text to analyze
    
    Returns:
        Content type string: "json", "xml", "html", or "text"
    """
    stripped = text.lstrip()
    if stripped.startswith(("{", "[")):
        return "json"
    lower_stripped = stripped.lower()
    if lower_stripped.startswith(("<!doctype html", "<html", "<body")):
        return "html"
    # Catch HTML-specific semantic tags (h1-h6, p, div class, section, article, etc.)
    if lower_stripped.startswith(("<h1", "<h2", "<h3", "<h4", "<h5", "<h6",
                                  "<p>", "<p ", "<div", "<section", "<article",
                                  "<main", "<header", "<footer", "<nav",
                                  "<ul", "<ol", "<table")):
        return "html"
    if stripped.startswith("<"):
        return "xml"
    return "text"
