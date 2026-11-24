"""Cancellation: the registry, the endpoint and the 499 outcome.

``de2generator`` grew a request body → orchestration → HTTP split, so these tests
drive the ASGI app directly and patch the supervisor at
``api.chat.supervise_generation`` — the place the route actually looks it up.

Run: python -m unittest src.test.test_cancellation   (or pytest)
"""

import asyncio
import sys
import unittest
from pathlib import Path

import httpx

SRC_DIR = Path(__file__).resolve().parents[1]
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import api.chat as chat_route                                          # noqa: E402
from core.models import GenerationSession                              # noqa: E402
from orchestration import cancellation                                 # noqa: E402
from orchestration.cancellation import (                               # noqa: E402
    RequestControl,
    extract_cancellation_token,
    run_cancellable_generation,
    validate_cancellation_token,
)
from ui import _ui_find, ui_start_interaction                           # noqa: E402


class CancellationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        await cancellation._clear()

    async def asyncTearDown(self) -> None:
        await cancellation._clear()

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            transport=httpx.ASGITransport(app=_app()), timeout=5.0, base_url="http://test"
        )

    async def test_cancel_returns_200_and_completion_returns_499(self) -> None:
        started = asyncio.Event()
        stopped = asyncio.Event()

        async def hanging_generation(body, request=None, request_id=None):
            ui_start_interaction(request_id, "test-model", None, False)
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                stopped.set()

        original = chat_route.supervise_generation
        chat_route.supervise_generation = hanging_generation
        try:
            async with self._client() as client:
                completion = asyncio.create_task(client.post(
                    "/v1/chat/completions",
                    json={
                        "model": "test-model",
                        "messages": [{"role": "user", "content": "test"}],
                        "de2generator": {"cancellation_token": "test-token"},
                    },
                ))
                await asyncio.wait_for(started.wait(), timeout=2.0)

                cancel_response = await client.post(
                    "/v1/cancel", json={"cancellation_token": "test-token"}
                )
                completion_response = await asyncio.wait_for(completion, timeout=2.0)
        finally:
            chat_route.supervise_generation = original

        self.assertEqual(cancel_response.status_code, 200)
        self.assertEqual(cancel_response.json()["status"], "cancelled")
        self.assertEqual(completion_response.status_code, 499)
        self.assertEqual(completion_response.json()["error"]["code"], "client_cancelled")
        self.assertTrue(stopped.is_set(), "the generation task was cancelled")
        self.assertNotIn("test-token", await cancellation.snapshot())
        interaction = _ui_find(cancel_response.json()["request_id"])
        self.assertIsNotNone(interaction)
        self.assertEqual(interaction["status"], "cancelled")

    async def test_duplicate_cancellation_token_is_rejected(self) -> None:
        async def blocking(body, request=None, request_id=None):
            await asyncio.sleep(0.05)
            return {"choices": []}, GenerationSession(request_id, "m", [])

        original = chat_route.supervise_generation
        chat_route.supervise_generation = blocking
        try:
            async with self._client() as client:
                body = {"model": "m", "messages": [], "de2generator":
                            {"cancellation_token": "shared"}}
                first = asyncio.create_task(client.post("/v1/chat/completions", json=body))
                await asyncio.sleep(0.01)
                second = await client.post("/v1/chat/completions", json=body)
                await first
        finally:
            chat_route.supervise_generation = original

        self.assertEqual(second.status_code, 409)
        self.assertIn("already in use", second.json()["error"]["message"])

    async def test_completed_generation_wins_a_ready_cancellation_race(self) -> None:
        async def finished(body, request=None, request_id=None):
            return {"choices": []}, GenerationSession("race-request", "m", [])

        control = RequestControl(request_id="race-request", cancel_event=asyncio.Event())
        control.cancel_event.set()
        result = await run_cancellable_generation(finished, {}, None, "race-request", control)

        self.assertIsNotNone(result)
        self.assertEqual(control.state, "completed")

    async def test_cancelling_a_finished_request_frees_its_token(self) -> None:
        async def finished(body, request=None, request_id=None):
            return {"choices": []}, GenerationSession("done", "m", [])

        original = chat_route.supervise_generation
        chat_route.supervise_generation = finished
        try:
            async with self._client() as client:
                completion = await client.post(
                    "/v1/chat/completions",
                    json={"model": "m", "messages": [],
                          "de2generator": {"cancellation_token": "used-once"}},
                )
                cancel_response = await client.post(
                    "/v1/cancel", json={"cancellation_token": "used-once"}
                )
        finally:
            chat_route.supervise_generation = original

        self.assertEqual(completion.status_code, 200)
        self.assertEqual(cancel_response.json(), {"status": "not_found"})

    async def test_unknown_token_is_an_idempotent_success(self) -> None:
        async with self._client() as client:
            response = await client.post("/v1/cancel",
                                         json={"cancellation_token": "unknown-token"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "not_found"})

    async def test_malformed_cancel_requests_are_rejected(self) -> None:
        payloads = [
            {},                                        # missing token
            {"cancellation_token": ""},                # empty
            {"cancellation_token": "   "},             # whitespace only
            {"cancellation_token": "x" * 257},         # too long
            {"cancellation_token": 42},                # wrong type
        ]
        async with self._client() as client:
            for payload in payloads:
                with self.subTest(payload=str(payload)[:24]):
                    response = await client.post("/v1/cancel", json=payload)
                    self.assertEqual(response.status_code, 400)
            non_json = await client.post("/v1/cancel", content="not json",
                                        headers={"Content-Type": "application/json"})
            self.assertEqual(non_json.status_code, 400)


