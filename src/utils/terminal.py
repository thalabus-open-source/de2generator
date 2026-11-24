"""Terminal output utilities for de²generator."""

# ANSI color codes
_ANSI_BLUE = "\033[34m"
_ANSI_GREEN = "\033[32m"
_ANSI_RED = "\033[31m"
_ANSI_RESET = "\033[0m"


def print_block(label: str, ok: bool, text: str, *, note: str = "") -> None:
    """Print an [OK] or [REJECT] block to stdout with the accumulated text.
    
    Args:
        label: Block label
        ok: True for [OK] (green), False for [REJECT] (red)
        text: Text content to print
        note: Optional note to append to label
    """
    colour = _ANSI_GREEN if ok else _ANSI_RED
    tag = "OK" if ok else "REJECT"
    suffix = f"  {note}" if note else ""
    print(f"{colour}[{tag}]{_ANSI_RESET} {label}{suffix}")
    if text:
        print(text)
    print()


def print_thinking_block(label: str, text: str) -> None:
    """Print a thinking checkpoint block in blue.
    
    Args:
        label: Block label
        text: Thinking content to print
    """
    print(f"{_ANSI_BLUE}[THINKING]{_ANSI_RESET} {label}")
    if text:
        print(text)
    print()
