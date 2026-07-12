import pytest
import os
import json
from click.testing import CliRunner
from eval_harness.config import settings
from eval_harness import database, runner

@pytest.fixture(autouse=True)
def test_db_setup(tmp_path):
    """
    Fixture that redirects the database path to a temporary file
    and initializes the database schema before each test.
    """
    test_db = str(tmp_path / "test_eval_runner.db")
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

def test_runner_happy_path(tmp_path):
    # Create a temporary test set
    test_set_path = tmp_path / "test_set.json"
    test_cases = [
        {
            "case_id": "case_1",
            "input_data": "Write some text",
            "expected_output": "something",
            "rules": {
                "length_check": {"min": 5, "max": 100}
            }
        },
        {
            "case_id": "case_2",
            "input_data": "Write JSON data",
            "rules": {
                "json_format": {}
            }
        }
    ]
    with open(test_set_path, "w") as f:
        json.dump(test_cases, f)

    cli_runner = CliRunner()
    result = cli_runner.invoke(
        runner.cli,
        [
            "run-eval",
            "--test-set", str(test_set_path),
            "--run-id", "test_runner_run_1",
            "--prompt-template", "Query: {input}",
            "--model-name", "mock-model",
            "--is-baseline"
        ]
    )

    assert result.exit_code == 0
    assert "Run Summary" in result.output
    assert '"total_cases": 2' in result.output
    assert '"passed": 2' in result.output

    # Verify db state
    results = database.get_run_results("test_runner_run_1")
    assert len(results) == 2
    assert results[0]["case_id"] == "case_1"
    assert "length_check" in results[0]["metrics"]
    assert results[0]["metrics"]["length_check"]["score"] == 1.0
    assert results[0]["metrics"]["length_check"]["status"] == "SUCCESS"

def test_runner_llm_failure_handling(tmp_path):
    # Test case designed to fail the LLM-under-test call via trigger string
    test_set_path = tmp_path / "test_set_fail.json"
    test_cases = [
        {
            "case_id": "case_fail_timeout",
            "input_data": "TRIGGER_TIMEOUT in input",
            "rules": {
                "length_check": {"min": 5}
            }
        },
        {
            "case_id": "case_fail_crash",
            "input_data": "TRIGGER_CRASH in input",
            "rules": {
                "length_check": {"min": 5}
            }
        }
    ]
    with open(test_set_path, "w") as f:
        json.dump(test_cases, f)

    cli_runner = CliRunner()
    result = cli_runner.invoke(
        runner.cli,
        [
            "run-eval",
            "--test-set", str(test_set_path),
            "--run-id", "test_runner_run_fail",
            "--prompt-template", "Query: {input}"
        ]
    )

    # CLI should run and complete successfully (non-zero exits only for config/invocation bugs)
    assert result.exit_code == 0
    assert '"total_cases": 2' in result.output
    assert '"failed": 2' in result.output

    # Check that failures are written to DB instead of crashing
    results = database.get_run_results("test_runner_run_fail")
    assert len(results) == 2
    
    # Check case 1: timeout
    c1 = next(r for r in results if r["case_id"] == "case_fail_timeout")
    assert c1["actual_output"] is None
    assert "TimeoutError" in c1["error_message"]
    # Metric should be marked FAILED with score 0
    assert c1["metrics"]["length_check"]["score"] == 0.0
    assert c1["metrics"]["length_check"]["status"] == "FAILED"
    assert "LLM-under-test failed" in c1["metrics"]["length_check"]["explanation"]

    # Check case 2: crash
    c2 = next(r for r in results if r["case_id"] == "case_fail_crash")
    assert c2["actual_output"] is None
    assert "RuntimeError" in c2["error_message"]