class CancellationRegistryTests(unittest.IsolatedAsyncioTestCase):
    """The registry rules, without an HTTP layer in the way."""

    async def asyncSetUp(self) -> None:
        await cancellation._clear()

    async def asyncTearDown(self) -> None:
        await cancellation._clear()

    async def test_token_claims_are_exclusive(self) -> None:
        first = await cancellation.register_request("tok", "req-1")
        second = await cancellation.register_request("tok", "req-2")
        self.assertIsNotNone(first)
        self.assertIsNone(second)
        self.assertEqual(first.state, "active")

    async def test_cancel_is_idempotent_and_reports_who_was_told(self) -> None:
        control = await cancellation.register_request("tok", "req-1")
        first = await cancellation.cancel_request("tok")
        second = await cancellation.cancel_request("tok")

        self.assertEqual(first.status, "cancelled")
        self.assertEqual(second.status, "already_cancelled")
        self.assertIs(first.control, control)
        self.assertTrue(control.cancel_event.is_set())
        self.assertEqual(control.state, "cancelling")

    async def test_unregister_only_releases_your_own_claim(self) -> None:
        control = await cancellation.register_request("tok", "req-1")
        other = RequestControl(request_id="someone-else", cancel_event=asyncio.Event())
        await cancellation.unregister_request("tok", other)
        self.assertIn("tok", await cancellation.snapshot())

        await cancellation.unregister_request("tok", control)
        self.assertNotIn("tok", await cancellation.snapshot())
        # and the token is claimable again
        self.assertIsNotNone(await cancellation.register_request("tok", "req-2"))

    def test_token_validation(self) -> None:
        self.assertEqual(validate_cancellation_token("  spaced  "), "spaced")
        self.assertEqual(validate_cancellation_token("x" * 256), "x" * 256)
        for bad in (None, "", "  ", 7, "x" * 257):
            with self.subTest(value=str(bad)[:12]):
                with self.assertRaises(ValueError):
                    validate_cancellation_token(bad)

    def test_token_is_read_from_the_extension_block_only(self) -> None:
        self.assertIsNone(extract_cancellation_token({"model": "m"}))
        self.assertIsNone(extract_cancellation_token({"model": "m", "de2generator": {}}))
        self.assertIsNone(extract_cancellation_token(
            {"model": "m", "cancellation_token": "top-level-ignored"}))
        self.assertEqual(
            extract_cancellation_token({"de2generator": {"cancellation_token": " tok "}}),
            "tok",
        )
        with self.assertRaises(ValueError):
            extract_cancellation_token({"de2generator": {"cancellation_token": " "}})


def _app():
    """Built lazily so this module can be imported before the routers are wired."""
    from de2generator import app
    return app


if __name__ == "__main__":
    unittest.main()
