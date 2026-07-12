import pytest
import os
import sqlite3
from eval_harness.config import settings
from eval_harness import database

@pytest.fixture(autouse=True)
def test_db_setup(tmp_path):
    """
    Fixture that redirects the database path to a temporary file
    and initializes the database schema before each test.
    """
    test_db = str(tmp_path / "test_eval.db")
    original_path = settings.DATABASE_PATH
    settings.DATABASE_PATH = test_db
    
    # Initialize the tables
    database.init_db()
    
    yield test_db
    
    # Restore original path
    settings.DATABASE_PATH = original_path
    if os.path.exists(test_db):
        try:
            os.remove(test_db)
        except OSError:
            pass

def test_db_initialization(test_db_setup):
    # Verify that all schema tables exist
    with database.get_db_conn() as conn:
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        table_names = [t["name"] for t in tables]
        
        assert "prompts_config" in table_names
        assert "eval_runs" in table_names
        assert "test_cases" in table_names
        assert "eval_case_results" in table_names
        assert "eval_metrics" in table_names

def test_run_idempotency():
    # Save a configuration
    config_hash = "abc123config"
    database.save_prompt_config(config_hash, "Template", "gpt-mock", {"temp": 0.5})
    
    # Initialize the run first time
    run_id = "test_run_1"
    database.initialize_run(run_id, config_hash)
    
    # Add dummy results
    database.save_test_case("c1", "input")
    database.save_case_result(run_id, "c1", "output", 120.0, 0.001)
    database.save_metric(run_id, "c1", "json_format", "RULE", 1.0, "Valid JSON", "SUCCESS")
    
    # Verify records exist
    results = database.get_run_results(run_id)
    assert len(results) == 1
    assert results[0]["case_id"] == "c1"
    assert "json_format" in results[0]["metrics"]
    
    # Re-initialize the exact same run_id (simulate idempotency trigger)
    database.initialize_run(run_id, config_hash)
    
    # Verify previous results for this run_id are cleared and no duplicates exist
    results_after = database.get_run_results(run_id)
    assert len(results_after) == 0
    
    # Check that test_cases still exist (independent definition)
    with database.get_db_conn() as conn:
        cases_count = conn.execute("SELECT count(*) as cnt FROM test_cases").fetchone()["cnt"]
        assert cases_count == 1

def test_foreign_key_constraints():
    # Attempt to initialize a run with a non-existent config_hash should fail due to foreign key constraint
    with pytest.raises(sqlite3.IntegrityError):
        database.initialize_run("invalid_run", "non_existent_config_hash")
