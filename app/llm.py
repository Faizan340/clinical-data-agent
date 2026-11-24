"""Gemini client wrapped in the harness: structured output, retries, rate limiting,
per-question call budget, token accounting and an optional on-disk response cache."""
import hashlib
import json
import threading
import time
from pathlib import Path
from typing import Type, TypeVar

from google import genai
from google.genai import errors, types
from pydantic import BaseModel
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from app.config import settings

T = TypeVar("T", bound=BaseModel)


class BudgetExceeded(RuntimeError):
    pass


def new_usage() -> dict:
    return {"llm_calls": 0, "cached_calls": 0, "input_tokens": 0, "output_tokens": 0}


# Response cache: identical (model, system prompt, prompt, schema) -> reuse the stored output.
# Used by evals so re-runs only spend free-tier quota on cases whose prompts actually changed.
CACHE_ENABLED = settings.llm_cache
CACHE_DIR = Path(__file__).resolve().parent.parent / ".llm_cache"


def _cache_path(system: str, prompt: str, schema: Type[BaseModel]) -> Path:
    key = json.dumps([settings.gemini_model, system, prompt, schema.model_json_schema()], sort_keys=True)
    return CACHE_DIR / f"{hashlib.sha256(key.encode()).hexdigest()}.json"


_lock = threading.Lock()
_last_call = 0.0


def _throttle():
    """Space calls out to stay under free-tier requests-per-minute limits."""
    global _last_call
    with _lock:
        wait = settings.llm_min_interval_s - (time.monotonic() - _last_call)
        if wait > 0:
            time.sleep(wait)
        _last_call = time.monotonic()


class DailyQuotaExhausted(RuntimeError):
    pass


def _is_retryable(exc: BaseException) -> bool:
    """Retry per-minute rate limits and transient errors; never retry a spent daily quota."""
    return (isinstance(exc, errors.APIError) and getattr(exc, "code", None) in (429, 500, 503)
            and "PerDay" not in str(exc))


_client = None


def _get_client():
    global _client
    if _client is None:
        if not settings.gemini_api_key:
            raise RuntimeError("GEMINI_API_KEY is not set. Copy .env.example to .env and add your key.")
        _client = genai.Client(api_key=settings.gemini_api_key)
    return _client


@retry(retry=retry_if_exception(_is_retryable), wait=wait_exponential(multiplier=2, min=4, max=60),
       stop=stop_after_attempt(5), reraise=True)
def _call(system: str, prompt: str, schema: Type[T]):
    _throttle()
    return _get_client().models.generate_content(
        model=settings.gemini_model,
        contents=prompt,
        config=types.GenerateContentConfig(
            system_instruction=system,
            temperature=0,
            response_mime_type="application/json",
            response_schema=schema,
        ),
    )


def generate(system: str, prompt: str, schema: Type[T], usage: dict) -> tuple[T, dict]:
    """One budgeted, structured LLM call. Returns (parsed_output, updated_usage)."""
    cache_file = _cache_path(system, prompt, schema) if CACHE_ENABLED else None
    if cache_file and cache_file.exists():
        cached = json.loads(cache_file.read_text(encoding="utf-8"))
        return schema.model_validate(cached["output"]), {**usage, "cached_calls": usage["cached_calls"] + 1}

    if usage["llm_calls"] >= settings.max_llm_calls:
        raise BudgetExceeded(f"LLM call budget of {settings.max_llm_calls} per question exhausted")
    try:
        response = _call(system, prompt, schema)
    except errors.APIError as e:
        if "PerDay" in str(e):
            raise DailyQuotaExhausted(f"Gemini free-tier daily quota for {settings.gemini_model} is used up; "
                                      "it resets daily (see https://ai.dev/rate-limit).") from e
        raise
    meta = response.usage_metadata
    usage = {
        **usage,
        "llm_calls": usage["llm_calls"] + 1,
        "input_tokens": usage["input_tokens"] + (meta.prompt_token_count or 0),
        "output_tokens": usage["output_tokens"] + (meta.candidates_token_count or 0)
                         + (getattr(meta, "thoughts_token_count", 0) or 0),
    }
    parsed = response.parsed
    if parsed is None:  # model returned JSON that didn't match the schema
        parsed = schema.model_validate_json(response.text)
    if cache_file:
        CACHE_DIR.mkdir(exist_ok=True)
        cache_file.write_text(json.dumps({"output": parsed.model_dump()}), encoding="utf-8")
    return parsed, usage
