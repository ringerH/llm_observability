import sys
import os
import time
import json
from unittest.mock import patch, MagicMock
import httpx
from click.testing import CliRunner

# Insert workspace root to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from eval_harness.config import settings
from eval_harness.client import call_llm, LLMTimeoutError, LLMRateLimitError
from eval_harness import sampler, worker, database
from eval_harness.runner import compare_regression_cmd

RESULTS_FILE = r"C:\Users\delah\.gemini\antigravity-ide\brain\6e8b2d58-bc39-4f86-82dd-cfaeda3cf7c4\simulation_results.md"


def log_result(title: str, status: str, details: str):
    print(f"[{status}] {title}")
    content = (
        f"### {title}\n* **Status:** {status}\n* **Details:**\n```\n{details}\n```\n\n"
    )
    with open(RESULTS_FILE, "a", encoding="utf-8") as f:
        f.write(content)


def init_results_file():
    with open(RESULTS_FILE, "w", encoding="utf-8") as f:
        f.write("# Observability Simulation Results\n")
        f.write(f"Generated at: {time.strftime('%Y-%m-%d %H:%M:%S')}\n\n")
        f.write(
            "This file contains the execution log of simulated production edge cases.\n\n"
        )


def run_simulation():
    init_results_file()

    # Ensure database tables exist
    database.init_db()

    # Wrap the entire simulation logic in a global patch to bypass sampling caps
    with (
        patch.object(settings, "SAMPLING_RATE", 1.0),
        patch.object(settings, "MAX_SAMPLING_RATE", 1.0),
    ):
        # --- Scenario 1: PII Masking & Telemetry Sampling ---
        try:
            # Save configuration first to satisfy FK constraint
            config_hash = "sim_v1.0"
            with database.get_db_conn() as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO prompts_config (config_hash, prompt_template, model_name, parameters) VALUES (?, ?, ?, ?)",
                    (
                        config_hash,
                        "User contact is confidential: {input}",
                        "mock-model",
                        "{}",
                    ),
                )

            raw_input = "User contact is confidential: email alex@company.com, phone 555-123-4567, SSN 123-45-6789."
            raw_output = "Response containing invoice ref CC: 1111-2222-3333-4444."

            req_id = sampler.log_production_traffic(
                config_hash=config_hash,
                input_data=raw_input,
                actual_output=raw_output,
                latency_ms=110.5,
                cost=0.0002,
            )

            # Fetch from DB to verify masking
            with database.get_db_conn() as conn:
                row = conn.execute(
                    "SELECT input_data, actual_output FROM production_traffic WHERE request_id = ?",
                    (req_id,),
                ).fetchone()

            assert row is not None
            masked_input = row["input_data"]
            masked_output = row["actual_output"]

            assert "[EMAIL]" in masked_input
            assert "[PHONE]" in masked_input
            assert "[SSN]" in masked_input
            assert "[CREDIT_CARD]" in masked_output

            log_result(
                "PII Masking & Telemetry Sampling",
                "SUCCESS",
                f"Logged Request ID: {req_id}\n"
                f"Raw Input:  {raw_input}\n"
                f"Masked In:  {masked_input}\n"
                f"Masked Out: {masked_output}",
            )
        except Exception as e:
            import traceback

            log_result(
                "PII Masking & Telemetry Sampling",
                "FAILED",
                f"{str(e)}\n{traceback.format_exc()}",
            )

        # --- Scenario 2: LLM API Timeout Exception (Fail-Fast Retry) ---
        try:
            # Mock httpx Client post to raise TimeoutException
            with patch(
                "httpx.Client.post",
                side_effect=httpx.TimeoutException("Connection timed out"),
            ):
                start_time = time.time()
                try:
                    # We expect it to try 3 times and fail fast, raising LLMTimeoutError
                    call_llm("Test timeout query", provider="openai", timeout_sec=1.0)
                    raise AssertionError("Should have raised LLMTimeoutError")
                except LLMTimeoutError as ex:
                    duration = time.time() - start_time
                    log_result(
                        "LLM API Timeout Fail-Fast",
                        "SUCCESS",
                        f"Successfully raised LLMTimeoutError as expected.\n"
                        f"Total duration (including Tenacity exponential retries): {duration:.2f} seconds.\n"
                        f"Error details: {ex}",
                    )
        except Exception as e:
            import traceback

            log_result(
                "LLM API Timeout Fail-Fast",
                "FAILED",
                f"{str(e)}\n{traceback.format_exc()}",
            )

        # --- Scenario 3: LLM API Rate Limit (HTTP 429) Propagation ---
        try:
            # Mock httpx response to return 429 status code
            mock_response = MagicMock()
            mock_response.status_code = 429
            mock_response.text = "Rate limit exceeded"

            with patch("httpx.Client.post", return_value=mock_response):
                try:
                    call_llm("Test rate limit", provider="openai")
                    raise AssertionError("Should have raised LLMRateLimitError")
                except LLMRateLimitError as ex:
                    log_result(
                        "LLM API Rate Limit (429) Propagation",
                        "SUCCESS",
                        f"Successfully caught rate limit HTTP 429 and raised LLMRateLimitError.\n"
                        f"Error details: {ex}",
                    )
        except Exception as e:
            import traceback

            log_result(
                "LLM API Rate Limit (429) Propagation",
                "FAILED",
                f"{str(e)}\n{traceback.format_exc()}",
            )

        # --- Scenario 4: Worker Scorer Evaluation & JsonValidator Failure ---
        try:
            # Save a prompt config specifying JSON validation is required
            config_hash = "json_fail_config"
            rules = {"json_format": {}}
            with database.get_db_conn() as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO prompts_config (config_hash, prompt_template, model_name, parameters) VALUES (?, ?, ?, ?)",
                    (
                        config_hash,
                        "Give json output",
                        "mock-model",
                        json.dumps({"rules": rules}),
                    ),
                )

            req_id = sampler.log_production_traffic(
                config_hash=config_hash,
                input_data="Give me data",
                actual_output="Invalid output, not a JSON",
                latency_ms=85.0,
                cost=0.0001,
            )

            # Fetch request and process via worker
            with database.get_db_conn() as conn:
                row = conn.execute(
                    "SELECT * FROM production_traffic WHERE request_id = ?", (req_id,)
                ).fetchone()

            # Process single request through worker logic
            worker.process_single_request(dict(row))

            # Fetch evaluated score
            with database.get_db_conn() as conn:
                score_row = conn.execute(
                    "SELECT score, explanation, status FROM production_scores WHERE request_id = ? AND metric_name = 'json_format'",
                    (req_id,),
                ).fetchone()

            assert score_row is not None
            assert score_row["score"] == 0.0
            assert (
                score_row["status"] == "SUCCESS"
            )  # The evaluation succeeded, even though JSON validation scored 0.0

            log_result(
                "Worker Scorer Validation (JSON Failure)",
                "SUCCESS",
                f"Processed invalid JSON format via worker.\n"
                f"Score given: {score_row['score']}\n"
                f"Status: {score_row['status']}\n"
                f"Explanation: {score_row['explanation']}",
            )
        except Exception as e:
            import traceback

            log_result(
                "Worker Scorer Validation (JSON Failure)",
                "FAILED",
                f"{str(e)}\n{traceback.format_exc()}",
            )

        # --- Scenario 5: Regression Evaluation Runner Verification ---
        try:
            # Create test run data demonstrating quality regression
            baseline_run_id = "baseline_run_sim"
            comparison_run_id = "comparison_run_sim"
            config_hash = "regression_config"

            with database.get_db_conn() as conn:
                # Setup Prompt Config
                conn.execute(
                    "INSERT OR REPLACE INTO prompts_config (config_hash, prompt_template, model_name, parameters) VALUES (?, ?, ?, ?)",
                    (config_hash, "Template", "mock-model", "{}"),
                )

                # Baseline run summary (high score: 0.95)
                conn.execute(
                    "INSERT OR REPLACE INTO eval_runs (run_id, config_hash, status, is_baseline, summary) VALUES (?, ?, 'COMPLETED', 1, ?)",
                    (
                        baseline_run_id,
                        config_hash,
                        json.dumps(
                            {
                                "avg_score": 0.95,
                                "total_cases": 10,
                                "passed": 9,
                                "failed": 1,
                            }
                        ),
                    ),
                )
                # Comparison run summary (low score: 0.70 - significant regression!)
                conn.execute(
                    "INSERT OR REPLACE INTO eval_runs (run_id, config_hash, status, is_baseline, summary) VALUES (?, ?, 'COMPLETED', 0, ?)",
                    (
                        comparison_run_id,
                        config_hash,
                        json.dumps(
                            {
                                "avg_score": 0.70,
                                "total_cases": 10,
                                "passed": 7,
                                "failed": 3,
                            }
                        ),
                    ),
                )

                # Setup some dummy case results so get_run_results returns them
                conn.execute(
                    "INSERT OR REPLACE INTO test_cases (case_id, input_data) VALUES ('case_1', 'input')"
                )
                conn.execute(
                    "INSERT OR REPLACE INTO eval_case_results (run_id, case_id, actual_output, latency_ms, cost) VALUES (?, 'case_1', 'out', 10.0, 0.001)",
                    (baseline_run_id,),
                )
                conn.execute(
                    "INSERT OR REPLACE INTO eval_case_results (run_id, case_id, actual_output, latency_ms, cost) VALUES (?, 'case_1', 'out', 10.0, 0.001)",
                    (comparison_run_id,),
                )

                # Setup metric scores to trigger regression
                conn.execute(
                    "INSERT OR REPLACE INTO eval_metrics (run_id, case_id, metric_name, metric_type, score, status) VALUES (?, 'case_1', 'rule_metric', 'RULE', 1.0, 'SUCCESS')",
                    (baseline_run_id,),
                )
                conn.execute(
                    "INSERT OR REPLACE INTO eval_metrics (run_id, case_id, metric_name, metric_type, score, status) VALUES (?, 'case_1', 'rule_metric', 'RULE', 0.5, 'SUCCESS')",
                    (comparison_run_id,),
                )

            # Run regression CLI check using Click runner
            runner = CliRunner()
            res = runner.invoke(
                compare_regression_cmd,
                ["--run-id", comparison_run_id, "--threshold", "0.1"],
            )

            # We expect it to exit with status 1 (regression detected)
            assert res.exit_code == 1
            assert "degraded by 0.500" in res.output

            log_result(
                "Regression Threshold Detector",
                "SUCCESS",
                f"Comparing Baseline Run ({baseline_run_id}, Score: 1.0) and PR Run ({comparison_run_id}, Score: 0.5).\n"
                f"Exit Code: {res.exit_code} (1 means regression found)\n"
                f"Command Output:\n{res.output}",
            )
        except Exception as e:
            import traceback

            log_result(
                "Regression Threshold Detector",
                "FAILED",
                f"{str(e)}\n{traceback.format_exc()}",
            )


if __name__ == "__main__":
    run_simulation()