def test_runner_judge_disagreement_calculation(tmp_path, monkeypatch):
    # Set up test set with both rule check and judge
    test_set_path = tmp_path / "test_set_disagree.json"
    test_cases = [
        {
            "case_id": "case_disagree",
            "input_data": "Write JSON data",
            "rules": {
                "length_check": {"min": 5},
                "llm_judge": {"rubric": "Helpfulness", "trials": 1}
            }
        }
    ]
    with open(test_set_path, "w") as f:
        json.dump(test_cases, f)

    # Mock client call_llm to return score 0.1 (disagreeing with rule score 1.0)
    from eval_harness import client
    def mock_call(*args, **kwargs):
        return '{"score": 0.1, "explanation": "Not helpful at all"}'
    monkeypatch.setattr(client, "call_llm", mock_call)

    cli_runner = CliRunner()
    result = cli_runner.invoke(
        runner.cli,
        [
            "run-eval",
            "--test-set", str(test_set_path),
            "--run-id", "test_run_disagree",
            "--prompt-template", "Query: {input}",
            "--real-llm" # Force real LLM path to trigger monkeypatched client call_llm
        ]
    )

    assert result.exit_code == 0
    
    # Retrieve the run summary from DB
    with database.get_db_conn() as conn:
        row = conn.execute("SELECT summary FROM eval_runs WHERE run_id='test_run_disagree'").fetchone()
        summary = json.loads(row["summary"])
        # Expect 1.0 disagreement rate (1 case with both, 1 disagreement where 1.0 - 0.1 > 0.5)
        assert summary["judge_disagreement_rate"] == 1.0


def test_regression_comparator(tmp_path):
    # Setup test set
    test_set_path = tmp_path / "test_set.json"
    test_cases = [
        {
            "case_id": "case_1",
            "input_data": "Write some text",
            "rules": {
                "length_check": {"min": 5, "max": 100}
            }
        }
    ]
    with open(test_set_path, "w") as f:
        json.dump(test_cases, f)

    cli_runner = CliRunner()
    
    # 1. Run baseline run
    result_base = cli_runner.invoke(
        runner.cli,
        [
            "run-eval",
            "--test-set", str(test_set_path),
            "--run-id", "run_baseline",
            "--prompt-template", "Query: {input}",
            "--model-name", "mock-model",
            "--is-baseline"
        ]
    )
    assert result_base.exit_code == 0

    # 2. Run a comparison run (no regressions, mock LLM output is identical)
    result_ok = cli_runner.invoke(
        runner.cli,
        [
            "run-eval",
            "--test-set", str(test_set_path),
            "--run-id", "run_ok",
            "--prompt-template", "Query: {input}",
            "--model-name", "mock-model"
        ]
    )
    assert result_ok.exit_code == 0

    # Compare: should pass
    result_comp_ok = cli_runner.invoke(
        runner.cli,
        [
            "compare-regression",
            "--run-id", "run_ok",
            "--threshold", "0.05"
        ]
    )
    assert result_comp_ok.exit_code == 0
    assert "Success: No regressions detected!" in result_comp_ok.output

    # 3. Simulate regression: update run_ok score to be degraded in DB
    with database.get_db_conn() as conn:
        conn.execute(
            "UPDATE eval_metrics SET score = 0.5 WHERE run_id = 'run_ok' AND metric_name = 'length_check'"
        )

    # Compare: should fail due to degradation (1.0 -> 0.5 > 0.05)
    result_comp_fail = cli_runner.invoke(
        runner.cli,
        [
            "compare-regression",
            "--run-id", "run_ok",
            "--threshold", "0.05"
        ]
    )
    assert result_comp_fail.exit_code == 1
    assert "Total regressions detected: 1" in result_comp_fail.output
    assert "degraded by 0.500" in result_comp_fail.output

    # 4. Compare with missing baseline config
    result_comp_nobase = cli_runner.invoke(
        runner.cli,
        [
            "compare-regression",
            "--run-id", "run_ok",
            "--baseline-id", "non_existent_run"
        ]
    )
    assert result_comp_nobase.exit_code == 1
    assert "Error: Specified baseline run 'non_existent_run' not found" in result_comp_nobase.output


