"""
Async translation service using Hy-MT2 (or compatible) via OpenAI-compatible API.

- Submits checkpoint text for translation in background threads (parallel to semantic check)
- Uses content-type-aware prompts per Hy-MT2 documentation
- Waits for all translations after main generation completes
- Falls back to original content on timeout or any error
"""

import asyncio
import json
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Dict, Optional

import httpx

from core.config import get_config_value
from utils import get_logger

log = get_logger()

# ── Language code → full name map (Hy-MT2 supported languages) ──────────────
LANG_CODE_TO_NAME: Dict[str, str] = {
    "ZH": "Chinese", "EN": "English", "FR": "French", "PT": "Portuguese",
    "ES": "Spanish", "JA": "Japanese", "TR": "Turkish", "RU": "Russian",
    "AR": "Arabic",  "KO": "Korean",   "TH": "Thai",   "IT": "Italian",
    "DE": "German",  "VI": "Vietnamese","MS": "Malay",  "ID": "Indonesian",
    "TL": "Filipino","HI": "Hindi",
    "ZH-HANT": "Traditional Chinese",
    "PL": "Polish",  "CS": "Czech",    "NL": "Dutch",  "KM": "Khmer",
    "MY": "Burmese", "FA": "Persian",  "GU": "Gujarati","UR": "Urdu",
    "TE": "Telugu",  "MR": "Marathi",  "HE": "Hebrew", "BN": "Bengali",
    "TA": "Tamil",   "UK": "Ukrainian","BO": "Tibetan", "KK": "Kazakh",
    "MN": "Mongolian","UG": "Uyghur", "YUE": "Cantonese",
}


def _lang_name(code: str) -> str:
    """Return full language name for a 2-char (or longer) language code."""
    return LANG_CODE_TO_NAME.get(code.upper(), code)


def _build_prompt(text: str, target_lang_name: str, content_type: str) -> str:
    """
    Build the translation prompt matching Hy-MT2 documentation examples,
    using the appropriate template per content type.

    Templates from https://huggingface.co/tencent/Hy-MT2-7B-FP8:
      - text:       Default Translation
      - json/xml:   Structured Data (preserve keys, translate visible values only)
      - html:       Structured Data (preserve tags/attributes, translate visible text)
    """
    if content_type in ("json", "xml"):
        format_type = content_type.upper()
        return (
            f"# Task Goal\n"
            f"Translate the {format_type} formatted data below into {target_lang_name}.\n"
            f"# Strict Constraints\n"
            f"1. Structure Lock: Absolutely preserve the original {format_type} data structure, indentation and hierarchy unchanged.\n"
            f"2. Selective Translation: Only translate user-visible text content (string values). Do NOT translate keys, tag names, attribute names, or code.\n"
            f"3. Forbidden Modifications: Do not translate or change any code labels, key names, variable placeholders.\n"
            f"4. Output ONLY the translated {format_type}. No extra explanation.\n\n"
            f"{text}"
        )
    elif content_type == "html":
        return (
            f"# Task Goal\n"
            f"Translate the HTML content below into {target_lang_name}.\n"
            f"# Strict Constraints\n"
            f"1. Structure Lock: Absolutely preserve all HTML tags, attributes, and nesting unchanged.\n"
            f"2. Selective Translation: Only translate visible text between tags. Do NOT translate attribute values, class names, ids, or any HTML syntax.\n"
            f"3. Output ONLY the translated HTML. No extra explanation.\n\n"
            f"{text}"
        )
    else:
        # Default plain text translation
        return (
            f"Translate the following text into {target_lang_name}. "
            f"Note that you should only output the translated result without any additional explanation:\n\n"
            f"{text}"
        )


def _translate_sync(
    text: str,
    target_lang_code: str,
    content_type: str,
    request_id: str,
    cp_idx: int,
    endpoint: str,
    model: str,
    timeout: float,
) -> Optional[tuple[str, int]]:
    """
    Blocking translation call. Runs inside a thread-pool worker.
    Returns (translated_text, elapsed_ms) or None on failure/timeout.
    """
    print(f"[DEBUG] _translate_sync START: cp[{cp_idx}], lang={target_lang_code}, endpoint={endpoint}, model={model}", flush=True)
    if not text.strip():
        print(f"[DEBUG] Text is empty for cp[{cp_idx}], skipping", flush=True)
        return (text, 0)

    target_lang_name = _lang_name(target_lang_code)
    print(f"[DEBUG] Target language: {target_lang_code} = {target_lang_name}", flush=True)
    prompt = _build_prompt(text, target_lang_name, content_type)
    print(f"[DEBUG] Built prompt for cp[{cp_idx}], prompt length: {len(prompt)}", flush=True)

    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.7,
        "top_p": 0.6,
        "top_k": 20,
        "repetition_penalty": 1.05,
        "max_tokens": 4096,
    }

    t0 = time.time()
    try:
        print(f"[DEBUG] Making HTTP request to {endpoint}", flush=True)
        with httpx.Client(timeout=timeout) as client:
            resp = client.post(endpoint, json=payload)
            print(f"[DEBUG] HTTP response status: {resp.status_code}", flush=True)
            resp.raise_for_status()
            data = resp.json()
            translated = data["choices"][0]["message"]["content"].strip()
            elapsed = time.time() - t0
            elapsed_ms = int(elapsed * 1000)
            print(f"[DEBUG] Translation succeeded: {len(text)} chars → {len(translated)} chars in {elapsed:.2f}s", flush=True)
            log.info(f"[{request_id}] Translation cp[{cp_idx}] → {target_lang_code} in {elapsed:.2f}s ({len(text)} chars → {len(translated)} chars)")
            return (translated, elapsed_ms)
    except httpx.TimeoutException:
        elapsed = time.time() - t0
        print(f"[DEBUG] TIMEOUT error for cp[{cp_idx}] after {elapsed:.2f}s", flush=True)
        log.warning(f"[{request_id}] cp[{cp_idx}] translation timeout ({elapsed:.2f}s)")
        return None
    except httpx.HTTPError as e:
        elapsed = time.time() - t0
        print(f"[DEBUG] HTTP error for cp[{cp_idx}]: {e}", flush=True)
        log.warning(f"[{request_id}] cp[{cp_idx}] HTTP error: {e}")
        return None
    except Exception as e:
        elapsed = time.time() - t0
        print(f"[DEBUG] Unexpected error for cp[{cp_idx}]: {e}", flush=True)
        log.error(f"[{request_id}] cp[{cp_idx}] translation error: {e}")
        return None


