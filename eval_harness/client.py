import httpx
import time
from typing import Dict, Any, Optional
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type, before_sleep_log, retry_if_exception
from eval_harness.config import settings
from eval_harness import logging

# Custom typed exceptions
class LLMError(Exception):
    """Base exception for all LLM client failures."""
    pass

class LLMTimeoutError(LLMError):
    """Raised when an LLM API call times out."""
    pass

class LLMRateLimitError(LLMError):
    """Raised when the LLM provider rate limits the request (HTTP 429)."""
    pass

class LLMApiError(LLMError):
    """Raised when the LLM provider returns a non-200 server/client error."""
    pass


def _should_retry_exception(exception: Exception) -> bool:
    """
    Decides whether an exception is transient and should be retried.
    Retries on:
    - HTTP 429 (RateLimit)
    - HTTP 5xx (Server error)
    - Network timeouts
    """
    if isinstance(exception, LLMRateLimitError):
        return True
    if isinstance(exception, LLMTimeoutError):
        return True
    if isinstance(exception, LLMApiError):
        # Retry only on server errors (status 5xx)
        # We can extract the status from the error message or store it.
        # For simplicity, if the API error states a 5xx code, retry.
        msg = str(exception)
        return "50" in msg or "502" in msg or "503" in msg or "504" in msg
    return False


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    retry=retry_if_exception(lambda e: isinstance(e, LLMError) and _should_retry_exception(e)),
    reraise=True,
    before_sleep=before_sleep_log(logging.get_logger(), logging.logging.WARNING)
)
def _call_gemini_api(prompt: str, response_json: bool, timeout_sec: float) -> str:
    """
    Invokes the Google Gemini Developer API.
    """
    if not settings.GEMINI_API_KEY:
        raise LLMApiError("GEMINI_API_KEY is not configured in settings.")

    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-3.5-flash:generateContent?key={settings.GEMINI_API_KEY}"
    
    headers = {"Content-Type": "application/json"}
    
    payload = {
        "contents": [{
            "parts": [{"text": prompt}]
        }]
    }
    
    if response_json:
        payload["generationConfig"] = {"responseMimeType": "application/json"}

    try:
        with httpx.Client(timeout=timeout_sec) as client:
            response = client.post(url, headers=headers, json=payload)
            
            if response.status_code == 429:
                raise LLMRateLimitError("Gemini API rate limit reached (HTTP 429)")
            elif response.status_code >= 500:
                raise LLMApiError(f"Gemini server error (HTTP {response.status_code}): {response.text}")
            elif response.status_code != 200:
                raise LLMApiError(f"Gemini client error (HTTP {response.status_code}): {response.text}")
                
            data = response.json()
            # Extract content from response
            try:
                candidate = data["candidates"][0]
                text = candidate["content"]["parts"][0]["text"]
                return text.strip()
            except (KeyError, IndexError) as parse_err:
                raise LLMApiError(f"Malformed Gemini API response: {data}") from parse_err

    except httpx.TimeoutException as exc:
        raise LLMTimeoutError(f"Gemini API call timed out after {timeout_sec}s") from exc
    except httpx.RequestError as exc:
        raise LLMApiError(f"Gemini API request failed: {str(exc)}") from exc


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    retry=retry_if_exception(lambda e: isinstance(e, LLMError) and _should_retry_exception(e)),
    reraise=True,
    before_sleep=before_sleep_log(logging.get_logger(), logging.logging.WARNING)
)
def _call_openai_api(prompt: str, response_json: bool, timeout_sec: float) -> str:
    """
    Invokes the OpenAI Chat Completions API.
    """
    if not settings.OPENAI_API_KEY:
        raise LLMApiError("OPENAI_API_KEY is not configured in settings.")

    url = "https://api.openai.com/v1/chat/completions"
    
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {settings.OPENAI_API_KEY}"
    }
    
    payload = {
        "model": "gpt-4o-mini",
        "messages": [
            {"role": "user", "content": prompt}
        ]
    }
    
    if response_json:
        payload["response_format"] = {"type": "json_object"}

    try:
        with httpx.Client(timeout=timeout_sec) as client:
            response = client.post(url, headers=headers, json=payload)
            
            if response.status_code == 429:
                raise LLMRateLimitError("OpenAI API rate limit reached (HTTP 429)")
            elif response.status_code >= 500:
                raise LLMApiError(f"OpenAI server error (HTTP {response.status_code}): {response.text}")
            elif response.status_code != 200:
                raise LLMApiError(f"OpenAI client error (HTTP {response.status_code}): {response.text}")
                
            data = response.json()
            try:
                text = data["choices"][0]["message"]["content"]
                return text.strip()
            except (KeyError, IndexError) as parse_err:
                raise LLMApiError(f"Malformed OpenAI API response: {data}") from parse_err

    except httpx.TimeoutException as exc:
        raise LLMTimeoutError(f"OpenAI API call timed out after {timeout_sec}s") from exc
    except httpx.RequestError as exc:
        raise LLMApiError(f"OpenAI API request failed: {str(exc)}") from exc


def call_llm(
    prompt: str,
    provider: Optional[str] = None,
    response_json: bool = False,
    timeout_sec: float = 12.0
) -> str:
    """
    Orchestrates calling the configured LLM API.
    Defaults to Gemini if provider not specified.
    """
    if not provider:
        provider = "gemini" if settings.GEMINI_API_KEY else "openai"

    if provider == "gemini":
        return _call_gemini_api(prompt, response_json, timeout_sec)
    elif provider == "openai":
        return _call_openai_api(prompt, response_json, timeout_sec)
    else:
        raise ValueError(f"Unknown LLM provider: {provider}")
