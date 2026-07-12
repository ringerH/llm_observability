import click
import json
import uuid
import hashlib
import time
import os
from typing import Dict, Any, List, Optional, Tuple
from eval_harness.config import settings
from eval_harness import database, scorers, logging

def compute_config_hash(prompt_template: str, model_name: str, parameters: Dict[str, Any]) -> str:
    """
    Computes a stable SHA-256 hash of the configuration to version scoring results.
    """
    param_str = json.dumps(parameters, sort_keys=True)
    raw_str = f"{prompt_template}||{model_name}||{param_str}"
    return hashlib.sha256(raw_str.encode("utf-8")).hexdigest()

def mock_call_llm(prompt: str, case_id: str) -> str:
    """
    Simulates an LLM call for offline development and testing.
    Can be programmed to simulate transient failures or formatting variations.
    """
    time.sleep(0.1) # Simulate network latency
    
    # Special triggers for testing failure modes
    if "TRIGGER_TIMEOUT" in prompt:
        raise TimeoutError("Mock LLM call timed out after 5.0s")
    if "TRIGGER_CRASH" in prompt:
        raise RuntimeError("Mock LLM service crashed unexpectedly")
    
    # Default returns
    if "json" in prompt.lower() or "json_format" in case_id:
        return '{"status": "ok", "message": "This is simulated JSON response", "score": 0.95}'
    
    return f"Simulated output for prompt: {prompt[:30]}..."

def execute_llm_call(
    prompt_template: str,
    input_data: str,
    model_name: str,
    parameters: Dict[str, Any],
    case_id: str,
    mock: bool = True
) -> Tuple[Optional[str], float, float, Optional[str]]:
    """
    Executes the LLM-under-test call.
    Returns a tuple of (actual_output, latency_ms, cost, error_message).
    """
    prompt = prompt_template.replace("{input}", input_data)
    start_time = time.time()
    latency_ms = 0.0
    cost = 0.0
    actual_output = None
    error_message = None

    try:
        if mock:
            actual_output = mock_call_llm(prompt, case_id)
            # Simulated cheap model cost calculation (e.g. Gemini 1.5 Flash approx rate)
            cost = 0.000075 
        else:
            from eval_harness import client
            provider = "openai" if model_name.lower().startswith("gpt") else "gemini"
            actual_output = client.call_llm(prompt, provider=provider, timeout_sec=12.0)
            
            # Approximate cost tracking:
            if provider == "gemini":
                # gemini-1.5-flash inputs/outputs rate approx: $0.075 / 1M input tokens, $0.3 / 1M output tokens
                cost = (len(prompt) / 4) * 0.075 / 1_000_000 + (len(actual_output) / 4) * 0.3 / 1_000_000
            else:
                # gpt-4o-mini inputs/outputs rate approx: $0.15 / 1M input tokens, $0.6 / 1M output tokens
                cost = (len(prompt) / 4) * 0.15 / 1_000_000 + (len(actual_output) / 4) * 0.6 / 1_000_000
            
        latency_ms = (time.time() - start_time) * 1000.0
        
    except Exception as e:
        latency_ms = (time.time() - start_time) * 1000.0
        error_message = f"{type(e).__name__}: {str(e)}"
        logging.log_error(
            f"LLM call failed for test case {case_id}",
            case_id=case_id,
            exc_info=e
        )

    return actual_output, latency_ms, cost, error_message

