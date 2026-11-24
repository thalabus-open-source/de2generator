"""Wait for pending checkpoint translations and push them to the UI.

The supervisor calls :func:`apply_translations` right before returning, so the
client either gets a fully translated answer or the original one — never a mix.
"""

from typing import Optional

from core.models import GenerationSession
from ui import ui_update_checkpoint_translation
from utils import get_logger

from .translator import TranslationService

log = get_logger()


async def apply_translations(session: GenerationSession,
                            translation_svc: Optional[TranslationService]) -> None:
    """
    Wait for all pending checkpoint translations.
    If all succeed: store in session.translated_checkpoints and push SSE updates.
    If any fail/timeout: leave session.translated_checkpoints empty (caller uses originals).
    """
    if translation_svc is None:
        log.debug(f"[{session.request_id}] Translation: no service for this request, skipping")
        return

    log.debug(f"[{session.request_id}] Translation: waiting for pending translations")
    results = await translation_svc.wait_all()
    if not results:
        log.debug(f"[{session.request_id}] Translation: no results, keeping original content")
        return

    # results is dict{cp_idx -> (translated_text, elapsed_ms)}
    session.translated_checkpoints = {idx: text for idx, (text, _) in results.items()}
    for cp_idx, (translated_text, elapsed_ms) in results.items():
        ui_update_checkpoint_translation(
            session.request_id, cp_idx, translated_text, session.translation_language, elapsed_ms
        )
    log.info(f"[{session.request_id}] Translation applied to {len(results)} checkpoints")
