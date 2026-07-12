import pytest
import os
import json
from unittest.mock import patch
from eval_harness.config import settings
from eval_harness import database, alerts

@pytest.fixture(autouse=True)
def test_db_setup(tmp_path):
    """
    Fixture that redirects the database path to a temporary file
    and initializes the database schema before each test.
    """
    test_db = str(tmp_path / "test_eval_alerts.db")
    original_path = settings.DATABASE_PATH
    settings.DATABASE_PATH = test_db
    database.init_db()
    yield test_db
    settings.DATABASE_PATH = original_path
    if os.path.exists(test_db):
        try:
            os.remove(test_db)
        except OSError:
            pass

def test_rolling_regression_rate():
    config_hash = "config_hash_alert"
    # Create configuration first
    database.save_prompt_config(config_hash, "Template", "gpt-mock", {"rules": {"length_check": {"min": 5}}})

    # 1. No baseline: rate should be 0.0
    rate = database.get_rolling_regression_rate(limit=10, threshold=0.05)
    assert rate == 0.0

    # 2. Add baseline run
    database.initialize_run("run_base", config_hash, is_baseline=True)
    database.save_test_case("c1", "input")
    database.save_case_result("run_base", "c1", "output_val", 100.0, 0.0)
    database.save_metric("run_base", "c1", "length_check", "RULE", 1.0, "Pass", "SUCCESS")
    database.update_run_status("run_base", "COMPLETED", {"total_cases": 1, "passed": 1, "failed": 0, "avg_score": 1.0, "cost": 0.0, "duration_ms": 100.0})

    # Add production traffic with no degradation (score = 1.0)
    with database.get_db_conn() as conn:
        conn.execute(
            """
            INSERT INTO production_traffic (request_id, config_hash, input_data, actual_output, latency_ms, cost)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            ("req_ok", config_hash, "input", "output_val", 100.0, 0.0)
        )
    database.save_production_score("req_ok", "length_check", "RULE", 1.0, "Pass", "SUCCESS")

    # Regression rate should be 0.0 (1.0 - 1.0 = 0.0 <= 0.05)
    rate = database.get_rolling_regression_rate(limit=10, threshold=0.05)
    assert rate == 0.0

    # Add production traffic with degradation (score = 0.5, degradation = 1.0 - 0.5 = 0.5 > 0.05)
    with database.get_db_conn() as conn:
        conn.execute(
            """
            INSERT INTO production_traffic (request_id, config_hash, input_data, actual_output, latency_ms, cost)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            ("req_degraded", config_hash, "input", "short", 100.0, 0.0)
        )
    database.save_production_score("req_degraded", "length_check", "RULE", 0.5, "Degraded", "SUCCESS")

    # We now have 2 requests, 1 degraded. Regression rate = 1 / 2 = 0.5
    rate = database.get_rolling_regression_rate(limit=10, threshold=0.05)
    assert rate == 0.5

def test_webhook_alert_trigger():
    orig_url = settings.ALERT_WEBHOOK_URL
    settings.ALERT_WEBHOOK_URL = None

    # 1. No webhook configured: returns False
    assert not alerts.fire_webhook_alert(0.1, 0.05)

    settings.ALERT_WEBHOOK_URL = "http://localhost:8000/alert-webhook"

    # 2. Below threshold: returns False
    assert not alerts.fire_webhook_alert(0.02, 0.05)

    # 3. Above threshold: triggers POST
    with patch("httpx.post") as mock_post:
        # Mock successful POST response
        mock_response = mock_post.return_value
        mock_response.status_code = 200
        mock_response.text = "OK"

        fired = alerts.fire_webhook_alert(0.1, 0.05)
        assert fired
        mock_post.assert_called_once()
        
        # Verify JSON payload structure
        call_kwargs = mock_post.call_args[1]
        assert call_kwargs["json"]["event"] == "REGRESSION_ALERT"
        assert call_kwargs["json"]["rolling_regression_rate"] == 0.1
        assert call_kwargs["json"]["threshold"] == 0.05

    # Clean up settings
    settings.ALERT_WEBHOOK_URL = orig_url
