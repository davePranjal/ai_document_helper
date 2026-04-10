import json
import structlog
import time
from collections.abc import Generator

import anthropic

from app.config import settings

logger = structlog.get_logger(__name__)

_client: anthropic.Anthropic | None = None


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    return _client


def analyze_document(prompt: str) -> dict:
    """Send an analysis prompt to Claude and return parsed JSON response.

    Returns dict with: analysis (parsed JSON), token_usage, processing_time_seconds
    """
    client = _get_client()
    start = time.time()

    response = client.messages.create(
        model=settings.claude_model,
        max_tokens=4096,
        messages=[{"role": "user", "content": prompt}],
    )

    elapsed = time.time() - start
    response_text = response.content[0].text

    # Parse JSON from response
    try:
        # Handle case where Claude wraps JSON in markdown code blocks
        text = response_text.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
        analysis = json.loads(text)
    except json.JSONDecodeError:
        logger.error("Failed to parse Claude response as JSON: %s", response_text[:200])
        analysis = {
            "summary": response_text,
            "key_topics": [],
            "entities": {"people": [], "organizations": [], "locations": [], "dates": [], "other": []},
            "category": "other",
            "tags": [],
            "sentiment": "neutral",
            "language": "unknown",
            "confidence_score": 0.3,
        }

    token_usage = {
        "input_tokens": response.usage.input_tokens,
        "output_tokens": response.usage.output_tokens,
    }

    logger.info(
        "Claude analysis completed in %.1fs (input: %d, output: %d tokens)",
        elapsed, token_usage["input_tokens"], token_usage["output_tokens"],
    )

    return {
        "analysis": analysis,
        "token_usage": token_usage,
        "processing_time_seconds": round(elapsed, 2),
    }


def chat_completion(
    system_prompt: str,
    messages: list[dict],
    max_tokens: int = 2048,
) -> dict:
    """Send a chat completion request to Claude.

    Returns dict with: content, token_usage, processing_time_seconds
    """
    client = _get_client()
    start = time.time()

    response = client.messages.create(
        model=settings.claude_model,
        max_tokens=max_tokens,
        system=system_prompt,
        messages=messages,
    )

    elapsed = time.time() - start
    content = response.content[0].text

    token_usage = {
        "input_tokens": response.usage.input_tokens,
        "output_tokens": response.usage.output_tokens,
    }

    return {
        "content": content,
        "token_usage": token_usage,
        "processing_time_seconds": round(elapsed, 2),
    }


def chat_completion_stream(
    system_prompt: str,
    messages: list[dict],
    max_tokens: int = 2048,
) -> Generator[dict, None, None]:
    """Stream a chat completion from Claude. Yields event dicts.

    Event types: {"type": "chunk", "text": "..."} and
                 {"type": "done", "token_usage": {...}, "processing_time_seconds": float}
    """
    client = _get_client()
    start = time.time()

    with client.messages.stream(
        model=settings.claude_model,
        max_tokens=max_tokens,
        system=system_prompt,
        messages=messages,
    ) as stream:
        for text_chunk in stream.text_stream:
            yield {"type": "chunk", "text": text_chunk}

        # After stream completes, get final message for usage
        final = stream.get_final_message()

    elapsed = time.time() - start
    token_usage = {
        "input_tokens": final.usage.input_tokens,
        "output_tokens": final.usage.output_tokens,
    }

    yield {
        "type": "done",
        "token_usage": token_usage,
        "processing_time_seconds": round(elapsed, 2),
    }