def run_case_evaluation(
    run_id: str,
    case: Dict[str, Any],
    prompt_template: str,
    model_name: str,
    parameters: Dict[str, Any],
    mock_llm: bool
) -> Dict[str, Any]:
    """
    Orchestrates the evaluation of a single test case.
    Saves results and runs all defined rules-based scorers.
    """
    case_id = case["case_id"]
    input_data = case["input_data"]
    expected_output = case.get("expected_output")
    rubric = case.get("rubric")
    rules = case.get("rules", {})

    # 1. Save the test case definition
    database.save_test_case(case_id, input_data, expected_output, rubric)

    # 2. Run the LLM under test
    logging.log_info(f"Starting LLM call for case {case_id}", run_id=run_id, case_id=case_id)
    actual_output, latency, cost, error = execute_llm_call(
        prompt_template, input_data, model_name, parameters, case_id, mock=mock_llm
    )

    # 3. Save Case Result
    database.save_case_result(run_id, case_id, actual_output, latency, cost, error)

    case_passed = True

    if error:
        logging.log_error(
            f"Case {case_id} failed due to LLM error: {error}",
            run_id=run_id,
            case_id=case_id
        )
        # Grade all metrics as failed because there is no output to grade
        for rule_name in rules.keys():
            database.save_metric(
                run_id=run_id,
                case_id=case_id,
                metric_name=rule_name,
                metric_type="RULE" if rule_name != "llm_judge" else "LLM_JUDGE",
                score=0.0,
                explanation=f"LLM-under-test failed: {error}",
                status="FAILED"
            )
        return {"passed": False, "cost": cost, "latency_ms": latency}

    # 4. Run scorers
    for rule_name, rule_config in rules.items():
        scorer_inst = None
        try:
            if rule_name == "json_format":
                scorer_inst = scorers.JsonValidatorScorer()
            elif rule_name == "regex_match":
                pattern = rule_config if isinstance(rule_config, str) else rule_config.get("pattern")
                case_sensitive = rule_config.get("case_sensitive", True) if isinstance(rule_config, dict) else True
                scorer_inst = scorers.RegexMatchScorer(pattern, case_sensitive)
            elif rule_name == "length_check":
                min_len = rule_config.get("min")
                max_len = rule_config.get("max")
                scorer_inst = scorers.LengthScorer(min_len, max_len)
            elif rule_name == "llm_judge":
                rubric_val = rule_config.get("rubric") if isinstance(rule_config, dict) else rubric
                trials_val = rule_config.get("trials", 3) if isinstance(rule_config, dict) else 3
                scorer_inst = scorers.LlmJudgeScorer(rubric=rubric_val, trials=trials_val, mock=mock_llm)
            
            if scorer_inst:
                res = scorer_inst.score(input_val=input_data, output_val=actual_output, expected_val=expected_output)
                database.save_metric(
                    run_id=run_id,
                    case_id=case_id,
                    metric_name=rule_name,
                    metric_type=scorer_inst.metric_type,
                    score=res.score,
                    explanation=res.explanation,
                    status=res.status
                )
                if res.score is not None and res.score < 0.5: # Consider <0.5 score as failing quality threshold
                    case_passed = False
            else:
                logging.log_warn(f"Unknown scorer: {rule_name}", run_id=run_id, case_id=case_id)
                
        except Exception as ex:
            # Scorer evaluation itself failed
            logging.log_error(
                f"Scorer '{rule_name}' failed to evaluate case {case_id}",
                run_id=run_id,
                case_id=case_id,
                exc_info=ex
            )
            database.save_metric(
                run_id=run_id,
                case_id=case_id,
                metric_name=rule_name,
                metric_type="RULE" if rule_name != "llm_judge" else "LLM_JUDGE",
                score=0.0,
                explanation=f"Grader error: {type(ex).__name__} {str(ex)}",
                status="FAILED"
            )
            case_passed = False

    return {"passed": case_passed, "cost": cost, "latency_ms": latency}


@click.group()
def cli():
    """LLM Evaluation and Observability Harness Command Line Interface."""
    pass

@cli.command("init-db")
def init_db_cmd():
    """Initializes the database and runs migrations."""
    click.echo("Initializing SQLite Database...")
    try:
        database.init_db()
        click.echo(f"Database successfully initialized at {settings.DATABASE_PATH}")
    except Exception as e:
        click.echo(f"Error initializing database: {e}", err=True)

