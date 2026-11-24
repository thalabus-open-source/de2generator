r"""Minimaler Client für de²generator: eine Anfrage, eine Antwort.

Kette: Client -> de²generator -> LLM. Der Client schickt Modell und Bearer-Key mit,
de²generator reicht beides an das LLM durch. Die Antwort kommt immer als fertiges
JSON; einen Token-Stream zum Client gibt es nicht.

Aufruf (im Repo-Root):
    .venv\Scripts\python.exe client\minimal_client.py --model <modell> "Sag kurz Hallo."

Umgebungsvariablen (Fallback: .env im Repo-Root):
    DE2_BASE_URL   Adresse von de²generator (Standard: http://127.0.0.1:8356)
    LLM_MODEL      Modellname, überschreibbar mit --model
    LLM_API_KEY    Bearer-Key; wird nie ausgegeben

Antwort auf stdout, Statistik und Fehler auf stderr.
Exit-Codes: 0 ok, 1 Konfiguration fehlt, 2 Server nicht erreichbar,
3 HTTP-Fehler, 4 leere Antwort.
"""

import argparse
import os
import re
import sys
import time
from pathlib import Path
from typing import NoReturn
from urllib.parse import urlsplit

try:
    import httpx
except ImportError:
    print("Fehler: httpx fehlt. Bitte mit .venv\\Scripts\\python.exe aufrufen.", file=sys.stderr)
    sys.exit(1)

EXIT_OK = 0
EXIT_CONFIG = 1
EXIT_UNREACHABLE = 2
EXIT_HTTP = 3
EXIT_EMPTY = 4

ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
DEFAULT_BASE_URL = "http://127.0.0.1:8356"
LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}
HEALTH_TIMEOUT_SECONDS = 5.0
MAX_ERROR_CHARS = 300


class _Parser(argparse.ArgumentParser):
    """Bedienfehler enden mit Exit-Code 1 (argparse nutzt sonst 2)."""

    def error(self, message: str) -> NoReturn:
        self.print_usage(sys.stderr)
        self.exit(EXIT_CONFIG, f"Fehler: {message}\n")


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    # Bewusst kein Argument für den Key: er stünde sonst in Verlauf und Prozessliste.
    parser = _Parser(description="Minimaler Client für de²generator")
    parser.add_argument("prompt", help="Frage an das Modell")
    parser.add_argument("--model", default="", help="überschreibt LLM_MODEL")
    parser.add_argument("--base-url", default="", help="überschreibt DE2_BASE_URL")
    parser.add_argument("--max-tokens", type=int, default=2048)
    parser.add_argument("--timeout", type=float, default=120.0, help="Sekunden (Standard: 120)")
    return parser.parse_args(argv)


def read_env_file(path: Path = ENV_PATH) -> dict[str, str]:
    """Liest KEY=Wert-Zeilen aus einer .env-Datei (leer, wenn sie fehlt)."""
    if not path.is_file():
        return {}
    values = {}
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        key, sep, value = line.removeprefix("export ").partition("=")
        if sep:
            values[key.strip()] = value.strip().strip("\"'")
    return values


def setting(name: str, env_file: dict[str, str], default: str = "") -> str:
    """Umgebungsvariable vor .env vor Standardwert."""
    return (os.environ.get(name) or env_file.get(name) or default).strip()


