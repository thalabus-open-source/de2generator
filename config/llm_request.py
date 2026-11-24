"""Simple LLM request against an OpenAI-compatible endpoint over HTTPS."""

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config" / "llm_config.json"
ENV_PATH = ROOT / ".env"


class FatalConfigError(RuntimeError):
    """Raised when required configuration or credentials cannot be resolved."""


def load_config(path: Path = CONFIG_PATH) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def parse_env_file(path: Path) -> dict:
    """Parse KEY=value lines from a .env file, ignoring blanks and comments."""
    values = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        key, sep, value = line.partition("=")
        if not sep:
            continue
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key:
            values[key] = value
    return values


def resolve_api_key(config: dict) -> str:
    env_var = config.get("api_key_env", "LLM_API_KEY")

    api_key = os.environ.get(env_var)
    if api_key:
        return api_key

    env_path = Path(config.get("env_file", ENV_PATH))
    if env_path.is_file():
        api_key = parse_env_file(env_path).get(env_var)
        if api_key:
            os.environ[env_var] = api_key
            return api_key

    raise FatalConfigError(
        f"{env_var} is not set. Define it as an environment variable, or provide "
        f"a {env_path} file containing '{env_var}=<your-key>'."
    )


def chat_completion(prompt: str, config: dict) -> str:
    base_url = config["base_url"].rstrip("/")
    if not base_url.startswith("https://"):
        raise ValueError(f"base_url must use https, got: {base_url}")

    api_key = resolve_api_key(config)

    url = base_url + config["endpoint"]
    payload = {
        "model": config["model"],
        "messages": [{"role": "user", "content": prompt}],
        "temperature": config.get("temperature", 0.7),
        "max_tokens": config.get("max_tokens", 256),
    }

    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=config.get("timeout_seconds", 60)) as response:
            body = json.load(response)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code} from {url}: {detail}") from exc

    return body["choices"][0]["message"]["content"]


if __name__ == "__main__":
    user_prompt = sys.argv[1] if len(sys.argv) > 1 else "The username is Alice. Say hello to her in one short sentence."
    try:
        print(chat_completion(user_prompt, load_config()))
    except FatalConfigError as error:
        print(f"fatal: {error}", file=sys.stderr)
        sys.exit(1)