@cli.command("run-eval")
@click.option("--test-set", required=True, type=click.Path(exists=True), help="Path to test set JSON file.")
@click.option("--run-id", default=None, help="Unique identifier for the run. If not provided, a UUID will be generated.")
@click.option("--prompt-template", default="User query: {input}", help="The prompt template under test.")
@click.option("--model-name", default="gemini-1.5-flash", help="The name of the model being evaluated.")
@click.option("--parameters", default="{}", help="JSON string representing model parameters.")
@click.option("--is-baseline", is_flag=True, help="Designate this run as the baseline version.")
@click.option("--mock-llm/--real-llm", default=True, help="Use simulated LLM output to save cost during testing.")
def run_eval_cmd(test_set, run_id, prompt_template, model_name, parameters, is_baseline, mock_llm):
    """Runs the evaluation test suite."""
    # 1. Parse params
    try:
        params_dict = json.loads(parameters)
    except json.JSONDecodeError as e:
        click.echo(f"Invalid JSON in --parameters: {e}", err=True)
        return

    # 2. Setup run-id & config-hash
    if not run_id:
        run_id = f"run_{uuid.uuid4().hex[:12]}"
        
    config_hash = compute_config_hash(prompt_template, model_name, params_dict)
    
    click.echo(f"Starting Eval Run: {run_id}")
    click.echo(f"Config Hash: {config_hash}")

    # 3. Load test cases
    try:
        with open(test_set, "r") as f:
            cases = json.load(f)
    except Exception as e:
        click.echo(f"Error loading test set file: {e}", err=True)
        return

    # 4. Prepare DB
    database.save_prompt_config(config_hash, prompt_template, model_name, params_dict)
    database.initialize_run(run_id, config_hash, is_baseline)
    
    database.update_run_status(run_id, "RUNNING")
    logging.log_info(f"Eval run {run_id} status changed to RUNNING", run_id=run_id)

    total_cases = len(cases)
    passed_cases = 0
    total_cost = 0.0
    total_latency = 0.0
    
    # 5. Process test cases
    for case in cases:
        result = run_case_evaluation(
            run_id=run_id,
            case=case,
            prompt_template=prompt_template,
            model_name=model_name,
            parameters=params_dict,
            mock_llm=mock_llm
        )
        if result["passed"]:
            passed_cases += 1
        total_cost += result["cost"]
        total_latency += result["latency_ms"]

    # 6. Calculate judge disagreement rate (absolute difference > 0.5 between judge and average of rule metrics)
    results = database.get_run_results(run_id)
    cases_with_both = 0
    disagreements = 0
    
    for res in results:
        metrics = res.get("metrics", {})
        judge_metric = metrics.get("llm_judge")
        
        # Collect rule metrics
        rule_scores = [
            m["score"] for name, m in metrics.items()
            if m["metric_type"] == "RULE" and m["score"] is not None and m["status"] == "SUCCESS"
        ]
        
        if judge_metric and judge_metric["status"] == "SUCCESS" and judge_metric["score"] is not None and rule_scores:
            cases_with_both += 1
            avg_rule_score = sum(rule_scores) / len(rule_scores)
            if abs(judge_metric["score"] - avg_rule_score) > 0.5:
                disagreements += 1
                
    disagreement_rate = float(disagreements) / cases_with_both if cases_with_both > 0 else 0.0

    # 7. Finalize Run
    summary = {
        "total_cases": total_cases,
        "passed": passed_cases,
        "failed": total_cases - passed_cases,
        "avg_score": float(passed_cases) / total_cases if total_cases > 0 else 0.0,
        "cost": total_cost,
        "duration_ms": total_latency,
        "judge_disagreement_rate": disagreement_rate
    }
    
    database.update_run_status(run_id, "COMPLETED", summary)
    logging.log_info(
        f"Eval run {run_id} completed successfully",
        run_id=run_id,
        judge_disagreement_rate=disagreement_rate,
        summary=summary
    )
    
    click.echo("--- Run Summary ---")
    click.echo(json.dumps(summary, indent=2))
    click.echo(f"Run data written to SQLite database.")