def config_problem(base_url: str, model: str, api_key: str) -> str:
    """Beschreibt den ersten Konfigurationsfehler, sonst leerer Text."""
    if not model:
        return "LLM_MODEL fehlt (Umgebung, .env oder --model)"
    if not api_key:
        return "LLM_API_KEY fehlt (Umgebung oder .env)"
    # Ungültige Zeichen würden sonst samt Key in einer Header-Fehlermeldung landen.
    if not api_key.isascii() or not api_key.isprintable() or " " in api_key:
        return "LLM_API_KEY enthält ungültige Zeichen"
    parts = urlsplit(base_url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return "DE2_BASE_URL ist keine gültige http(s)-Adresse"
    # Der Key darf nicht unverschlüsselt ins Netz.
    if parts.scheme == "http" and parts.hostname not in LOCAL_HOSTS:
        return "DE2_BASE_URL muss bei http auf localhost zeigen"
    return ""


def redact(text: str, api_key: str) -> str:
    """Entfernt Key und URLs aus einem Fehlertext und kürzt ihn danach."""
    if api_key:
        text = text.replace(api_key, "***")
    text = re.sub(r"https?://[^\s'\"<>]+", "<URL>", text)
    return " ".join(text.split())[:MAX_ERROR_CHARS]


def error_text(response: httpx.Response) -> str:
    """Meldung aus {"error": {"message": ...}}, sonst der Rohtext."""
    try:
        return str(response.json()["error"]["message"])
    except (ValueError, KeyError, TypeError):
        return response.text


def fail(code: int, message: str) -> int:
    print(f"Fehler: {message}", file=sys.stderr)
    return code


def print_stats(data: dict, message: dict, duration: float) -> None:
    stats = data.get("de2generator") or {}
    ttft = stats.get("ttft_seconds")
    ttft_text = "-" if ttft is None else f"{ttft}s"
    print(
        f"[Statistik] modell={data.get('model')} dauer={duration:.1f}s "
        f"recovery_attempts={stats.get('recovery_attempts')} "
        f"ttft={ttft_text} "
        f"tokens_ein={stats.get('input_tokens')} "
        f"tokens_aus={stats.get('total_output_tokens')} "
        f"denktext_zeichen={len(message.get('reasoning_content') or '')} "
        f"truncation={stats.get('truncation_status')}",
        file=sys.stderr,
    )


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(errors="replace")  # Sonderzeichen dürfen die Ausgabe nicht abbrechen

    args = parse_args(argv)
    env_file = read_env_file()
    base_url = (args.base_url or setting("DE2_BASE_URL", env_file, DEFAULT_BASE_URL)).rstrip("/")
    model = args.model.strip() or setting("LLM_MODEL", env_file)
    api_key = setting("LLM_API_KEY", env_file)

    problem = config_problem(base_url, model, api_key)
    if problem:
        return fail(EXIT_CONFIG, problem)

    body = {
        "model": model,
        "messages": [{"role": "user", "content": args.prompt}],
        "max_tokens": args.max_tokens,
        # Wird von de²generator verbraucht und nicht ans LLM weitergereicht.
        "de2generator": {"agent_name": "minimal-client"},
    }
    headers = {"Authorization": f"Bearer {api_key}"}

    try:
        # trust_env=False: Anfragen mit Key nie über einen System-Proxy leiten.
        with httpx.Client(base_url=base_url, timeout=args.timeout, trust_env=False) as client:
            health = client.get("/health", timeout=HEALTH_TIMEOUT_SECONDS)
            if health.status_code != 200:
                return fail(EXIT_HTTP, f"/health antwortet mit HTTP {health.status_code}")
            started = time.perf_counter()
            response = client.post("/v1/chat/completions", json=body, headers=headers)
            duration = time.perf_counter() - started
    except httpx.TimeoutException as exc:
        return fail(EXIT_UNREACHABLE, f"Zeitüberschreitung ({type(exc).__name__})")
    except httpx.HTTPError as exc:
        # Nur der Typ: der Meldungstext könnte Header-Inhalte enthalten.
        return fail(EXIT_UNREACHABLE, f"de²generator nicht erreichbar ({type(exc).__name__})")

    if response.status_code != 200:
        detail = redact(error_text(response), api_key)
        return fail(EXIT_HTTP, f"HTTP {response.status_code} nach {duration:.1f}s: {detail}")

    try:
        data = response.json()
        message = data["choices"][0]["message"]
        answer = (message.get("content") or "").strip()
    except (ValueError, KeyError, IndexError, TypeError, AttributeError):
        return fail(EXIT_HTTP, "Antwort hat nicht die erwartete Form")

    print_stats(data, message, duration)
    if not answer:
        return fail(EXIT_EMPTY, "leere Antwort (HTTP 200)")
    print(answer)
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
