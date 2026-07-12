import sqlite3
import os
import json
from contextlib import contextmanager
from typing import Generator, Dict, Any, Optional, List, Tuple
from eval_harness.config import settings

SCHEMA_PATH = os.path.join(os.path.dirname(__file__), "schema.sql")

@contextmanager
def get_db_conn() -> Generator[sqlite3.Connection, None, None]:
    """
    Context manager for database connections.
    Enables foreign keys and sets row factory for dictionary-like access.
    """
    db_path = settings.DATABASE_PATH
    db_dir = os.path.dirname(db_path)
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

def init_db() -> None:
    """
    Initializes the database schema if tables do not exist.
    """
    if not os.path.exists(SCHEMA_PATH):
        raise FileNotFoundError(f"Database schema file not found at: {SCHEMA_PATH}")
        
    with open(SCHEMA_PATH, "r") as f:
        schema_sql = f.read()
        
    with get_db_conn() as conn:
        conn.executescript(schema_sql)

def save_prompt_config(
    config_hash: str,
    prompt_template: str,
    model_name: str,
    parameters: Dict[str, Any]
) -> None:
    """
    Saves a prompt/model configuration.
    """
    with get_db_conn() as conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO prompts_config (config_hash, prompt_template, model_name, parameters)
            VALUES (?, ?, ?, ?)
            """,
            (config_hash, prompt_template, model_name, json.dumps(parameters))
        )

def initialize_run(run_id: str, config_hash: str, is_baseline: bool = False) -> None:
    """
    Initializes or resets an evaluation run.
    To ensure run idempotency, if the run_id already exists, all associated case results
    and metrics are deleted before recreating the run.
    """
    with get_db_conn() as conn:
        # Delete existing data for the run_id if it exists (cascade will handle child tables if foreign keys are on,
        # but we explicitly delete from related tables just in case to guarantee clean slate)
        conn.execute("DELETE FROM eval_metrics WHERE run_id = ?", (run_id,))
        conn.execute("DELETE FROM eval_case_results WHERE run_id = ?", (run_id,))
        conn.execute("DELETE FROM eval_runs WHERE run_id = ?", (run_id,))
        
        # Insert fresh run
        conn.execute(
            """
            INSERT INTO eval_runs (run_id, config_hash, status, is_baseline)
            VALUES (?, ?, 'PENDING', ?)
            """,
            (run_id, config_hash, 1 if is_baseline else 0)
        )

def save_test_case(
    case_id: str,
    input_data: str,
    expected_output: Optional[str] = None,
    rubric: Optional[str] = None
) -> None:
    """
    Saves a test case definition.
    """
    with get_db_conn() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO test_cases (case_id, input_data, expected_output, rubric)
            VALUES (?, ?, ?, ?)
            """,
            (case_id, input_data, expected_output, rubric)
        )

def save_case_result(
    run_id: str,
    case_id: str,
    actual_output: Optional[str],
    latency_ms: Optional[float],
    cost: Optional[float],
    error_message: Optional[str] = None
) -> None:
    """
    Saves the execution result of a single case in a run.
    """
    with get_db_conn() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO eval_case_results (run_id, case_id, actual_output, latency_ms, cost, error_message)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (run_id, case_id, actual_output, latency_ms, cost, error_message)
        )

def save_metric(
    run_id: str,
    case_id: str,
    metric_name: str,
    metric_type: str,
    score: Optional[float],
    explanation: Optional[str],
    status: str
) -> None:
    """
    Saves an individual metric score for a specific case in a run.
    """
    with get_db_conn() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO eval_metrics (run_id, case_id, metric_name, metric_type, score, explanation, status)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (run_id, case_id, metric_name, metric_type, score, explanation, status)
        )