@cli.command("compare-regression")
@click.option("--run-id", required=True, help="The current run ID to compare.")
@click.option("--baseline-id", default=None, help="Specific baseline run ID. If not provided, the latest baseline for the configuration will be auto-detected.")
@click.option("--threshold", type=float, default=None, help="Regression threshold delta. Defaults to REGRESSION_THRESHOLD.")
def compare_regression_cmd(run_id, baseline_id, threshold):
    """Compares the current run results against a baseline to detect regressions."""
    import sys
    if threshold is None:
        threshold = settings.REGRESSION_THRESHOLD

    # 1. Fetch current run details
    with database.get_db_conn() as conn:
        current_run = conn.execute("SELECT * FROM eval_runs WHERE run_id = ?", (run_id,)).fetchone()
    
    if not current_run:
        click.echo(f"Error: Current run '{run_id}' not found in database.", err=True)
        sys.exit(1)

    if current_run["status"] != "COMPLETED":
        click.echo(f"Error: Current run '{run_id}' is not in COMPLETED status (status is '{current_run['status']}').", err=True)
        sys.exit(1)

    config_hash = current_run["config_hash"]

    # 2. Get baseline run
    if baseline_id:
        with database.get_db_conn() as conn:
            baseline_run = conn.execute("SELECT * FROM eval_runs WHERE run_id = ?", (baseline_id,)).fetchone()
        if not baseline_run:
            click.echo(f"Error: Specified baseline run '{baseline_id}' not found in database.", err=True)
            sys.exit(1)
    else:
        baseline_run = database.get_baseline_run(config_hash)
        if not baseline_run:
            click.echo(f"Error: No baseline run found for config hash '{config_hash}'.", err=True)
            sys.exit(1)

    baseline_id = baseline_run["run_id"]
    click.echo(f"Comparing current run '{run_id}' against baseline run '{baseline_id}'...")

    # 3. Fetch results for both runs
    current_results = {r["case_id"]: r for r in database.get_run_results(run_id)}
    baseline_results = {r["case_id"]: r for r in database.get_run_results(baseline_id)}

    regressions_found = []
    comparisons = []

    # 4. Compare case metrics
    for case_id, curr_case in current_results.items():
        base_case = baseline_results.get(case_id)
        if not base_case:
            click.echo(f"Warning: Case '{case_id}' not present in baseline run '{baseline_id}'. Skipping comparison.")
            continue

        curr_metrics = curr_case.get("metrics", {})
        base_metrics = base_case.get("metrics", {})

        for metric_name, curr_metric in curr_metrics.items():
            base_metric = base_metrics.get(metric_name)
            if not base_metric:
                continue

            curr_score = curr_metric.get("score")
            base_score = base_metric.get("score")

            if curr_score is not None and base_score is not None:
                delta = base_score - curr_score
                comparisons.append({
                    "case_id": case_id,
                    "metric_name": metric_name,
                    "baseline_score": base_score,
                    "current_score": curr_score,
                    "delta": delta
                })

                if delta > threshold:
                    regressions_found.append({
                        "case_id": case_id,
                        "metric_name": metric_name,
                        "baseline_score": base_score,
                        "current_score": curr_score,
                        "delta": delta
                    })

    # 5. Output Summary
    click.echo("\n--- Regression Comparison Summary ---")
    click.echo(f"Threshold: {threshold:.3f}")
    click.echo(f"Total metrics compared: {len(comparisons)}")
    click.echo(f"Total regressions detected: {len(regressions_found)}")

    if regressions_found:
        click.echo("\nDetected Regressions:")
        for reg in regressions_found:
            click.echo(
                f"  - Case '{reg['case_id']}', Metric '{reg['metric_name']}': "
                f"degraded by {reg['delta']:.3f} (Baseline: {reg['baseline_score']:.2f} -> Current: {reg['current_score']:.2f})"
            )
        sys.exit(1)
    else:
        click.echo("\nSuccess: No regressions detected!")
        sys.exit(0)


if __name__ == "__main__":
    cli()
