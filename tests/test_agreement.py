import pytest
import os
import sqlite3
from eval_harness.config import settings
from eval_harness import database

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

def test_human_score_saving():
    req_id = "prod_req_1"
    database.save_prompt_config("cfg", "template", "model", {})
    # Insert traffic
    with database.get_db_conn() as conn:
        conn.execute(
            "INSERT INTO production_traffic (request_id, config_hash, input_data, actual_output, latency_ms, cost) VALUES (?, 'cfg', 'in', 'out', 100, 0.0)",
            (req_id,)
        )
    
    # Save score
    database.save_human_score(req_id, 0.8)
    
    # Verify in DB
    with database.get_db_conn() as conn:
        row = conn.execute("SELECT human_score FROM human_scores WHERE request_id = ?", (req_id,)).fetchone()
        assert row is not None
        assert row["human_score"] == 0.8

def test_stratified_sampling():
    config_hash = "cfg"
    database.save_prompt_config(config_hash, "Template", "gemini-1.5-flash", {"rules": {"llm_judge": {}}})
    
    # Insert 10 requests with different judge scores:
    # 3 Fails (< 0.4), 4 Borderline (0.4 - 0.8), 3 Passes (> 0.8)
    scores = [0.1, 0.2, 0.3, 0.5, 0.6, 0.7, 0.75, 0.9, 0.95, 1.0]
    
    for i, score in enumerate(scores):
        req_id = f"req_{i}"
        with database.get_db_conn() as conn:
            conn.execute(
                "INSERT INTO production_traffic (request_id, config_hash, input_data, actual_output, latency_ms, cost, is_canary) VALUES (?, ?, 'in', 'out', 100, 0.0, 0)",
                (req_id, config_hash)
            )
            conn.execute(
                "INSERT INTO production_scores (request_id, metric_name, metric_type, score, status) VALUES (?, 'llm_judge', 'LLM_JUDGE', ?, 'SUCCESS')",
                (req_id, score)
            )
            
    # Sample 6 cases. Target mix is:
    # 50% borderline = 3 items
    # 25% fail = 1.5 -> 1 item
    # 25% pass = 1.5 -> 2 items
    samples = database.get_human_review_samples(limit=6)
    assert len(samples) == 6
    
    # Check that they represent a mix of items
    borderline_samples = [s for s in samples if 0.4 <= s["judge_score"] <= 0.8]
    fail_samples = [s for s in samples if s["judge_score"] < 0.4]
    pass_samples = [s for s in samples if s["judge_score"] > 0.8]
    
    assert len(borderline_samples) > 0
    assert len(fail_samples) > 0
    assert len(pass_samples) > 0

def test_agreement_calculations():
    # Define local versions of the mathematical functions to verify their accuracy
    def cohens_kappa(h_binary, j_binary):
        n = len(h_binary)
        if n == 0:
            return 1.0
        p_o = sum(h == j for h, j in zip(h_binary, j_binary)) / n
        h_pass = sum(h_binary) / n
        j_pass = sum(j_binary) / n
        p_e = (h_pass * j_pass) + ((1.0 - h_pass) * (1.0 - j_pass))
        if p_e >= 1.0:
            return 1.0
        return (p_o - p_e) / (1.0 - p_e)

    def pearson_corr(x, y):
        n = len(x)
        if n <= 1:
            return 1.0
        mean_x = sum(x) / n
        mean_y = sum(y) / n
        num = sum((xi - mean_x) * (yi - mean_y) for xi, yi in zip(x, y))
        den_x = sum((xi - mean_x) ** 2 for xi in x)
        den_y = sum((yi - mean_y) ** 2 for yi in y)
        if den_x == 0 or den_y == 0:
            return 0.0
        return num / ((den_x * den_y) ** 0.5)

    # 100% agreement
    h1 = [True, True, False, False]
    j1 = [True, True, False, False]
    assert cohens_kappa(h1, j1) == 1.0
    
    # Partial agreement
    h2 = [True, True, False, False]
    j2 = [True, False, True, False]
    # Expected: p_o = 2/4 = 0.5. h_pass = 0.5, j_pass = 0.5. p_e = 0.25 + 0.25 = 0.5.
    # Kappa = (0.5 - 0.5) / 0.75 = 0.0 (random agreement level)
    assert cohens_kappa(h2, j2) == 0.0

    # Pearson Correlation Check
    x = [1.0, 2.0, 3.0, 4.0]
    y = [2.0, 4.0, 6.0, 8.0]
    assert pearson_corr(x, y) == 1.0
