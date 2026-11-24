"""
de²generator test client — simulates thalabus sending requests to de²generator.

Usage:
  Terminal 1: python de2generator.py          (starts the supervisor on :8356)
  Terminal 2: python de2generator_test.py     (runs all test cases)

Individual tests:
  python de2generator_test.py hello           # quick hello-world smoke test
  python de2generator_test.py text            # plain-text dog story
  python de2generator_test.py html            # HTML-formatted story
  python de2generator_test.py json            # CSV → JSON conversion
  python de2generator_test.py models          # list models via de²generator
"""

import argparse
import json
import sys
import threading
import time

import httpx

# ---------------------------------------------------------------------------
# ANSI Color codes
# ---------------------------------------------------------------------------

_ANSI_BLUE  = "\033[34m"
_ANSI_GREEN = "\033[32m"
_ANSI_RESET = "\033[0m"

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DE2_BASE_URL = "http://127.0.0.1:8356"
TIMEOUT      = 300.0   # seconds — long generations can be slow

# Model served by vLLM (passed through de²generator unchanged)
MODEL = "Qwen/Qwen3.5-35B-A3B-FP8"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _hr(char: str = "─", width: int = 80) -> str:
    return char * width


def _send(messages: list, *, max_tokens: int = 8192,
          thinking_budget: int | None = 2048,
          enable_thinking: bool = True,
          label: str = "") -> dict:
    """
    POST a chat-completion request to de²generator and return the parsed JSON.
    Mirrors how thalabus's provider_qwen.py submits requests.
    """
    chat_template_kwargs: dict = {"enable_thinking": enable_thinking}
    if enable_thinking and thinking_budget is not None:
        chat_template_kwargs["thinking_budget"] = thinking_budget

    payload = {
        "model":               MODEL,
        "messages":            messages,
        "max_tokens":          max_tokens,
        "max_completion_tokens": max_tokens,
        "chat_template_kwargs": chat_template_kwargs,
    }

    print(f"\n{_hr()}")
    print(f"  TEST: {label}")
    print(f"  model={MODEL}  max_tokens={max_tokens}  "
          f"enable_thinking={enable_thinking}  thinking_budget={thinking_budget}")
    print(_hr())

    t0 = time.perf_counter()
    _resp: list = [None]
    _err:  list = [None]

    def _do_post() -> None:
        try:
            with httpx.Client(timeout=TIMEOUT) as client:
                _resp[0] = client.post(f"{DE2_BASE_URL}/v1/chat/completions", json=payload)
        except Exception as exc:
            _err[0] = exc

    thread = threading.Thread(target=_do_post, daemon=True)
    thread.start()
    try:
        while thread.is_alive():
            thread.join(timeout=0.25)
    except KeyboardInterrupt:
        print("\n[INTERRUPTED] Test cancelled by user")
        raise
    elapsed = time.perf_counter() - t0

    if _err[0]:
        raise _err[0]
    resp = _resp[0]

    if resp.status_code != 200:
        print(f"  [ERROR] HTTP {resp.status_code}: {resp.text[:400]}")
        return {}

    data = resp.json()
    content = (data.get("choices") or [{}])[0].get("message", {}).get("content", "")
    de2_meta = data.get("de2generator", {})
    thinking = de2_meta.get("thinking", "")
    tokens = data.get('usage', {}).get('completion_tokens', 0)
    tps = tokens / elapsed if elapsed > 0 else 0

    print(f"  elapsed : {elapsed:.1f}s")
    print(f"  tokens  : {tokens}  ({tps:.1f} tok/s)")
    print(f"  recovery: {de2_meta.get('recovery_attempts', 0)}  "
          f"checkpoints: {de2_meta.get('checkpoints', 0)}")
    print(_hr("─"))
    if thinking:
        thinking_clean = thinking.rstrip()
        print(f"{_ANSI_BLUE}[THINKING]")
        print(thinking_clean)
        print(f"[/THINKING]{_ANSI_RESET}")
    content_clean = content.strip()
    if content_clean:
        print(f"{_ANSI_GREEN}[CONTENT]")
        print(content_clean)
        print(f"[/CONTENT]{_ANSI_RESET}")
    print(_hr("═"))
    return data


# ---------------------------------------------------------------------------
# Test cases
# ---------------------------------------------------------------------------

def test_hello() -> dict:
    """Quick hello-world smoke test — minimal tokens, no thinking."""
    messages = [
        {"role": "user", "content": "Say exactly: Hello World"},
    ]
    return _send(
        messages,
        max_tokens=64,
        thinking_budget=None,
        enable_thinking=False,
        label="Hello World (quick smoke test)",
    )


def test_hello_with_thinking() -> dict:
    """Hello World with thinking enabled."""
    messages = [
        {"role": "user", "content": "Say exactly: Hello World"},
    ]
    return _send(
        messages,
        max_tokens=256,
        thinking_budget=512,
        enable_thinking=True,
        label="Hello World with thinking (supervised reasoning)",
    )


