import pytest
import os
import json
import sqlite3
from unittest.mock import MagicMock
from eval_harness.config import settings
from eval_harness import database, sampler, worker

@pytest.fixture(autouse=True)
def test_db_setup(tmp_path):
    """
    Fixture that redirects the database path to a temporary file
    and initializes the database schema before each test.
    """
    test_db = str(tmp_path / "test_eval_production.db")
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

def test_pii_masking():
    # Test email masking
    assert sampler.mask_pii("Contact me at user@example.com.") == "Contact me at [EMAIL]."
    # Test phone masking
    assert sampler.mask_pii("Call 123-456-7890 or +1-800-555-1212") == "Call [PHONE] or [PHONE]"
    # Test credit card masking
    assert sampler.mask_pii("Card number: 1234-5678-9012-3456.") == "Card number: [CREDIT_CARD]."
    # Test SSN masking
    assert sampler.mask_pii("My SSN is 000-12-3456.") == "My SSN is [SSN]."
    # Test no PII
    assert sampler.mask_pii("Just standard text.") == "Just standard text."

def test_sampler_kill_switch_and_rates():
    # Save original settings
    orig_kill = settings.SAMPLER_KILL_SWITCH
    orig_rate = settings.SAMPLING_RATE
    orig_max_rate = settings.MAX_SAMPLING_RATE

    try:
        # Test Kill Switch
        settings.SAMPLER_KILL_SWITCH = True
        settings.SAMPLING_RATE = 1.0
        settings.MAX_SAMPLING_RATE = 1.0
        assert not sampler.should_sample()

        # Test Sampling Rate
        settings.SAMPLER_KILL_SWITCH = False
        settings.SAMPLING_RATE = 0.0
        assert not sampler.should_sample()

        settings.SAMPLING_RATE = 1.0
        assert sampler.should_sample()
    finally:
        settings.SAMPLER_KILL_SWITCH = orig_kill
        settings.SAMPLING_RATE = orig_rate
        settings.MAX_SAMPLING_RATE = orig_max_rate

def test_log_production_traffic():
    # Force sample
    orig_kill = settings.SAMPLER_KILL_SWITCH
    orig_rate = settings.SAMPLING_RATE
    orig_max_rate = settings.MAX_SAMPLING_RATE
    settings.SAMPLER_KILL_SWITCH = False
    settings.SAMPLING_RATE = 1.0
    settings.MAX_SAMPLING_RATE = 1.0

    config_hash = "production_config_hash"
    # Create configuration first to satisfy FK constraint
    database.save_prompt_config(config_hash, "Template", "gemini-1.5-flash", {"rules": {"length_check": {"min": 5}}})

    try:
        req_id = sampler.log_production_traffic(
            config_hash=config_hash,
            input_data="My email is admin@site.com",
            actual_output="Output text containing SSN: 111-22-3333",
            latency_ms=150.0,
            cost=0.0001
        )
        assert req_id is not None
        assert req_id.startswith("req_")

        # Verify DB entry
        with database.get_db_conn() as conn:
            row = conn.execute("SELECT * FROM production_traffic WHERE request_id = ?", (req_id,)).fetchone()
            assert row is not None
            # Check PII is masked
            assert row["input_data"] == "My email is [EMAIL]"
            assert row["actual_output"] == "Output text containing SSN: [SSN]"
            assert row["latency_ms"] == 150.0
            assert row["cost"] == 0.0001
    finally:
        settings.SAMPLER_KILL_SWITCH = orig_kill
        settings.SAMPLING_RATE = orig_rate

def test_worker_processing():
    config_hash = "worker_config_hash"
    rules = {
        "length_check": {"min": 10},
        "json_format": {}
    }
    database.save_prompt_config(config_hash, "Template", "gpt-mock", {"rules": rules})

    # Log traffic
    with database.get_db_conn() as conn:
        conn.execute(
            """
            INSERT INTO production_traffic (request_id, config_hash, input_data, actual_output, latency_ms, cost)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            ("req_test_1", config_hash, "user input", '{"status": "ok"}', 100.0, 0.0001)
        )

    # Verify no scores yet
    with database.get_db_conn() as conn:
        scores_count = conn.execute("SELECT count(*) as cnt FROM production_scores WHERE request_id = 'req_test_1'").fetchone()["cnt"]
        assert scores_count == 0

    # Process request
    req = {
        "request_id": "req_test_1",
        "config_hash": config_hash,
        "input_data": "user input",
        "actual_output": '{"status": "ok"}',
    }
    worker.process_single_request(req)

    # Verify scores are created
    with database.get_db_conn() as conn:
        scores = conn.execute("SELECT * FROM production_scores WHERE request_id = 'req_test_1'").fetchall()
        assert len(scores) == 2
        
        metrics = {s["metric_name"]: dict(s) for s in scores}
        assert "length_check" in metrics
        assert metrics["length_check"]["score"] == 1.0  # len('{"status": "ok"}') is 16 >= 10
        assert "json_format" in metrics
        assert metrics["json_format"]["score"] == 1.0   # valid JSON

def test_health_check_handler():
    # Mock BaseHTTPRequestHandler dependencies to test the handler
    mock_wfile = MagicMock()
    
    # Simple handler subclass to intercept send_response
    class TestHandler(worker.HealthCheckHandler):
        def __init__(self):
            self.wfile = mock_wfile
            self.headers_sent = []
            self.response_code = None

        def send_response(self, code, message=None):
            self.response_code = code

        def send_header(self, keyword, value):
            self.headers_sent.append((keyword, value))

        def end_headers(self):
            pass

    # 1. Test /health
    h1 = TestHandler()
    h1.path = "/health"
    h1.do_GET()
    assert h1.response_code == 200
    mock_wfile.write.assert_called_with(b'{"status": "healthy"}')

    # 2. Test /ready with healthy worker & DB
    h2 = TestHandler()
    h2.path = "/ready"
    worker.worker_healthy = True
    h2.do_GET()
    assert h2.response_code == 200
    mock_wfile.write.assert_called_with(b'{"status": "ready"}')

    # 3. Test /ready when worker is unhealthy
    h3 = TestHandler()
    h3.path = "/ready"
    worker.worker_healthy = False
    h3.do_GET()
    assert h3.response_code == 503
    mock_wfile.write.assert_called_with(b'{"status": "unhealthy"}')
