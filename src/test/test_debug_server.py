"""HTTP integration tests for the refactored proxy — run against the debug stack.

Start the stack and run everything with ``python src/test/debug_stack.py test``,
or point these tests at an already-running debug instance:

    DE2_TEST_BASE_URL=http://127.0.0.1:8399 \\
    DE2_TEST_MOCK_URL=http://127.0.0.1:8398 \\
    PYTHONPATH=src python -m pytest src/test/test_debug_server.py -v

These exercise the whole request path over a real socket: routing, status codes,
the client-visible JSON contract, telemetry, cancellation, runtime config.  The
assertions therefore stay on the contract, not on internals; the supervised loop
itself is covered unit-level by ``test_supervision_scenarios.py``.

The debug stack runs with the semantic verifier disabled, so a healthy answer is
never disturbed by an async verdict: what fails here is the proxy, not a detector.

The debug instance shares the hub's statistics, so nothing here resets counters.
"""

import asyncio
import json
import os
import unittest
from typing import Any, Optional

import httpx

BASE_URL = os.getenv("DE2_TEST_BASE_URL", "http://127.0.0.1:8399").rstrip("/")
MOCK_URL = os.getenv("DE2_TEST_MOCK_URL", "http://127.0.0.1:8398").rstrip("/")

MESSAGES = [{"role": "user", "content": "Describe the keeper's night routine."}]
HEALTHY_SENTENCE = "The lighthouse keeper logged every passing ship."
CONTINUATION_MARKER = "Continue from the following text"
NO_THINK_MARKER = "/no_think"
DEGENERATE_FRAGMENT = "aaaa bbbb cccc"
TRANSLATED_ANSWER = "Il faro era acceso e il porto era al sicuro."

TIMEOUT = httpx.Timeout(60.0, connect=5.0)


