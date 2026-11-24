"""Message builders for recovery strategies."""

from typing import Optional

from core.constants import THINKING_CHARS_PER_TOKEN, THINKING_BUDGET_EXHAUSTION_THRESHOLD

MAX_INJECTED_THINKING_CHARS = 12000


def is_thinking_exhausted(thinking_text: str, response_text: str, thinking_budget_tokens: Optional[int] = None) -> bool:
    """Returns True when the model produced substantial thinking content but an empty response.
    
    This is the classic sign that the thinking budget was exhausted before a response
    token could be generated.
    
    If thinking_budget_tokens is provided (from chat_template_kwargs.thinking_budget),
    exhaustion is declared when thinking consumed >= 80% of the budget (estimated
    at ~4 chars/token). Without a budget, any non-trivial thinking (> 10 chars)
    paired with an empty response is flagged.
    
    Args:
        thinking_text: The thinking/reasoning content
        response_text: The response content
        thinking_budget_tokens: Optional thinking budget in tokens
    
    Returns:
        True if thinking budget was exhausted.
    """
    if len(response_text.strip()) > 0:
        return False
    thinking_chars = len(thinking_text.strip())
    if thinking_chars == 0:
        return False
    if thinking_budget_tokens is not None:
        thinking_token_estimate = thinking_chars / THINKING_CHARS_PER_TOKEN
        return thinking_token_estimate >= thinking_budget_tokens * THINKING_BUDGET_EXHAUSTION_THRESHOLD
    return True  # any thinking with empty response = exhausted


def build_continuation_messages(original_messages: list, checkpoint_text: str) -> list:
    """Append a continuation instruction to the original message list.
    
    Args:
        original_messages: Original conversation messages
        checkpoint_text: Text from last healthy checkpoint
    
    Returns:
        New message list with continuation instruction.
    """
    instruction = (
        "Continue from the following text.\n\n"
        "Do not repeat previous content.\n"
        "Advance the narrative.\n"
        "Introduce new information.\n"
        "Avoid rephrasing prior paragraphs.\n\n"
        f"{checkpoint_text}\n\n"
        "Append the new continuation to the output stream."
    )
    return list(original_messages) + [{"role": "user", "content": instruction}]


def build_thinking_resume_messages(original_messages: list, thinking_text: str) -> list:
    """Inject the interrupted thinking as context and instruct to conclude reasoning.
    
    Thinking remains enabled so the model can use a small amount of additional
    reasoning to reach a conclusion.
    
    Args:
        original_messages: Original conversation messages
        thinking_text: Interrupted thinking content
    
    Returns:
        New message list with thinking resume instruction.
    """
    injection = (
        f"[PREVIOUS REASONING — DO NOT REPEAT OR REDO]\n{thinking_text}\n\n"
        "[INSTRUCTION]\nYour reasoning above was interrupted before you could respond. "
        "Do NOT repeat or redo the reasoning. "
        "Instead, briefly conclude the reasoning above and then immediately produce the final response."
    )
    messages = [dict(m) for m in original_messages]
    if messages and messages[0].get("role") == "system":
        content = messages[0].get("content", "")
        if isinstance(content, str):
            messages[0]["content"] = content + "\n\n" + injection
        elif isinstance(content, list):
            messages[0]["content"] = list(content) + [{"type": "text", "text": injection}]
    else:
        messages.insert(0, {"role": "system", "content": injection})
    return messages


def build_nothink_retry_messages(original_messages: list, thinking_text: str) -> list:
    """Inject thinking as context and disable thinking entirely (last resort).
    
    Forces the model to produce only the final response.
    Uses multiple techniques:
    - Add "/no_think" directive in system message
    - Inject thinking as context
    - Add fake assistant message with <think></think> to signal thinking is done
    
    Args:
        original_messages: Original conversation messages
        thinking_text: Interrupted thinking content
    
    Returns:
        New message list with no-think retry configuration.
    """
    nothink_directive = "/no_think — Do not use <think>…</think> tags. Respond directly without internal reasoning."
    if len(thinking_text) > MAX_INJECTED_THINKING_CHARS:
        thinking_text = "[…earlier reasoning omitted…]\n" + thinking_text[-MAX_INJECTED_THINKING_CHARS:]
    injection = (
        f"[PREVIOUS INTERRUPTED REASONING]\n{thinking_text}\n\n"
        "[INSTRUCTION]\nYour previous reasoning was interrupted. "
        "Now generate ONLY the final response without using <think>…</think> tags. "
        "Do not repeat the reasoning above. Respond directly."
    )
    messages = [dict(m) for m in original_messages]
    if messages and messages[0].get("role") == "system":
        content = messages[0].get("content", "")
        if isinstance(content, str):
            messages[0]["content"] = nothink_directive + "\n\n" + content + "\n\n" + injection
        elif isinstance(content, list):
            messages[0]["content"] = [{"type": "text", "text": nothink_directive + "\n\n" + injection}] + list(content)
    else:
        messages.insert(0, {"role": "system", "content": nothink_directive + "\n\n" + injection})
    # No trailing assistant turn: a prefilled assistant message makes some models emit an empty reply.
    return messages


def build_nothink_request(request_data: dict, messages: list, thinking_text: str) -> dict:
    """Copy of the original request with thinking fully disabled and prior reasoning injected.

    Combines :func:`build_nothink_retry_messages` with the template-kwargs side of
    the switch: ``thinking_budget``/``reasoning_effort`` are dropped (they would
    re-enable thinking) and ``enable_thinking`` is forced off.
    """
    req = dict(request_data)
    req["messages"] = build_nothink_retry_messages(messages, thinking_text)
    ctk = {k: v for k, v in (request_data.get("chat_template_kwargs") or {}).items()
           if k not in ("thinking_budget", "reasoning_effort")}
    ctk["enable_thinking"] = False
    req["chat_template_kwargs"] = ctk
    req.pop("reasoning_effort", None)  # would otherwise re-enable thinking
    return req