class TranslationService:
    """
    Manages async translation of content checkpoints.

    Usage:
      svc = TranslationService(request_id, target_lang_code)
      svc.submit(cp_idx, text, content_type)   # non-blocking, called per checkpoint
      results = await svc.wait_all()            # called after generation is complete
                                                # returns dict{cp_idx -> translated_text}
                                                # or None if any translation failed/timed out
    """

    def __init__(self, request_id: str, target_lang_code: str) -> None:
        self.request_id = request_id
        self.target_lang_code = target_lang_code.upper()
        # Read current config values for translation
        self.endpoint = get_config_value("translation.endpoint", default="")
        self.model = get_config_value("translation.model", default="tencent/Hy-MT2-7B-FP8")
        self.timeout = float(get_config_value("translation.timeout_seconds", default=15.0))
        self._executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="translator")
        self._futures: Dict[int, "asyncio.Future[Optional[tuple[str, int]]]"] = {}
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._shutdown = False

    def submit(self, cp_idx: int, text: str, content_type: str) -> None:
        """Submit a checkpoint for async translation. Non-blocking."""
        if self._shutdown:
            return
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            log.warning(f"[{self.request_id}] TranslationService.submit called outside event loop — skipping cp[{cp_idx}]")
            return
        self._loop = loop
        future = loop.run_in_executor(
            self._executor,
            _translate_sync,
            text,
            self.target_lang_code,
            content_type,
            self.request_id,
            cp_idx,
            self.endpoint,
            self.model,
            self.timeout,
        )
        self._futures[cp_idx] = future  # type: ignore[assignment]

    async def wait_all(self) -> Optional[Dict[int, tuple[str, int]]]:
        """
        Await all pending translations.

        Returns:
          dict{cp_idx -> (translated_text, elapsed_ms)} if ALL translations succeeded,
          None if any translation timed out or failed (caller should use originals).
        """
        if not self._futures:
            return {}

        print(f"[DEBUG] TranslationService.wait_all(): waiting for {len(self._futures)} translations", flush=True)
        results: Dict[int, tuple[str, int]] = {}
        any_failed = False

        for cp_idx, future in self._futures.items():
            try:
                result = await asyncio.wait_for(
                    asyncio.shield(future),  # type: ignore[arg-type]
                    timeout=self.timeout + 2.0,  # small extra margin
                )
                if result is None:
                    print(f"[DEBUG] Checkpoint {cp_idx} translation returned None", flush=True)
                    any_failed = True
                else:
                    print(f"[DEBUG] Checkpoint {cp_idx} translated successfully ({len(result[0])} chars)", flush=True)
                    results[cp_idx] = result
            except asyncio.TimeoutError:
                print(f"[DEBUG] Checkpoint {cp_idx} translation TIMEOUT after {self.timeout + 2.0}s", flush=True)
                log.warning(f"[{self.request_id}] wait_all: cp[{cp_idx}] still pending after deadline — falling back to originals")
                any_failed = True
            except Exception as exc:
                print(f"[DEBUG] Checkpoint {cp_idx} translation ERROR: {exc}", flush=True)
                log.warning(f"[{self.request_id}] wait_all: cp[{cp_idx}] error: {exc} — falling back to originals")
                any_failed = True

        self._executor.shutdown(wait=False)

        if any_failed:
            print(f"[DEBUG] Translation FAILED for some checkpoints - returning None", flush=True)
            log.warning(f"[{self.request_id}] Translation incomplete — returning original content for all checkpoints")
            return None

        print(f"[DEBUG] All {len(results)} translations complete", flush=True)
        log.info(f"[{self.request_id}] All {len(results)} checkpoint translations complete")
        return results

    def shutdown(self) -> None:
        """Clean up executor."""
        if self._shutdown:
            return
        self._shutdown = True
        for future in self._futures.values():
            future.cancel()
        self._executor.shutdown(wait=False, cancel_futures=True)