class DebugStackTests(unittest.IsolatedAsyncioTestCase):
    """Contract tests against a live debug proxy."""

    @classmethod
    def setUpClass(cls) -> None:
        try:
            with httpx.Client(timeout=httpx.Timeout(4.0, connect=2.0)) as probe:
                probe.get(f"{BASE_URL}/health").raise_for_status()
                probe.get(f"{MOCK_URL}/mock/health").raise_for_status()
        except Exception as exc:                                    # noqa: BLE001
            raise unittest.SkipTest(
                f"debug stack is not up ({exc}) — run `python src/test/debug_stack.py test`"
            ) from exc

    async def asyncTearDown(self) -> None:
        """Leave no runtime configuration behind for the next test."""
        async with httpx.AsyncClient(base_url=BASE_URL, timeout=TIMEOUT) as client:
            await client.post("/ui/logprobs", json={"enabled": False})
            await client.post("/ui/update-translation", json={"enabled": False})

    # ── helpers ───────────────────────────────────────────────────────────────

    async def _mock_served(self, client: httpx.AsyncClient) -> int:
        response = await client.get(f"{MOCK_URL}/mock/requests")
        response.raise_for_status()
        return response.json()["served"]

    async def _mock_requests(self, client: httpx.AsyncClient) -> list[dict]:
        response = await client.get(f"{MOCK_URL}/mock/requests")
        response.raise_for_status()
        return response.json()["requests"]

    async def _calls_since(self, client: httpx.AsyncClient, served_before: int
                           ) -> list[dict]:
        """The requests a single test caused, oldest first."""
        response = await client.get(f"{MOCK_URL}/mock/requests")
        response.raise_for_status()
        body = response.json()
        return body["requests"][-(body["served"] - served_before):]

    async def _complete(self, client: httpx.AsyncClient, model: str = "mock/healthy",
                        messages: Optional[list] = None, top: Optional[dict] = None,
                        **extension: Any) -> httpx.Response:
        """POST a completion, splitting top-level fields from the de2generator block."""
        token = extension.pop("cancellation_token", None)
        if token is not None:
            extension["cancellation_token"] = token
        extension.setdefault("stream", True)
        body: dict = {"model": model, "messages": list(messages or MESSAGES)}
        body.update(top or {})
        body["de2generator"] = extension
        return await client.post("/v1/chat/completions", json=body)

    @staticmethod
    def _content(response: httpx.Response) -> str:
        return (response.json()["choices"][0]["message"].get("content") or "")

    @staticmethod
    def _meta(response: httpx.Response) -> dict:
        return response.json()["de2generator"]

    def _assert_clean_answer(self, response: httpx.Response) -> str:
        content = self._content(response)
        self.assertIn(HEALTHY_SENTENCE, content)
        self.assertNotIn(DEGENERATE_FRAGMENT, content)
        return content

    # ── service endpoints ─────────────────────────────────────────────────────

    async def test_health_reports_the_component(self) -> None:
        async with httpx.AsyncClient(base_url=BASE_URL, timeout=TIMEOUT) as client:
            response = await client.get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok", "component": "de2generator"})

    async def test_upstream_probe_reports_the_backend_without_failing(self) -> None:
        """The healthcheck asks about the proxy; /health/upstream asks about the backend."""
        async with httpx.AsyncClient(base_url=BASE_URL, timeout=TIMEOUT) as client:
            reachable = (await client.get("/health/upstream")).json()
        self.assertEqual(set(reachable), {"base_url", "reachable", "status_code", "error"})
        self.assertTrue(reachable["reachable"], "the mock backend is up in this stack")
        self.assertEqual(reachable["status_code"], 200)
        self.assertIsNone(reachable["error"])
        self.assertEqual(reachable["base_url"], MOCK_URL,
                         "the probe must report the URL it actually tried")
        # The unreachable-backend case (a report, never a 500) needs to control the
        # proxy's own settings, so it lives in test_health_probe.py, which drives the
        # ASGI app in-process instead of over HTTP.

    async def test_models_are_proxied_verbatim(self) -> None:
        async with httpx.AsyncClient(base_url=BASE_URL, timeout=TIMEOUT) as client:
            proxied = (await client.get("/v1/models")).json()
        mirrored = httpx.get(f"{MOCK_URL}/v1/models").json()
        self.assertEqual(proxied["object"], "list")
        self.assertEqual({item["id"] for item in proxied["data"]},
                         {item["id"] for item in mirrored["data"]})

    async def test_dashboard_assets_are_served(self) -> None:
        expected = {"/ui": "text/html", "/ui/app.js": "application/javascript",
                    "/ui/style.css": "text/css", "/ui/stats": "text/html",
                    "/favicon.svg": "image/svg"}
        async with httpx.AsyncClient(base_url=BASE_URL, timeout=TIMEOUT) as client:
            for path, media_type in expected.items():
                response = await client.get(path)
                self.assertEqual(response.status_code, 200, path)
                self.assertIn(media_type, response.headers["content-type"], path)

    async def test_dashboard_state_endpoints_answer(self) -> None:
        async with httpx.AsyncClient(base_url=BASE_URL, timeout=TIMEOUT) as client:
            config = (await client.get("/ui/config.json")).json()
            hourly = (await client.get("/ui/stats/hourly")).json()
            totals = (await client.get("/ui/stats/totals")).json()
            logprobs = (await client.post("/ui/logprobs", json={"enabled": True})).json()
            state = (await client.get("/ui/logprobs")).json()

        self.assertIn("enabled", config["semantic_llm"])
        self.assertIs(config["semantic_llm"]["enabled"], False,
                      "the verifier stays off on the debug stack")
        self.assertIn("detection", config)
        self.assertIsInstance(hourly["hours"], list)
        self.assertIs(logprobs["enabled"], True)
        self.assertEqual(state, logprobs)
        self.assertIn("requests", totals)

    async def test_log_files_cannot_be_traversed(self) -> None:
        async with httpx.AsyncClient(base_url=BASE_URL, timeout=TIMEOUT) as client:
            missing = await client.get("/logs/log_llm_interaction_does_not_exist.log")
            traversal = await client.get("/logs/../../etc/passwd")
            encoded = await client.get("/logs/%2e%2e%2fDockerfile")
        self.assertEqual(missing.status_code, 404)
        for response in (traversal, encoded):
            self.assertIn(response.status_code, (403, 404))

    async def test_static_scripts_cannot_escape_their_folder(self) -> None:
        async with httpx.AsyncClient(base_url=BASE_URL, timeout=TIMEOUT) as client:
            encoded = await client.get("/ui/scripts/%2e%2e%2f%2e%2e%2fDockerfile")
            missing = await client.get("/ui/scripts/nope.js")
        # Raw ".." is normalised away by the router before it reaches the guard,
        # so only the encoded form proves the guard itself works.
        self.assertEqual(encoded.status_code, 403)
        self.assertEqual(missing.status_code, 404)

    # ── happy paths ───────────────────────────────────────────────────────────

    async def test_healthy_completion_is_left_untouched(self) -> None:
        async with httpx.AsyncClient(base_url=BASE_URL, timeout=TIMEOUT) as client:
            response = await self._complete(client, top={"max_tokens": 4096})
            meta = self._meta(response)
            upstream = (await self._mock_requests(client))[-1]["payload"]

        self.assertEqual(response.status_code, 200)
        content = self._assert_clean_answer(response)
        self.assertEqual(meta["recovery_attempts"], 0, "nothing to recover from")
        # The last paragraph carries no trailing blank line, so it reaches the
        # client through the final flush rather than as its own checkpoint.
        self.assertIn(meta["checkpoints"], (2, 3))
        self.assertNotIn("</think>", content)
        self.assertNotIn("[RETRY", response.text.replace("\\n", ""))

        # The client sees an OpenAI-shaped completion with the full accounting
        body = response.json()
        self.assertEqual(body["object"], "chat.completion")
        self.assertEqual(body["choices"][0]["finish_reason"], "stop")
        self.assertEqual(body["usage"]["completion_tokens"], meta["total_tokens"])
        self.assertGreater(meta["elapsed_total_seconds"], 0)
        self.assertGreaterEqual(meta["ttft_seconds"], 0)

        # Nothing proxy-specific is forwarded, and the proxy does ask for usage
        self.assertNotIn("de2generator", upstream)
        self.assertIs(upstream["stream"], True)
        self.assertIs(upstream["stream_options"]["include_usage"], True)
        self.assertEqual(upstream["max_tokens"], 4096)

    async def test_a_healthy_request_is_sent_exactly_once(self) -> None:
        async with httpx.AsyncClient(base_url=BASE_URL, timeout=TIMEOUT) as client:
            before = await self._mock_served(client)
            await self._complete(client, model="mock/json")
            served = await self._mock_served(client)
        self.assertEqual(served - before, 1,
                         "a healthy generation must not be replayed")

    async def test_json_output_is_returned_verbatim(self) -> None:
        async with httpx.AsyncClient(base_url=BASE_URL, timeout=TIMEOUT) as client:
            response = await self._complete(client, model="mock/json")
        payload = json.loads(self._content(response))
        self.assertEqual(payload["ship"], "Aurora")
        self.assertEqual(len(payload["arrivals"]), 2)

    async def test_max_tokens_is_forwarded_verbatim(self) -> None:
        """The proxy never edits the caller's budget on the way through."""
        for requested in (999_999, 100):
            async with httpx.AsyncClient(base_url=BASE_URL, timeout=TIMEOUT) as client:
                response = await self._complete(client, top={"max_tokens": requested})
                forwarded = (await self._mock_requests(client))[-1]["payload"]
            self.assertEqual(response.status_code, 200)
            self.assertEqual(forwarded["max_tokens"], requested)

    async def test_endpoint_override_is_honoured(self) -> None:
        override = f"{MOCK_URL}/alt/v1/chat/completions"
        async with httpx.AsyncClient(base_url=BASE_URL, timeout=TIMEOUT) as client:
            response = await self._complete(client, model_endpoint=override)
            recorded = (await self._mock_requests(client))[-1]
        self.assertEqual(response.status_code, 200)
        self._assert_clean_answer(response)
        self.assertEqual(recorded["path"], "/alt/v1/chat/completions")

    # ── supervision over HTTP ─────────────────────────────────────────────────

    async def test_degeneration_is_detected_rolled_back_and_retried(self) -> None:
        """A backend that keeps degenerating cannot be cured, only detected.

        The clean-recovery path — roll back to the last healthy checkpoint and keep
        the answer — is pinned by ``test_supervision_scenarios``, where the stream is
        scripted. What matters over the wire is that detection, rollback and retry
        all actually happen and that the client still gets a well-formed completion.
        """
        async with httpx.AsyncClient(base_url=BASE_URL, timeout=TIMEOUT) as client:
            before = await self._mock_served(client)
            response = await self._complete(client, model="mock/degenerate")
            meta = self._meta(response)
            calls = await self._calls_since(client, before)

        self.assertEqual(response.status_code, 200)
        self.assertIsInstance(self._content(response), str)
        self.assertGreaterEqual(meta["checkpoints"], 1,
                               "the healthy opening must reach a checkpoint")
        self.assertGreaterEqual(meta["recovery_attempts"], 1,
                               "the noise must cost at least one retry")
        self.assertGreaterEqual(meta["rejected_checkpoints_count"], 1,
                               "the rejected checkpoint must be accounted for")
        self.assertGreaterEqual(len(calls), 2, "the retry must reach the backend")
        self.assertIn(CONTINUATION_MARKER, calls[-1]["payload"]["messages"][-1]["content"],
                      "recovery must continue from the checkpoint, not start over")
        self.assertEqual(calls[-1]["payload"]["model"], "mock/degenerate",
                         "a recovery attempt must not change the deployment")

    async def test_empty_completion_exhausts_the_recovery_ladder(self) -> None:
        async with httpx.AsyncClient(base_url=BASE_URL, timeout=TIMEOUT) as client:
            response = await self._complete(client, model="mock/empty")
            meta = self._meta(response)
            calls = await self._mock_requests(client)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self._content(response), "")
        self.assertEqual(meta["recovery_attempts"], 4,
                         "the retry ladder must have been walked to its end")
        self.assertGreaterEqual(len(calls), 12, "retries and restarts were both walked")

    async def test_thinking_loop_rolls_back_to_the_healthy_checkpoint(self) -> None:
        async with httpx.AsyncClient(base_url=BASE_URL, timeout=TIMEOUT) as client:
            response = await self._complete(client, model="mock/thinkloop")
            meta = self._meta(response)
            calls = await self._mock_requests(client)
        self.assertEqual(response.status_code, 200)
        self._assert_clean_answer(response)
        self.assertGreaterEqual(meta["recovery_attempts"], 1)
        self.assertIn("recovered", meta["truncation_status"])
        self.assertTrue(any(NO_THINK_MARKER in json.dumps(call["payload"])
                            for call in calls),
                        "the recovery must disable thinking")

    async def test_thinking_budget_switches_to_a_no_think_retry(self) -> None:
        template_kwargs = {"thinking_budget": 64, "enable_thinking": True}
        async with httpx.AsyncClient(base_url=BASE_URL, timeout=TIMEOUT) as client:
            response = await self._complete(client, model="mock/overthink",
                                            top={"chat_template_kwargs": template_kwargs})
            meta = self._meta(response)
            calls = await self._mock_requests(client)

        self.assertEqual(response.status_code, 200)
        self._assert_clean_answer(response)
        self.assertGreaterEqual(meta["recovery_attempts"], 1)
        self.assertEqual(meta["truncation_status"],
                         "thinking_budget_exhausted_but_recovered")
        retry = calls[-1]["payload"]
        self.assertIs(retry["chat_template_kwargs"]["enable_thinking"], False)
        self.assertNotIn("thinking_budget", retry["chat_template_kwargs"])
        self.assertIn("INTERRUPTED REASONING", retry["messages"][0]["content"])

    """Logprobs are a dashboard feature: the toggle negotiates them per request."""

    async def test_logprobs_are_requested_when_the_backend_supports_them(self) -> None:
        async with httpx.AsyncClient(base_url=BASE_URL, timeout=TIMEOUT) as client:
            self.assertIs((await client.post("/ui/logprobs",
                                             json={"enabled": True})).json()["enabled"],
                          True)
            response = await self._complete(client, model="mock/healthy")
            upstream = (await self._mock_requests(client))[-1]["payload"]
            state = (await client.get("/ui/logprobs")).json()

        self.assertEqual(response.status_code, 200)
        self._assert_clean_answer(response)
        self.assertIs(upstream["logprobs"], True, "the proxy must ask for logprobs")
        self.assertGreaterEqual(upstream["top_logprobs"], 1)
        self.assertIs(state["unsupported"], False)

    async def test_logprobs_are_dropped_when_the_backend_refuses_them(self) -> None:
        async with httpx.AsyncClient(base_url=BASE_URL, timeout=TIMEOUT) as client:
            await client.post("/ui/logprobs", json={"enabled": True})
            before = await self._mock_served(client)
            response = await self._complete(client, model="mock/nologprobs")
            served = await self._mock_served(client)
            calls = (await self._mock_requests(client))[-(served - before):]
            state = (await client.get("/ui/logprobs")).json()
            retry = calls[-1]["payload"]

        self.assertEqual(response.status_code, 200)
        self._assert_clean_answer(response)
        self.assertGreaterEqual(len(calls), 2, "the refusal must be retried")
        self.assertIs(calls[0]["payload"]["logprobs"], True)
        self.assertNotIn("logprobs", retry, "the retry must not ask again")
        self.assertIs(state["unsupported"], True, "the UI pill must turn red")

    async def test_translation_falls_back_when_no_service_applies(self) -> None:
        async with httpx.AsyncClient(base_url=BASE_URL, timeout=TIMEOUT) as client:
            turned_on = await client.post("/ui/update-translation",
                                          json={"enabled": True, "language": "zh",
                                                "agent_type": "DefinitelyUnknownAgent"})
            config = (await client.get("/ui/config.json")).json()
            response = await self._complete(client, model="mock/healthy",
                                            agent_name="DefinitelyUnknownAgent")
            meta = self._meta(response)
            content = self._content(response)

        self.assertEqual(turned_on.status_code, 200)
        self.assertIs(config["translation"]["enabled"], True)
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(meta["translation_language"])
        self.assertIs(meta["translation_applied"], False)
        self.assertIn(HEALTHY_SENTENCE, content)
        self.assertNotIn(TRANSLATED_ANSWER, content)

    @unittest.skipUnless(os.getenv("DE2_TEST_LIVE_TRANSLATION") == "1", "needs the "
                         "translation service from config.json, which cannot be "
                         "redirected per request (set DE2_TEST_LIVE_TRANSLATION=1)")
    async def test_translation_uses_the_service_for_an_allow_listed_agent(self) -> None:
        """Opt-in: the positive path depends on a GPU service outside this suite.

        The translation endpoint has no per-request override, so the only way to
        exercise the happy path here is the service configured in config.json.
        """
        async with httpx.AsyncClient(base_url=BASE_URL, timeout=TIMEOUT) as client:
            config = (await client.get("/ui/config.json")).json()
            language = config["translation"]["thalabus"].get("ui_language") or "it"
            await client.post("/ui/update-translation",
                              json={"enabled": True, "language": language,
                                    "agent_type": "AgentAssistant"})
            try:
                response = await self._complete(client, model="mock/healthy",
                                                agent_name="AgentAssistant")
                meta = self._meta(response)
            finally:
                await client.post("/ui/update-translation", json={"enabled": False})

        self.assertIs(meta["translation_applied"], True)
        self.assertEqual(meta["translation_language"].lower(), language)
        self.assertNotIn(HEALTHY_SENTENCE, self._content(response))

    # ── error mapping ─────────────────────────────────────────────────────────

    async def test_unreachable_endpoint_is_bad_gateway(self) -> None:
        async with httpx.AsyncClient(base_url=BASE_URL, timeout=TIMEOUT) as client:
            response = await self._complete(
                client, model_endpoint="http://127.0.0.1:9/v1/chat/completions")
            payload = response.json()
        self.assertEqual(response.status_code, 502)
        self.assertIn("Cannot connect", payload["error"]["message"])

    async def test_backend_error_is_relayed(self) -> None:
        async with httpx.AsyncClient(base_url=BASE_URL, timeout=TIMEOUT) as client:
            response = await self._complete(client, model="mock/error500")
            payload = response.json()
            calls = await self._mock_requests(client)
        self.assertEqual(response.status_code, 500)
        self.assertIn("error", payload)
        self.assertGreaterEqual(len(calls), 1)
        # An HTTP error from the backend is relayed as-is: only transport failures
        # are retried, so a failing deployment is not hammered.

    async def test_unvalidated_fields_are_forwarded_rather_than_rejected(self) -> None:
        """No schema validation: the proxy forwards and lets the backend judge."""
        async with httpx.AsyncClient(base_url=BASE_URL, timeout=TIMEOUT) as client:
            no_messages = await client.post("/v1/chat/completions",
                                          json={"model": "mock/healthy"})
            bad_json = await client.post("/v1/chat/completions",
                                         content=b"{not json",
                                         headers={"content-type": "application/json"})
        self.assertEqual(no_messages.status_code, 200)
        self.assertEqual(bad_json.status_code, 400)
        self.assertIn("message", bad_json.json()["error"])

    # ── cancellation ──────────────────────────────────────────────────────────

    async def test_cancelling_a_live_stream_returns_499(self) -> None:
        token = "it-cancels"
        async with httpx.AsyncClient(base_url=BASE_URL, timeout=TIMEOUT) as client:
            generation, cancellation = await asyncio.gather(
                self._slow_completion(client, token),
                self._cancel_after(client, token, 1.0),
            )

        self.assertEqual(generation.status_code, 499)
        payload = generation.json()
        self.assertEqual(payload["error"]["code"], "client_cancelled")
        self.assertEqual(payload["de2generator"]["status"], "cancelled")
        self.assertEqual(cancellation.json()["status"], "cancelled")

        async with httpx.AsyncClient(base_url=BASE_URL, timeout=TIMEOUT) as client:
            gone = await client.post("/v1/cancel", json={"cancellation_token": token})
        self.assertEqual(gone.json()["status"], "not_found",
                         "the registration must be released afterwards")

    async def test_duplicate_cancellation_token_is_rejected(self) -> None:
        token = "shared-token"
        async with httpx.AsyncClient(base_url=BASE_URL, timeout=TIMEOUT) as client:
            first, second = await asyncio.gather(
                self._slow_completion(client, token),
                self._slow_completion(client, token),
                return_exceptions=True,
            )
        codes = sorted(r.status_code for r in (first, second)
                       if isinstance(r, httpx.Response))
        self.assertIn(409, codes, "the duplicate must be refused")

    async def test_cancellation_contract_for_unknown_and_malformed_tokens(self) -> None:
        async with httpx.AsyncClient(base_url=BASE_URL, timeout=TIMEOUT) as client:
            unknown = await client.post("/v1/cancel", json={"cancellation_token": "nope"})
            missing = await client.post("/v1/cancel", json={})
            empty = await client.post("/v1/cancel", json={"cancellation_token": "   "})
            not_a_string = await client.post("/v1/cancel", json={"cancellation_token": 7})

        self.assertEqual(unknown.status_code, 200)
        self.assertEqual(unknown.json()["status"], "not_found")
        for response in (missing, empty, not_a_string):
            self.assertEqual(response.status_code, 400)
            self.assertIn("message", response.json()["error"])

    async def _slow_completion(self, client: httpx.AsyncClient,
                               token: str) -> httpx.Response:
        return await self._complete(client, model="mock/slow", cancellation_token=token)

    async def _cancel_after(self, client: httpx.AsyncClient, token: str,
                            delay: float) -> httpx.Response:
        await asyncio.sleep(delay)
        return await client.post("/v1/cancel", json={"cancellation_token": token})


if __name__ == "__main__":
    unittest.main(verbosity=2)