def test_plain_text_story() -> dict:
    """
    Long plain-text story about a dog named Charly — 5 chapters.
    No structured output; exercises the 250-token fallback checkpoint strategy.
    Thinking level: SIMPLE (2048 tokens).
    """
    messages = [
        {
            "role": "system",
            "content": (
                "You are a gifted storyteller. "
                "Write in clear, vivid prose with rich descriptions."
            ),
        },
        {
            "role": "user",
            "content": (
                "Write a complete five-chapter story about a golden retriever named Charly. "
                "Each chapter must be at least 400 words long. "
                "Chapters:\n"
                "  1. The day Charly arrives home as a puppy.\n"
                "  2. Charly's first winter and learning to fetch.\n"
                "  3. Charly saves a child from a frozen lake.\n"
                "  4. Charly grows old but remains loyal.\n"
                "  5. A final walk at sunrise and a peaceful goodbye.\n\n"
                "Use clear chapter headings (e.g. '# Chapter 1 — ...')."
            ),
        },
    ]
    return _send(
        messages,
        max_tokens=8192,
        thinking_budget=2048,
        enable_thinking=True,
        label="Plain-text story — Charly the dog (5 chapters)",
    )


def test_html_story() -> dict:
    """
    Long HTML-formatted story.
    Exercises the HTML checkpoint strategy (</p>, </div>, </li>, </tr>).
    Thinking level: SIMPLE (2048 tokens).
    """
    messages = [
        {
            "role": "system",
            "content": (
                "You are a web content author. "
                "Output VALID HTML5 with <!DOCTYPE html> and <html> wrappers. "
                "Use semantic elements: <h1>, <h2>, <p>, <ul>, <li>, <blockquote>, <div>."
            ),
        },
        {
            "role": "user",
            "content": (
                "Write a five-chapter adventure story about Charly the golden retriever, "
                "formatted as a complete HTML document. "
                "Requirements:\n"
                "  - Start with <!DOCTYPE html> and <html><head>...</head><body> structure\n"
                "  - Wrap each chapter in a <div class='chapter'>\n"
                "  - Use <h2> for chapter titles (wrapped in <body>)\n"
                "  - Each chapter: at least 4 <p> paragraphs\n"
                "  - Include a <ul> of key events at the end of each chapter\n"
                "  - End with a <footer> containing the word count estimate\n\n"
                "Output ONLY the HTML, no markdown fences."
            ),
        },
    ]
    return _send(
        messages,
        max_tokens=8192,
        thinking_budget=2048,
        enable_thinking=True,
        label="HTML-formatted story — Charly the dog (5 chapters)",
    )


def test_csv_to_json() -> dict:
    """
    Convert a synthetic CSV of 10 customers to a JSON list.
    Exercises the JSON List checkpoint strategy.
    Thinking level: FAST (1024 tokens) — straightforward conversion task.
    """
    csv_data = (
        "id,first_name,last_name,email,phone,address,city,country,postal_code,account_balance\n"
        "1,Alice,Martin,alice.martin@example.com,+33-1-2345-6789,12 Rue de la Paix,Paris,France,75001,1250.50\n"
        "2,Bob,Schmidt,bob.schmidt@example.de,+49-30-9876-5432,Unter den Linden 5,Berlin,Germany,10117,340.00\n"
        "3,Clara,Rossi,clara.rossi@example.it,+39-06-1234-5678,Via Condotti 8,Rome,Italy,00187,870.25\n"
        "4,David,Jones,david.jones@example.co.uk,+44-20-7946-0958,10 Downing St,London,UK,SW1A 2AA,5000.00\n"
        "5,Eva,Novak,eva.novak@example.cz,+420-222-333-444,Wenceslas Square 1,Prague,Czech Republic,11000,190.75\n"
        "6,Frank,Müller,frank.mueller@example.de,+49-89-1122-3344,Marienplatz 3,Munich,Germany,80331,2200.00\n"
        "7,Grace,Dupont,grace.dupont@example.fr,+33-4-5678-9012,3 Cours Mirabeau,Aix-en-Provence,France,13100,660.40\n"
        "8,Hans,Bauer,hans.bauer@example.at,+43-1-2345-6780,Stephansplatz 2,Vienna,Austria,1010,430.90\n"
        "9,Iris,Santos,iris.santos@example.pt,+351-21-345-6789,Rua Augusta 100,Lisbon,Portugal,1100-048,980.00\n"
        "10,Jan,Kowalski,jan.kowalski@example.pl,+48-22-987-6543,Nowy Swiat 15,Warsaw,Poland,00-029,320.15\n"
    )

    messages = [
        {
            "role": "system",
            "content": (
                "You are a data-conversion assistant. "
                "Output only valid JSON — no markdown fences, no explanation text."
            ),
        },
        {
            "role": "user",
            "content": (
                "Convert the following CSV to a JSON array. "
                "Each row becomes an object with keys matching the CSV column headers. "
                "Preserve all values exactly as they appear; convert numeric fields "
                "(id, account_balance) to their natural JSON types.\n\n"
                f"```csv\n{csv_data}```\n\n"
                "Output the JSON array only."
            ),
        },
    ]
    return _send(
        messages,
        max_tokens=4096,
        thinking_budget=1024,
        enable_thinking=True,
        label="CSV → JSON conversion (10 customers)",
    )


