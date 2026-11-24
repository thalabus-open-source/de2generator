"""Tests for normalizing model streaming response formats."""

import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import httpx

SRC_DIR = Path(__file__).resolve().parents[1]
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from streaming import vllm_client


class StreamingReasoningTests(unittest.IsolatedAsyncioTestCase):
    async def test_native_reasoning_field_variants_are_extracted(self) -> None:
        responses = {
            "reasoning": "Qwen 3.5 reasoning",
            "reasoning_content": "Qwen 3.8 reasoning",
        }

        for field, expected in responses.items():
            with self.subTest(field=field):
                event = {
                    "choices": [
                        {
                            "delta": {field: expected},
                            "finish_reason": None,
                        }
                    ]
                }
                sse_body = f"data: {json.dumps(event)}\n\ndata: [DONE]\n\n"

                async def handler(request: httpx.Request) -> httpx.Response:
                    return httpx.Response(200, text=sse_body)

                original_async_client = httpx.AsyncClient
                fake_httpx = SimpleNamespace(
                    AsyncClient=lambda **kwargs: original_async_client(
                        transport=httpx.MockTransport(handler), **kwargs
                    ),
                    Timeout=httpx.Timeout,
                )
                with patch.object(vllm_client, "httpx", fake_httpx):
                    chunks = [
                        chunk
                        async for chunk in vllm_client.stream_from_vllm(
                                {"chat_template_kwargs": {}}, "test-session", "http://model"
                        )
                    ]

                self.assertEqual(chunks[0][1], expected)
                self.assertEqual(chunks[0][0], "")

    async def test_inline_think_tags_remain_supported(self) -> None:
        event = {
            "choices": [
                {
                    "delta": {"content": "<think>legacy reasoning</think>answer"},
                    "finish_reason": None,
                }
            ]
        }
        sse_body = f"data: {json.dumps(event)}\n\ndata: [DONE]\n\n"

        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text=sse_body)

        original_async_client = httpx.AsyncClient
        fake_httpx = SimpleNamespace(
            AsyncClient=lambda **kwargs: original_async_client(
                transport=httpx.MockTransport(handler), **kwargs
            ),
            Timeout=httpx.Timeout,
        )
        with patch.object(vllm_client, "httpx", fake_httpx):
            chunks = [
                chunk
                async for chunk in vllm_client.stream_from_vllm(
                    {"chat_template_kwargs": {}}, "test-session", "http://model"
                )
            ]

        self.assertEqual(chunks[0][1], "legacy reasoning")
        self.assertEqual(chunks[0][0], "answer")


if __name__ == "__main__":
    unittest.main()