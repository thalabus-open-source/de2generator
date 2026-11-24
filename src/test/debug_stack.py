"""Run de²generator beside a mock backend, on non-production ports.

Production listens on 8356 (see ``config.json`` / ``docker-compose.yml``); this
launcher refuses to start unless the *debug* ports are free, so a test session can
never disturb the deployed proxy.

    python src/test/debug_stack.py serve     # start both, Ctrl-C to stop
    python src/test/debug_stack.py test      # start, run the suite, stop
    python src/test/debug_stack.py test -k cancel

Ports
    8398  mock LLM backend   (mock_llm_backend.py)
    8399  debug proxy        (src/de2generator.py, DE2_PORT)

The debug proxy is started with ``VLLM_BASE_URL`` pointing at the mock and the
semantic verifier disabled, so runs are deterministic and need no GPU.  Set
``DE2_REAL_BACKEND=<url>`` to point the debug proxy at a real backend instead.
"""

import argparse
import os
import signal
import socket
import subprocess
import sys
import time
from typing import Optional

import httpx

HERE = os.path.dirname(os.path.abspath(__file__))                  # …/de2generator/src/test
SRC_DIR = os.path.dirname(HERE)                                    # …/de2generator/src
PROJECT_DIR = os.path.dirname(SRC_DIR)                              # …/de2generator

DEBUG_HOST = os.getenv("DE2_DEBUG_HOST", "127.0.0.1")
DEBUG_PORT = int(os.getenv("DE2_DEBUG_PORT", "8399"))
MOCK_PORT = int(os.getenv("MOCK_LLM_PORT", "8398"))
PRODUCTION_PORT = int(os.getenv("DE2_PRODUCTION_PORT", "8356"))
DEBUG_BASE_URL = f"http://{DEBUG_HOST}:{DEBUG_PORT}"
PYTHON = sys.executable
TAIL_LINES = 60


class PortInUse(RuntimeError):
    """Raised when a port we need is already taken."""


def _port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((DEBUG_HOST, port))
            return True
        except OSError:
            return False


def assert_ports_available() -> None:
    """Never share a port with something already listening — especially production."""
    if not _port_free(PRODUCTION_PORT):
        print(f"[debug-stack] production port {PRODUCTION_PORT} is in use; "
              f"refusing to interfere", file=sys.stderr)
    for port in (MOCK_PORT, DEBUG_PORT):
        if not _port_free(port):
            raise PortInUse(f"debug port {port} is already in use — pass DE2_DEBUG_PORT/MOCK_LLM_PORT")


def _python_for_module(module: str) -> list[str]:
    """Prefer the active virtualenv's interpreter when it has our dependencies."""
    return [PYTHON, "-m", module]


