import pytest
import os
import sqlite3
from unittest.mock import patch, MagicMock
from eval_harness.config import settings
from eval_harness import database, sampler, worker, alerts

@pytest.fixture(autouse=True)
def test_db_setup(tmp_path):
    test_db = str(tmp_path / "test_eval.db")
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

def test_canary_injection():
    config_hash = "canary_test_config"
    database.save_prompt_config(config_hash, "Template", "gemini-1.5-flash", {"rules": {"json_format": {}}})
    
    # 1. Inject a canary
    req_id = sampler.inject_canary_record(config_hash, canary_id="canary_json_fail")
    assert req_id is not None
    assert req_id.startswith("canary_")
    
    # Verify in DB
    with database.get_db_conn() as conn:
        row = conn.execute(
            "SELECT is_canary, canary_id, input_data FROM production_traffic WHERE request_id = ?",
            (req_id,)
        ).fetchone()
        assert row is not None
        assert row["is_canary"] == 1
        assert row["canary_id"] == "canary_json_fail"
        assert "products" in row["input_data"]

def test_canary_processing_success():
    config_hash = "canary_test_config"
    database.save_prompt_config(config_hash, "Template", "gemini-1.5-flash", {"rules": {"json_format": {}}})
    
    # Inject JSON fail canary (malformed JSON)
    req_id = sampler.inject_canary_record(config_hash, canary_id="canary_json_fail")
    
    # Process using process_single_request
    # For canary_json_fail, expected JSON score is 0.0. The actual score of the malformed output will be 0.0.
    # Therefore, no mismatch should occur and no alert should be triggered.
    with patch("eval_harness.alerts.fire_monitor_broken_alert") as mock_alert:
        with database.get_db_conn() as conn:
            req = conn.execute("SELECT * FROM production_traffic WHERE request_id = ?", (req_id,)).fetchone()
        worker.process_single_request(dict(req))
        
        # Verify scores written
        with database.get_db_conn() as conn:
            score = conn.execute("SELECT score FROM production_scores WHERE request_id = ?", (req_id,)).fetchone()
            assert score is not None
            assert score["score"] == 0.0
            
        mock_alert.assert_not_called()

def test_canary_processing_mismatch_alerts():
    config_hash = "canary_test_config"
    database.save_prompt_config(config_hash, "Template", "gemini-1.5-flash", {"rules": {"json_format": {}}})
    
    # Inject JSON fail canary, but let's mock the actual output to be VALID json (which triggers a mismatch!)
    # Expectation: Expected score = 0.0, but actual score will be 1.0 (valid JSON).
    # This indicates the monitor is failing to correctly flag a bad canary!
    from eval_harness.canary_lib import get_canary_by_id
    canary = get_canary_by_id("canary_json_fail")
    
    request_id = "canary_mismatch_test"
    with database.get_db_conn() as conn:
        conn.execute(
            """
            INSERT INTO production_traffic (request_id, config_hash, input_data, actual_output, latency_ms, cost, is_canary, canary_id)
            VALUES (?, ?, ?, ?, ?, ?, 1, ?)
            """,
            (request_id, config_hash, canary["input_data"], '{"status": "valid_json"}', 120.0, 0.000075, "canary_json_fail")
        )
        
    with patch("eval_harness.alerts.fire_monitor_broken_alert") as mock_alert:
        with database.get_db_conn() as conn:
            req = conn.execute("SELECT * FROM production_traffic WHERE request_id = ?", (request_id,)).fetchone()
        worker.process_single_request(dict(req))
        
        # Alert should be triggered because expected score (0.0) != actual score (1.0)
        mock_alert.assert_called_once_with(
            canary_id="canary_json_fail",
            category="should_flag_json",
            metric_name="json_format",
            expected_score=0.0,
            actual_score=1.0
        )

def test_canary_metrics_calculation():
    config_hash = "canary_test_config"
    database.save_prompt_config(config_hash, "Template", "gemini-1.5-flash", {"rules": {"json_format": {}}})
    
    # Inject and process one failing canary (correctly caught)
    req1 = sampler.inject_canary_record(config_hash, canary_id="canary_json_fail")
    with database.get_db_conn() as conn:
        r1 = conn.execute("SELECT * FROM production_traffic WHERE request_id = ?", (req1,)).fetchone()
    worker.process_single_request(dict(r1))
    
    # Inject and process one clean canary (correctly passed)
    req2 = sampler.inject_canary_record(config_hash, canary_id="canary_clean_pass")
    with database.get_db_conn() as conn:
        r2 = conn.execute("SELECT * FROM production_traffic WHERE request_id = ?", (req2,)).fetchone()
    worker.process_single_request(dict(r2))
    
    # Get metrics
    recall, fpr, count = database.get_canary_health_metrics()
    assert count == 2
    assert recall == 1.0 # 1 out of 1 caught
    assert fpr == 0.0 # 0 out of 1 flagged
