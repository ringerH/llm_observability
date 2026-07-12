-- 1. Prompts & Model Configurations (Immutable, versioned by content hash)
CREATE TABLE IF NOT EXISTS prompts_config (
    config_hash TEXT PRIMARY KEY,
    prompt_template TEXT NOT NULL,
    model_name TEXT NOT NULL,
    parameters TEXT NOT NULL, -- JSON string of temperature, max_tokens, etc.
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 2. Evaluation Runs
CREATE TABLE IF NOT EXISTS eval_runs (
    run_id TEXT PRIMARY KEY,
    config_hash TEXT NOT NULL,
    status TEXT NOT NULL, -- 'PENDING', 'RUNNING', 'COMPLETED', 'FAILED'
    summary TEXT, -- JSON string: {total_cases, passed, failed, avg_score, cost, duration_ms}
    is_baseline BOOLEAN DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (config_hash) REFERENCES prompts_config(config_hash)
);

-- 3. Evaluation Test Cases (Definitions of test sets)
CREATE TABLE IF NOT EXISTS test_cases (
    case_id TEXT PRIMARY KEY,
    input_data TEXT NOT NULL, -- JSON or string input to prompt
    expected_output TEXT, -- Optional ground truth
    rubric TEXT, -- Optional evaluation rubric/rules for judge
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 4. Case Run Results (One row per case per run)
CREATE TABLE IF NOT EXISTS eval_case_results (
    run_id TEXT NOT NULL,
    case_id TEXT NOT NULL,
    actual_output TEXT, -- LLM-under-test response
    latency_ms REAL,
    cost REAL,
    error_message TEXT, -- Null if success, string if LLM-under-test failed
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (run_id, case_id),
    FOREIGN KEY (run_id) REFERENCES eval_runs(run_id),
    FOREIGN KEY (case_id) REFERENCES test_cases(case_id)
);

-- 5. Individual Metric Evaluations (Rules & LLM-as-judge outputs)
CREATE TABLE IF NOT EXISTS eval_metrics (
    run_id TEXT NOT NULL,
    case_id TEXT NOT NULL,
    metric_name TEXT NOT NULL, -- 'regex_match', 'json_format', 'llm_judge_helpfulness', etc.
    metric_type TEXT NOT NULL, -- 'RULE' or 'LLM_JUDGE'
    score REAL, -- Numeric score (e.g. 0.0 to 1.0)
    explanation TEXT, -- Context, regex failures, or judge reasoning
    status TEXT NOT NULL, -- 'SUCCESS', 'FAILED' (if grader/judge failed)
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (run_id, case_id, metric_name),
    FOREIGN KEY (run_id, case_id) REFERENCES eval_case_results(run_id, case_id)
);

-- 6. Sampled Production Traffic (Immutable log, sampled at 3 req/sec)
CREATE TABLE IF NOT EXISTS production_traffic (
    request_id TEXT PRIMARY KEY,
    config_hash TEXT NOT NULL,
    input_data TEXT NOT NULL, -- PII-masked inputs
    actual_output TEXT NOT NULL, -- PII-masked outputs
    latency_ms REAL NOT NULL,
    cost REAL NOT NULL,
    sampled_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (config_hash) REFERENCES prompts_config(config_hash)
);

-- 7. Production Traffic Metric Scores (Async evaluated by worker)
CREATE TABLE IF NOT EXISTS production_scores (
    request_id TEXT NOT NULL,
    metric_name TEXT NOT NULL,
    metric_type TEXT NOT NULL,
    score REAL,
    explanation TEXT,
    status TEXT NOT NULL, -- 'SUCCESS', 'FAILED'
    evaluated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (request_id, metric_name),
    FOREIGN KEY (request_id) REFERENCES production_traffic(request_id)
);