class Service:
    """A child process plus a readiness probe."""

    def __init__(self, name: str, argv: list[str], cwd: str, env: Optional[dict] = None) -> None:
        self.name = name
        self.argv = argv
        self.cwd = cwd
        self.env = {**os.environ, **(env or {})}
        self.log_path = os.path.join(PROJECT_DIR, "logs", f"debug-{name}.log")
        self._process: Optional[subprocess.Popen] = None
        self._log = None

    def start(self) -> None:
        os.makedirs(os.path.dirname(self.log_path), exist_ok=True)
        self._log = open(self.log_path, "w", encoding="utf-8")
        self._process = subprocess.Popen(
            self.argv, cwd=self.cwd, env=self.env,
            stdout=self._log, stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        print(f"[debug-stack] started {self.name} pid={self._process.pid} → {self.log_path}")

    def stop(self) -> None:
        if self._process is None:
            return
        try:
            os.killpg(os.getpgid(self._process.pid), signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            self._process.terminate()
        try:
            self._process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            os.killpg(os.getpgid(self._process.pid), signal.SIGKILL)
        self._process = None
        if self._log is not None:
            self._log.close()
            self._log = None
        print(f"[debug-stack] stopped {self.name}")

    def tail(self, lines: int = TAIL_LINES) -> str:
        try:
            with open(self.log_path, encoding="utf-8", errors="replace") as handle:
                return "".join(handle.readlines()[-lines:])
        except OSError as exc:
            return f"(no log: {exc})"

    def poll(self) -> Optional[int]:
        return self._process.poll() if self._process is not None else None


def _wait_ready(url: str, timeout: float = 30.0, services: Optional[list[Service]] = None) -> None:
    deadline = time.time() + timeout
    last_error = ""
    while time.time() < deadline:
        for service in services or []:
            code = service.poll()
            if code is not None:
                raise RuntimeError(f"{service.name} exited early with code {code}")
        try:
            response = httpx.get(url, timeout=2.0)
            if response.status_code < 500:
                return
            last_error = f"HTTP {response.status_code}"
        except Exception as exc:                      # not up yet
            last_error = f"{type(exc).__name__}: {exc}"
        time.sleep(0.2)
    raise RuntimeError(f"{url} not ready after {timeout:.0f}s ({last_error})")


def build_services() -> list[Service]:
    backend = os.getenv("DE2_REAL_BACKEND") or f"http://{DEBUG_HOST}:{MOCK_PORT}"
    services: list[Service] = []

    if not os.getenv("DE2_REAL_BACKEND"):
        services.append(Service(
            "mock-llm",
            _python_for_module("mock_llm_backend") + ["--port", str(MOCK_PORT),
                                                      "--host", DEBUG_HOST],
            cwd=HERE,
            env={"MOCK_LLM_PORT": str(MOCK_PORT), "MOCK_LLM_HOST": DEBUG_HOST},
        ))

    services.append(Service(
        "proxy",
        [PYTHON, os.path.join(SRC_DIR, "de2generator.py")],
        cwd=PROJECT_DIR,
        env={
            "DE2_HOST": DEBUG_HOST,
            "DE2_PORT": str(DEBUG_PORT),
            "VLLM_BASE_URL": backend,
            # The verifier is a separate model on the production box; keep the
            # debug run deterministic and offline.
            "SEMANTIC_LLM_ENABLED": "false",
            "TRANSLATION_ENABLED": "false",
            "PYTHONPATH": SRC_DIR,
        },
    ))
    return services


def run_tests(pytest_args: list[str]) -> int:
    print(f"[debug-stack] debug proxy at {DEBUG_BASE_URL} (production stays on "
          f":{PRODUCTION_PORT})")
    return subprocess.call(
        _python_for_module("pytest") + pytest_args,
        cwd=HERE,
        env={
            **os.environ,
            "DE2_TEST_BASE_URL": DEBUG_BASE_URL,
            "DE2_TEST_MOCK_URL": os.getenv("DE2_REAL_BACKEND")
            or f"http://{DEBUG_HOST}:{MOCK_PORT}",
            "PYTHONPATH": SRC_DIR,
        },
    )


def serve() -> int:
    services: list[Service] = []
    try:
        for service in build_services():
            service.start()
            services.append(service)

        if not os.getenv("DE2_REAL_BACKEND"):
            _wait_ready(f"http://{DEBUG_HOST}:{MOCK_PORT}/mock/health", services=services)
            print(f"[debug-stack] mock LLM backend on http://{DEBUG_HOST}:{MOCK_PORT}")
        _wait_ready(f"{DEBUG_BASE_URL}/v1/models", services=services)
        print(f"[debug-stack] de²generator (debug) ready on {DEBUG_BASE_URL} "
              f"— dashboard {DEBUG_BASE_URL}/ui")
        print("[debug-stack] Ctrl-C to stop")

        while any(s.poll() is None for s in services):
            time.sleep(0.5)
        for service in services:
            if service.poll() is not None:
                print(f"[debug-stack] {service.name} exited ({service.poll()}):\n"
                      f"{service.tail()}")
                return 1
        return 0
    except KeyboardInterrupt:
        return 0
    finally:
        for service in reversed(services):
            service.stop()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("mode", choices=("serve", "test"), nargs="?", default="serve")
    parser.add_argument("pytest_args", nargs=argparse.REMAINDER,
                        help="extra arguments forwarded to pytest (with: test)")
    args = parser.parse_args()

    assert_ports_available()
    if args.mode == "serve":
        return serve()
    return _test_with_stack(args.pytest_args)


def _test_with_stack(pytest_args: list[str]) -> int:
    services: list[Service] = []
    try:
        for service in build_services():
            service.start()
            services.append(service)
        if not os.getenv("DE2_REAL_BACKEND"):
            _wait_ready(f"http://{DEBUG_HOST}:{MOCK_PORT}/mock/health", services=services)
        _wait_ready(f"{DEBUG_BASE_URL}/v1/models", services=services)
        selected = pytest_args or ["test_debug_server.py", "test_supervision_scenarios.py",
                                   "test_request_context.py", "test_response_builder.py",
                                   "test_cancellation.py", "test_health_probe.py", "-v"]
        return run_tests(selected)
    except Exception as exc:
        print(f"[debug-stack] {exc}", file=sys.stderr)
        for service in services:
            if service.poll() is not None:
                print(f"--- {service.name} log ---\n{service.tail()}", file=sys.stderr)
        return 2
    finally:
        for service in reversed(services):
            service.stop()


if __name__ == "__main__":
    sys.exit(main())
