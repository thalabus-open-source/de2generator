"""The `/health` and `/health/upstream` split, tested against the real ASGI app.

These two endpoints answer different questions, and the whole point of the split is
that a broken backend must never turn into a liveness failure — an orchestrator that
restarts this proxy because its upstream is down turns a clean 502 into an outage.

The upstream is pointed at a closed port (nothing can answer on port 1), so the probe
has to report the failure rather than raise: the exception path in
``api.system.health_upstream`` is the contract being asserted here.
"""

import sys
import unittest
from pathlib import Path

import httpx

SRC_DIR = Path(__file__).resolve().parent.parent
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import de2generator                                                       # noqa: E402
from core.endpoints import get_vllm_base_url, set_vllm_base_url            # noqa: E402

DEAD_BACKEND = "http://127.0.0.1:1"          # nothing listens here; connect refused

class HealthProbeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        # The base URL is process-global, so save and restore it: other suites in the
        # same pytest process talk to the mock backend and would inherit port 1.
        self._default_url = get_vllm_base_url()

    async def asyncTearDown(self) -> None:
        set_vllm_base_url(self._default_url)

    async def _get(self, path: str) -> httpx.Response:
        app = de2generator.create_app()
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                    base_url="http://test") as client:
            return await client.get(path)

    async def test_liveness_is_always_ok_even_with_a_dead_backend(self) -> None:
        set_vllm_base_url(DEAD_BACKEND)
        response = await self._get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok", "component": "de2generator"})

    async def test_upstream_probe_reports_a_dead_backend_without_failing(self) -> None:
        set_vllm_base_url(DEAD_BACKEND)
        response = await self._get("/health/upstream")
        self.assertEqual(response.status_code, 200, "the probe itself must not fail")
        body = response.json()
        self.assertEqual(set(body), {"base_url", "reachable", "status_code", "error"})
        self.assertEqual(body["base_url"], DEAD_BACKEND)
        self.assertIs(body["reachable"], False)
        self.assertIsNone(body["status_code"])
        self.assertIn("ConnectError", body["error"])


if __name__ == "__main__":
    unittest.main()