def update_run_status(run_id: str, status: str, summary: Optional[Dict[str, Any]] = None) -> None:
    """
    Updates the final status and summary of a run.
    """
    summary_json = json.dumps(summary) if summary else None
    with get_db_conn() as conn:
        conn.execute(
            """
            UPDATE eval_runs
            SET status = ?, summary = ?
            WHERE run_id = ?
            """,
            (status, summary_json, run_id)
        )

def get_baseline_run(config_hash: str) -> Optional[Dict[str, Any]]:
    """
    Retrieves the latest completed run designated as a baseline for the given config_hash.
    """
    with get_db_conn() as conn:
        row = conn.execute(
            """
            SELECT * FROM eval_runs
            WHERE config_hash = ? AND status = 'COMPLETED' AND is_baseline = 1
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (config_hash,)
        ).fetchone()
        return dict(row) if row else None

def get_run_results(run_id: str) -> List[Dict[str, Any]]:
    """
    Retrieves all results for a run, combining case results and their metrics.
    """
    with get_db_conn() as conn:
        rows = conn.execute(
            """
            SELECT r.*, t.input_data, t.expected_output, t.rubric
            FROM eval_case_results r
            JOIN test_cases t ON r.case_id = t.case_id
            WHERE r.run_id = ?
            """,
            (run_id,)
        ).fetchall()
        
        results = [dict(row) for row in rows]
        
        for res in results:
            metrics_rows = conn.execute(
                """
                SELECT metric_name, metric_type, score, explanation, status
                FROM eval_metrics
                WHERE run_id = ? AND case_id = ?
                """,
                (run_id, res["case_id"])
            ).fetchall()
            res["metrics"] = {m["metric_name"]: dict(m) for m in metrics_rows}
            
        return results


def save_production_score(
    request_id: str,
    metric_name: str,
    metric_type: str,
    score: Optional[float],
    explanation: Optional[str],
    status: str
) -> None:
    """
    Saves an evaluation score for a production request.
    """
    with get_db_conn() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO production_scores (request_id, metric_name, metric_type, score, explanation, status)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (request_id, metric_name, metric_type, score, explanation, status)
        )


def get_rolling_regression_rate(limit: int = 100, threshold: float = 0.05) -> float:
    """
    Computes the rolling regression rate over the last N sampled production requests.
    A request is considered a regression if its average rule score degrades from the config's baseline by more than threshold.
    """
    with get_db_conn() as conn:
        rows = conn.execute(
            """
            SELECT request_id, config_hash 
            FROM production_traffic 
            ORDER BY sampled_at DESC 
            LIMIT ?
            """,
            (limit,)
        ).fetchall()
        
    if not rows:
        return 0.0
        
    total_checked = 0
    regressions = 0
    
    for r in rows:
        req_id = r["request_id"]
        config_hash = r["config_hash"]
        
        # Get baseline for this config
        baseline = get_baseline_run(config_hash)
        if not baseline:
            continue
            
        baseline_id = baseline["run_id"]
        
        # Get average score of rules in the baseline run
        with get_db_conn() as conn:
            base_rule_scores = conn.execute(
                """
                SELECT avg(score) as avg_score 
                FROM eval_metrics 
                WHERE run_id = ? AND metric_type = 'RULE' AND score IS NOT NULL AND status = 'SUCCESS'
                """,
                (baseline_id,)
            ).fetchone()["avg_score"]
            
        if base_rule_scores is None:
            continue
            
        # Get average score of rules for this production request
        with get_db_conn() as conn:
            prod_rule_scores = conn.execute(
                """
                SELECT avg(score) as avg_score 
                FROM production_scores 
                WHERE request_id = ? AND metric_type = 'RULE' AND score IS NOT NULL AND status = 'SUCCESS'
                """,
                (req_id,)
            ).fetchone()["avg_score"]
            
        if prod_rule_scores is None:
            continue
            
        total_checked += 1
        if (base_rule_scores - prod_rule_scores) > threshold:
            regressions += 1
            
    return float(regressions) / total_checked if total_checked > 0 else 0.0


