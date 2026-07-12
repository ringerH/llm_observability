import pytest
import random
from unittest.mock import MagicMock
from eval_harness.config import settings
from eval_harness import client, scorers

def test_retry_non_transient_error(monkeypatch):
    """
    Validates that:
    1. A client error (HTTP 400) is NOT retried (fail-fast on non-transient errors).
    2. When the call fails, the exception is propagated instead of being swallowed.
    """
    import httpx
    
    orig_key = settings.GEMINI_API_KEY
    settings.GEMINI_API_KEY = "dummy_api_key_value_long_enough"

    call_count = 0
    def mock_post(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        resp = MagicMock()
        resp.status_code = 400
        resp.text = "Bad Request (Client Error)"
        return resp

    monkeypatch.setattr(httpx.Client, "post", mock_post)

    # Call should raise LLMApiError immediately on first try without swallowing
    with pytest.raises(client.LLMApiError):
        client._call_gemini_api("test prompt", response_json=False, timeout_sec=5.0)
    
    settings.GEMINI_API_KEY = orig_key

    # Assert it was NOT retried (only called 1 time)
    assert call_count == 1, f"Expected 1 call (no retries) for HTTP 400, got {call_count}"

def test_global_random_seed_pollution():
    """
    Validates that calling LlmJudgeScorer.score pollutes/resets the global random generator.
    """
    # 1. Establish a known global random state
    random.seed(42)
    first_val = random.random()

    # 2. Call the scorer which internally seeds the global generator with a hash seed
    scorer = scorers.LlmJudgeScorer(trials=3, mock=True)
    scorer.score("user input text", "assistant output text")

    # 3. Retrieve the next random number from the polluted state
    second_val = random.random()

    # 4. Compare with the expected second value if state hadn't been modified
    random.seed(42)
    _ = random.random()
    expected_second_val = random.random()

    # Assert that the global random state WAS mutated (the values do not match)
    assert second_val != expected_second_val, "Expected global random generator state to be mutated under current implementation"
