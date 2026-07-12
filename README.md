# LLM Evaluation and Observability Harness

This repository contains a production-ready LLM evaluation and observability harness designed to run offline evaluation checks in CI/CD pipelines and sample/observe traffic quality in live production environments. 

The harness relies on a shared scoring core and adheres to a "failure-first" design philosophy, degrading gracefully during third-party LLM API failures, rate limits, or malformed responses.

---

## Key Features

1. **Robust Offline Evaluations:** Runs evaluation suites using rule-based metrics (JSON syntax, Regex match, character length) and multiple-trial LLM-as-a-judge scoring.
2. **Regression Detection CLI:** Automatically detects quality regressions by comparing test run scores against designated baseline versions.
3. **Structured Observability Logs:** Outputs structured logs in JSON format with built-in PII redaction.
4. **Live Production Sampler:** Wrap outbound LLM requests with configured sampling rates, safety-mask PII, and push to an database-backed queue.
5. **Background Queue Worker:** An independent queue consumer that evaluates sampled requests asynchronously, exposing liveness (`/health`) and readiness (`/ready`) endpoints.
6. **Hardened Containerization:** Fully containerized utilizing a multi-stage Docker build running under non-root privilege contexts.

---

## Project Structure

```
├── .github/workflows/
│   └── regression.yml       # GitHub Actions CI workflow
├── eval_harness/
│   ├── __init__.py
│   ├── alerts.py            # Webhook alerting logic for quality regression alerts
│   ├── client.py            # Third-party LLM API client with tenacity retries
│   ├── config.py            # Pydantic configuration validation
│   ├── dashboard.py         # Streamlit interactive UI dashboard
│   ├── database.py          # SQLite database connection & execution wrapper
│   ├── logging.py           # Structured JSON logger with secret redaction
│   ├── runner.py            # CLI entrypoint for database initialization & runs
│   ├── sampler.py           # PII masking & production traffic sampler
│   ├── schema.sql           # SQLite database schema definition
│   ├── scorers.py           # Rule-based & LLM-as-judge scoring implementations
│   └── worker.py            # Background queue consumer & health check HTTP server
├── tests/                   # Pytest test suite targeting all components
├── Dockerfile               # Hardened multi-stage Docker configuration
├── .dockerignore            # Excludes build environment, credentials, and databases
├── pyproject.toml           # Poetry packaging configuration
└── requirements.txt         # Pinned python package dependencies
```

---

## Setup and Installation

### 1. Requirements
* Python 3.10 or higher
* SQLite 3

### 2. Install Dependencies
Initialize your virtual environment and install package dependencies:
```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

### 3. Environment Configuration
Create a `.env` file in the workspace root using the template below:
```ini
GEMINI_API_KEY=YOUR_GEMINI_DEVELOPER_API_KEY
OPENAI_API_KEY=YOUR_OPENAI_API_KEY
DATABASE_PATH=eval.db
SAMPLING_RATE=0.1
MAX_SAMPLING_RATE=0.5
SAMPLER_KILL_SWITCH=False
REGRESSION_THRESHOLD=0.05
```
*Note: The harness checks for placeholder keys (like `CHANGE_ME`) and suspiciously short credentials, raising validation errors on startup if present.*

---

## Usage Instructions

### Offline Evaluation Pipeline (CI Gate)

#### 1. Initialize the SQLite Database
Create database tables and run schema migrations:
```bash
python -m eval_harness.runner init-db
```

#### 2. Run Evaluations
Run your evaluation test sets. By default, this mock-evaluates results unless `--real-llm` is passed:
```bash
python -m eval_harness.runner run-eval --test-set tests/test_set_example.json --run-id pr_run_123
```
To mark this run as the baseline target, add the `--is-baseline` flag.

#### 3. Compare for Regressions
Compare a recent run's quality scores against its active baseline configuration:
```bash
python -m eval_harness.runner compare-regression --run-id pr_run_123
```
This CLI exits with status code `1` if regressions exceed the `REGRESSION_THRESHOLD` or if no baseline exists.

---

### Production Observability Pipeline

#### 1. Sampling Production Traffic
Use the sampler utility inside your application middleware or route handlers:
```python
from eval_harness import sampler

sampler.log_production_traffic(
    config_hash="your_config_hash",
    input_data="User input email user@example.com",
    actual_output="Output text containing SSN: 111-22-3333",
    latency_ms=120.5,
    cost=0.000075
)
```
This masks standard PII fields and pushes the sampled request to the SQLite database queue.

#### 2. Start the Background Consumer Worker
Spin up the worker to process, score, and persist logs:
```bash
python -m eval_harness.worker
```
The worker starts an HTTP server (defaulting to port `8000`) exposing `/health` and `/ready` checks, and intercepts `SIGTERM` / `SIGINT` signals for graceful process shutdown.

#### 3. Run the Dashboard
To start the Streamlit web dashboard to visualize evaluation run histories and production traffic trends:
```bash
streamlit run eval_harness/dashboard.py
```

---

## Containerization and CI/CD

### Docker Build
Build the production Docker image containing only runtime dependencies:
```bash
docker build -t llm-eval-harness:latest .
```

### CI/CD Workflow
The pipeline defined in `.github/workflows/regression.yml` automates the following actions on pull requests:
1. Builds the Docker image.
2. Initializes the evaluation database.
3. Performs a baseline run and a comparison PR run.
4. Executes the `compare-regression` command, blocking PR merges if quality drops.

---

## Running Verification Tests

Run the full automated test suite containing unit coverage for configuration, schema mappings, scorers, fallback logic, sampling, and background processing:
```bash
pytest
```
