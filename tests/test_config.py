import pytest
from pydantic import ValidationError
from eval_harness.config import Settings

def test_valid_config():
    # Verify that standard valid config doesn't raise error
    settings = Settings(
        GEMINI_API_KEY="AIzaSyA1234567890BCDEF_GHIJKLM-NOP",
        OPENAI_API_KEY="sk-proj-123456789012345678901234567890",
        SAMPLING_RATE=0.2,
        MAX_SAMPLING_RATE=0.6,
        REGRESSION_THRESHOLD=0.02
    )
    assert settings.SAMPLING_RATE == 0.2
    assert settings.DATABASE_PATH == "eval.db"

def test_placeholder_api_keys():
    # Verify that placeholder values trigger validation errors
    with pytest.raises(ValidationError) as exc_info:
        Settings(GEMINI_API_KEY="CHANGE_ME_KEY")
    assert "contains a placeholder value" in str(exc_info.value)

    with pytest.raises(ValidationError) as exc_info:
        Settings(OPENAI_API_KEY="YOUR_KEY")
    assert "contains a placeholder value" in str(exc_info.value)

def test_suspiciously_short_api_keys():
    # Verify that short keys are flagged
    with pytest.raises(ValidationError) as exc_info:
        Settings(GEMINI_API_KEY="short")
    assert "suspiciously short" in str(exc_info.value)

def test_out_of_bounds_sampling_rate():
    # Verify that sampling rates outside [0, 1] are rejected
    with pytest.raises(ValidationError) as exc_info:
        Settings(SAMPLING_RATE=1.5)
    assert "must be between 0.0 and 1.0" in str(exc_info.value)

    with pytest.raises(ValidationError) as exc_info:
        Settings(MAX_SAMPLING_RATE=-0.1)
    assert "must be between 0.0 and 1.0" in str(exc_info.value)

def test_invalid_regression_threshold():
    # Verify that negative threshold is rejected
    with pytest.raises(ValidationError) as exc_info:
        Settings(REGRESSION_THRESHOLD=-0.05)
    assert "must be non-negative" in str(exc_info.value)