def test_list_models() -> None:
    """Verify that de²generator proxies /v1/models from vLLM."""
    print(f"\n{_hr()}")
    print("  TEST: GET /v1/models")
    print(_hr())
    with httpx.Client(timeout=10.0) as client:
        resp = client.get(f"{DE2_BASE_URL}/v1/models")
    print(f"  HTTP {resp.status_code}")
    try:
        data = resp.json()
        models = [m.get("id") for m in data.get("data", [])]
        print(f"  models: {models}")
    except Exception:
        print(f"  raw: {resp.text[:400]}")
    print(_hr("═"))


def test_health() -> None:
    """Quick liveness check."""
    print(f"\n{_hr()}")
    print("  TEST: GET /health")
    print(_hr())
    with httpx.Client(timeout=5.0) as client:
        resp = client.get(f"{DE2_BASE_URL}/health")
    print(f"  HTTP {resp.status_code}  body={resp.text}")
    print(_hr("═"))


# ---------------------------------------------------------------------------
# Result validator
# ---------------------------------------------------------------------------

def _validate_json_output(data: dict, label: str) -> None:
    """For the CSV→JSON test, confirm the response parses as a list of 10 items."""
    content = (data.get("choices") or [{}])[0].get("message", {}).get("content", "")
    try:
        parsed = json.loads(content)
        if isinstance(parsed, list) and len(parsed) == 10:
            print(f"  [PASS] {label}: valid JSON list with {len(parsed)} items")
        else:
            print(f"  [WARN] {label}: parsed OK but unexpected shape — {type(parsed).__name__}, len={len(parsed) if isinstance(parsed, list) else 'N/A'}")
    except json.JSONDecodeError as exc:
        print(f"  [FAIL] {label}: response is not valid JSON — {exc}")
        print(f"         first 200 chars: {content[:200]}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

ALL_TESTS = {
    "hello":         (test_hello,              None),
    "hello-think":   (test_hello_with_thinking, None),
    "health":        (test_health,             None),
    "models":        (test_list_models,        None),
    "text":          (test_plain_text_story,   None),
    "html":          (test_html_story,         None),
    "json":          (test_csv_to_json,        "CSV → JSON conversion (10 customers)"),
}


def main() -> int:
    parser = argparse.ArgumentParser(description="de²generator test client")
    parser.add_argument(
        "tests",
        nargs="*",
        choices=list(ALL_TESTS.keys()),
        default=list(ALL_TESTS.keys()),
        help="Which tests to run (default: all)",
    )
    args = parser.parse_args()

    # Verify de²generator is reachable before running generation tests
    try:
        with httpx.Client(timeout=5.0) as client:
            resp = client.get(f"{DE2_BASE_URL}/health")
        if resp.status_code != 200:
            print(f"[ERROR] de²generator health check failed: HTTP {resp.status_code}")
            return 1
        print(f"[OK] de²generator reachable at {DE2_BASE_URL}")
    except Exception as exc:
        print(f"[ERROR] Cannot reach de²generator at {DE2_BASE_URL}: {exc}")
        print("        Start it first:  python de2generator.py")
        return 1

    results: dict[str, dict | None] = {}

    try:
        for name in args.tests:
            fn, validator_label = ALL_TESTS[name]
            try:
                result = fn()
                results[name] = result
            except KeyboardInterrupt:
                raise
            except Exception as exc:
                print(f"  [ERROR] test '{name}' raised: {exc}")
                results[name] = None
    except KeyboardInterrupt:
        print(f"\n\n[INTERRUPTED] Tests cancelled by user")
        return 130  # Standard exit code for Ctrl-C

    # Post-run validations
    print(f"\n{_hr('═')}")
    print("  POST-RUN VALIDATIONS")
    print(_hr("═"))

    for name, data in results.items():
        _, validator_label = ALL_TESTS[name]
        if validator_label and data:
            _validate_json_output(data, validator_label)

    print(f"\n{_hr('─')}")
    print(f"  Tests run: {len(results)}  |  "
          f"Passed (non-empty): {sum(1 for v in results.values() if v)}")
    print(_hr("─"))

    return 0


if __name__ == "__main__":
    sys.exit(main())
