# LLM Evaluation and Observability Harness - Testing Documentation

This document outlines the testing strategy, test inventory, execution instructions, and simulation scenarios for validating the LLM Evaluation and Observability Harness.

---

## 1. Test Architecture Overview

The harness employs a multi-layered testing strategy to guarantee regression resistance, configuration integrity, and observation accuracy:
1. **Automated Unit & Integration Tests (CI Gate):** Python `pytest` suite validating individual scorers, database transactions, configuration constraints, API retries, and background queue workers.
2. **Canary Verification System:** End-to-end simulation of production observability, dynamically injecting fake-but-realistic failure payloads (canaries) into the live logging stream to verify the monitoring pipeline.
3. **Judge Alignment Monitoring:** Stratified human sampling loops comparing human scores against automated LLM-as-a-judge scores (via Cohen's Kappa and Correlation metrics) to prevent judge drift.

---

## 2. Running Automated Tests

Ensure your virtual environment is active before running tests.

### Execution Commands
* **Run All Tests:**
  ```bash
  .venv/Scripts/pytest
  ```
* **Run with Coverage Report:**
  ```bash
  .venv/Scripts/pytest --cov=eval_harness
  ```
* **Run Specific Test File:**
  ```bash
  .venv/Scripts/pytest tests/test_canary.py
  ```

---

## 3. Automated Test Suite Inventory

A total of **35 unit and integration tests** are defined in the `tests/` directory:

| Test File | Target Component | Verifies |
| :--- | :--- | :--- |
| **`test_config.py`** | `eval_harness/config.py` | Config validations, key length limits, sampling bounds, and placeholder rejections. |
| **`test_database.py`** | `eval_harness/database.py` | Schema initialization, FK constraint enforcement, and clean state run-idempotency. |
| **`test_runner.py`** | `eval_harness/runner.py` | CLI execution pipeline, mock LLM execution, and regression threshold detection. |
| **`test_sampler_worker.py`** | `eval_harness/sampler.py` & `worker.py` | PII regex masking (emails, phones, CCs, SSNs), sampling triggers, and background consumer queue worker logic. |
| **`test_scorers.py`** | `eval_harness/scorers.py` | Rule scorers (regex, JSON, length) and LLM-as-a-judge score averaging/variance computation. |
| **`test_alerts_dashboard.py`** | `eval_harness/alerts.py` & `dashboard.py` | Rolling regression computation, Slack webhook triggering, and dashboard query functions. |
| **`test_canary.py`** | `eval_harness/canary_lib.py` & `worker.py` | Canary insertion, worker detection/scoring, expected score comparison, and monitor-broken alerts. |
| **`test_agreement.py`** | `eval_harness/database.py` & `dashboard.py` | Human review scores saving, stratified queue sampling, and Cohen's Kappa / Pearson correlation math. |
| **`test_code_review_issues.py`** | `eval_harness/client.py` & `scorers.py` | Fail-fast client behavior on HTTP 400 client errors (no unnecessary retries) and global random seed encapsulation. |

---

## 4. End-to-End Simulation & Verification Steps

To test the system against simulated real-world conditions, follow these steps:

### Scenario A: Simulating CI/CD Regressions
1. **Initialize the Database:**
   ```bash
   .venv/Scripts/python -m eval_harness.runner init-db
   ```
2. **Establish the Baseline Run:**
   ```bash
   .venv/Scripts/python -m eval_harness.runner run-eval --test-set tests/test_set_example.json --run-id baseline_run_1 --is-baseline
   ```
3. **Execute a Comparison Run:**
   ```bash
   .venv/Scripts/python -m eval_harness.runner run-eval --test-set tests/test_set_example.json --run-id comparison_run_1
   ```
4. **Compare for regressions:**
   ```bash
   .venv/Scripts/python -m eval_harness.runner compare-regression --run-id comparison_run_1
   ```
   *(Exits with status `0` if scores are stable, or status `1` if regression exceeds the threshold).*

### Scenario B: Observability Monitor & Canary Verification
1. **Launch the Background Worker:**
   ```bash
   .venv/Scripts/python -m eval_harness.worker
   ```
2. **Launch the Streamlit Web Dashboard:**
   ```bash
   .venv/Scripts/streamlit run eval_harness/dashboard.py
   ```
3. **Generate Production Logs & Injects Canaries:**
   In your application backend (or python scratch script), invoke the traffic logger:
   ```python
   from eval_harness import sampler
   sampler.log_production_traffic(
       config_hash="5a5d67052feb5e4cd9c285ba4311d7667b012be7110cf843e52a00d144c64199",
       input_data="Query SSN 000-12-3456",
       actual_output="Output with CC: 1111-2222-3333-4444",
       latency_ms=150.0,
       cost=0.0001
   )
   ```
   *(This automatically logs the traffic and randomly injects a canary record).*
4. **Verify Canary caught on Dashboard:**
   Open the **Live Production Monitoring** tab on the dashboard. Confirm the **Canary Recall** displays `100.0%` and **Canary False-Positive Rate** displays `0.0%`.

### Scenario C: Blind Human Review & Judge Alignment
1. Open the dashboard browser tab.
2. Go to the **Human Review & Judge Alignment Portal** tab.
3. Review the pending sampled records blind (inputs and AI outputs are shown, but the LLM judge's grade is hidden).
4. Select "Pass" or "Fail", set the score slider, and click **Submit Grade**.
5. Once several reviews are submitted, verify the **Cohen's Kappa (Agreement)** and **Pearson Correlation** values calculate and update. If Kappa falls below `0.6`, a critical warning will display indicating the LLM judge needs revision.
