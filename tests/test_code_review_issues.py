import pytest
import random
from unittest.mock import MagicMock
from eval_harness.config import settings
from eval_harness import client, scorers

def test_retry_non_transient_error(monkeypatch):
    """
    Validates the two bugs in client.py:
    1. A client error (HTTP 400) is unnecessarily retried 3 times (non-transient error retry bug).
    2. When the retries fail, the exception is swallowed and None is returned (callback swallow bug).
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

    # Call should succeed returning None (due to retry_error_callback swallowing the exception)
    res = client._call_gemini_api("test prompt", response_json=False, timeout_sec=5.0)
    
    settings.GEMINI_API_KEY = orig_key

    # Assert exception was swallowed (returned None)
    assert res is None, "Expected exception to be swallowed due to retry_error_callback returning None"
    
    # Assert it was retried 3 times instead of failing immediately
    assert call_count == 3, f"Expected 3 retry calls for HTTP 400 under current implementation, got {call_count}"

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
