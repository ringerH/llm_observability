import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import streamlit as st
import pandas as pd
import json
import sqlite3
from typing import List
from eval_harness.config import settings
from eval_harness import database, alerts

st.set_page_config(
    page_title="LLM Evaluation & Observability Dashboard",
    layout="wide",
    initial_sidebar_state="expanded",
)


# Helper to execute query and return DataFrame
def query_db(query: str, params: tuple = ()) -> pd.DataFrame:
    db_path = settings.DATABASE_PATH
    conn = sqlite3.connect(db_path)
    try:
        df = pd.read_sql_query(query, conn, params=params)
        return df
    finally:
        conn.close()


# Initialize DB connection and fetch basic stats
st.title("LLM Evaluation & Observability Dashboard")

tab1, tab2, tab3, tab4 = st.tabs(
    [
        "CI/CD Offline Evaluations",
        "Live Production Monitoring",
        "Human Review & Judge Alignment",
        "Live Chatbot Sandbox (Test System)",
    ]
)

# ----------------- Tab 1: CI/CD Offline Evaluations -----------------
with tab1:
    st.header("Evaluation Runs history")

    # Fetch list of runs
    runs_df = query_db(
        """
        SELECT r.run_id, r.config_hash, r.status, r.is_baseline, r.created_at, p.model_name
        FROM eval_runs r
        JOIN prompts_config p ON r.config_hash = p.config_hash
        ORDER BY r.created_at DESC
        """
    )

    if runs_df.empty:
        st.info(
            "No evaluation runs found in the database. Run your CLI evaluation pipeline first."
        )
    else:
        # Run Selector
        run_options = runs_df.apply(
            lambda row: (
                f"{row['run_id']} - {row['model_name']} ({row['status']}) {'[BASELINE]' if row['is_baseline'] else ''}"
            ),
            axis=1,
        ).tolist()

        selected_option = st.selectbox("Select Evaluation Run:", run_options)
        selected_run_id = selected_option.split(" - ")[0]

        # Details of selected run
        run_detail = query_db(
            """
            SELECT r.*, p.prompt_template, p.model_name, p.parameters
            FROM eval_runs r
            JOIN prompts_config p ON r.config_hash = p.config_hash
            WHERE r.run_id = ?
            """,
            (selected_run_id,),
        ).iloc[0]

        # Metadata Layout
        col1, col2, col3 = st.columns(3)
        with col1:
            st.metric("Status", str(run_detail["status"]))
            st.text(f"Created: {run_detail['created_at']}")
        with col2:
            st.metric("Model Name", str(run_detail["model_name"]))
            st.text(f"Config Hash: {run_detail['config_hash']}")
        with col3:
            st.metric("Is Baseline", "Yes" if run_detail["is_baseline"] else "No")
            st.text(f"Run ID: {run_detail['run_id']}")

        st.subheader("Prompt Template")
        st.code(run_detail["prompt_template"])

        # Summary Metrics
        if run_detail["summary"]:
            summary_dict = json.loads(run_detail["summary"])

            m1, m2, m3, m4, m5, m6 = st.columns(6)
            m1.metric("Total Cases", summary_dict.get("total_cases", 0))
            m2.metric("Passed Cases", summary_dict.get("passed", 0))
            m3.metric("Failed Cases", summary_dict.get("failed", 0))
            m4.metric("Avg Score", f"{summary_dict.get('avg_score', 0.0):.2%}")
            m5.metric("Total Cost", f"${summary_dict.get('cost', 0.0):.6f}")
            m6.metric(
                "Judge Disagreement",
                f"{summary_dict.get('judge_disagreement_rate', 0.0):.2%}",
            )

        # Case Results details
        st.subheader("Case Details")
        case_results_df = query_db(
            """
            SELECT cr.case_id, cr.actual_output, cr.latency_ms, cr.cost, cr.error_message, tc.input_data, tc.expected_output
            FROM eval_case_results cr
            JOIN test_cases tc ON cr.case_id = tc.case_id
            WHERE cr.run_id = ?
            """,
            (selected_run_id,),
        )

        for _, case_row in case_results_df.iterrows():
            case_id = case_row["case_id"]

            # Fetch scores for this case
            metrics_df = query_db(
                """
                SELECT metric_name, score, explanation, status
                FROM eval_metrics
                WHERE run_id = ? AND case_id = ?
                """,
                (selected_run_id, case_id),
            )

            status_color = (
                "red"
                if case_row["error_message"]
                or (not metrics_df.empty and (metrics_df["score"] < 0.5).any())
                else "green"
            )

            with st.expander(
                f"Case: {case_id} ({'FAILED' if status_color == 'red' else 'PASSED'})"
            ):
                sc1, sc2 = st.columns(2)
                with sc1:
                    st.write("**Input Data:**")
                    st.write(case_row["input_data"])
                    st.write("**Expected Output:**")
                    st.write(case_row["expected_output"] or "None")
                with sc2:
                    st.write("**Actual Output:**")
                    if case_row["error_message"]:
                        st.error(case_row["error_message"])
                    else:
                        st.write(case_row["actual_output"])

                st.write(
                    f"**Latency:** {case_row['latency_ms']:.1f}ms | **Cost:** ${case_row['cost']:.6f}"
                )

                if not metrics_df.empty:
                    st.write("**Scorer Evaluations:**")
                    st.table(metrics_df)

# ----------------- Tab 2: Live Production Monitoring -----------------
with tab2:
    st.header("Live Production Metrics")

    # Fetch live traffic summary stats (exclude canaries)
    traffic_count_df = query_db(
        "SELECT count(*) as cnt, sum(cost) as total_cost, avg(latency_ms) as avg_lat FROM production_traffic WHERE is_canary = 0"
    )
    total_reqs = traffic_count_df.iloc[0]["cnt"]
    total_prod_cost = traffic_count_df.iloc[0]["total_cost"] or 0.0
    avg_prod_lat = traffic_count_df.iloc[0]["avg_lat"] or 0.0

    # Calculate rolling regression rate (exclude canaries internally handled in database.py)
    rolling_regression_rate = database.get_rolling_regression_rate(
        limit=100, threshold=settings.REGRESSION_THRESHOLD
    )

    # Key Performance Indicators Layout
    p1, p2, p3, p4 = st.columns(4)
    p1.metric("Total Production Requests", total_reqs)
    p2.metric("Accumulated Cost", f"${total_prod_cost:.6f}")
    p3.metric("Average Latency", f"{avg_prod_lat:.1f} ms")

    # Alert state for regression
    alert_triggered = rolling_regression_rate > settings.REGRESSION_THRESHOLD
    p4.metric(
        "Rolling Regression Rate",
        f"{rolling_regression_rate:.2%}",
        delta=f"Threshold: {settings.REGRESSION_THRESHOLD:.2%}",
        delta_color="inverse" if alert_triggered else "normal",
    )

    if alert_triggered:
        st.error(
            f"WARNING: Rolling regression rate {rolling_regression_rate:.2%} is exceeding safe thresholds!"
        )
        # Option to manually trigger / fire webhook
        if st.button("Trigger Webhook Alert"):
            fired = alerts.fire_webhook_alert(
                rolling_regression_rate, settings.REGRESSION_THRESHOLD
            )
            if fired:
                st.success("Alert notification sent successfully.")
            else:
                st.error("Failed to send webhook alert.")

    # Observability Monitor Health (Canary Verification)
    st.subheader("Observability Monitor Health (Canary Verification)")
    recall, fpr, canary_count = database.get_canary_health_metrics()
    c1, c2, c3 = st.columns(3)
    c1.metric("Canaries Processed", canary_count)
    c2.metric(
        "Canary Recall (Caught Rate)",
        f"{recall:.2%}",
        delta="Expected: 100.0%",
        delta_color="normal" if recall >= 1.0 else "inverse",
    )
    c3.metric(
        "Canary False-Positive Rate",
        f"{fpr:.2%}",
        delta="Expected: 0.0%",
        delta_color="normal" if fpr <= 0.0 else "inverse",
    )

    # Historical trends
    st.subheader("Performance Trends")

    # Cost, Latency and quality score trends over time (exclude canaries)
    trends_df = query_db(
        """
        SELECT t.sampled_at, t.latency_ms, t.cost, avg(s.score) as avg_score
        FROM production_traffic t
        LEFT JOIN production_scores s ON t.request_id = s.request_id
        WHERE t.is_canary = 0
        GROUP BY t.request_id
        ORDER BY t.sampled_at ASC
        """
    )

    if trends_df.empty:
        st.info(
            "No production traffic logs found in database. Run production mock calls to view dashboards."
        )
    else:
        trends_df["sampled_at"] = pd.to_datetime(trends_df["sampled_at"])
        trends_df.set_index("sampled_at", inplace=True)

        tc1, tc2 = st.columns(2)
        with tc1:
            st.write("**Rolling Quality Score Trend**")
            st.line_chart(trends_df["avg_score"])
        with tc2:
            st.write("**Production Latency (ms)**")
            st.line_chart(trends_df["latency_ms"])

        st.write("**Accumulated Token Cost ($)**")
        st.area_chart(trends_df["cost"].cumsum())

    # Raw Production Traffic Log explorer (exclude canaries)
    st.subheader("Sampled Logs Explorer")
    prod_logs_df = query_db(
        """
        SELECT t.request_id, t.config_hash, t.input_data, t.actual_output, t.latency_ms, t.cost, t.sampled_at
        FROM production_traffic t
        WHERE t.is_canary = 0
        ORDER BY t.sampled_at DESC
        """
    )
    st.dataframe(prod_logs_df, use_container_width=True)

# ----------------- Tab 3: Human Review & Judge Alignment -----------------
with tab3:
    st.header("Human Review & Judge Alignment Portal")

    # 1. Blind Human Review Section
    st.subheader("Blind Human Evaluation Queue")

    # Stratified sample queue
    sample_records = database.get_human_review_samples(limit=1)

    if not sample_records:
        st.info(
            "No pending requests in the review queue. Run more live production requests first."
        )
    else:
        sample = sample_records[0]
        req_id = sample["request_id"]

        with st.form("human_evaluation_form"):
            st.write(f"**Request ID:** `{req_id}`")
            st.write("**User Input Prompt:**")
            st.code(sample["input_data"])

            st.write("**AI Assistant Response:**")
            st.info(sample["actual_output"])

            st.write("Rate this response:")
            cols = st.columns(2)
            is_pass = cols.radio("Verdict:", ["Pass", "Fail"], index=0)
            score_slider = cols.slider(
                "Score (optional detail):", 0.0, 1.0, 1.0 if is_pass == "Pass" else 0.0
            )

            submitted = st.form_submit_button("Submit Grade")
            if submitted:
                # Save review
                database.save_human_score(req_id, score_slider)
                st.success("Human grade successfully logged!")
                st.rerun()

    # 2. Judge Alignment Statistics Section
    st.subheader("Judge Alignment Statistics")

    comparison_data = database.get_human_judge_comparison_data()
    if not comparison_data:
        st.info(
            "Submit some human grades in the blind review queue above to calculate alignment statistics."
        )
    else:
        df_comp = pd.DataFrame(comparison_data)

        # Binary classifications (threshold at 0.5)
        df_comp["human_binary"] = df_comp["human_score"] >= 0.5
        df_comp["judge_binary"] = df_comp["judge_score"] >= 0.5

        # Calculate agreement metrics
        from typing import List

        def cohens_kappa(h_binary: List[bool], j_binary: List[bool]) -> float:
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

        def pearson_corr(x: List[float], y: List[float]) -> float:
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

        kappa = cohens_kappa(
            df_comp["human_binary"].tolist(), df_comp["judge_binary"].tolist()
        )
        correlation = pearson_corr(
            df_comp["human_score"].tolist(), df_comp["judge_score"].tolist()
        )

        # Alert display
        if kappa < 0.6:
            st.error(
                f"⚠️ CRITICAL DRIFT: LLM Judge Needs Review! Cohen's Kappa = {kappa:.2f} (Target >= 0.6)"
            )
        else:
            st.success(
                f"✅ ALIGNED: LLM Judge meets alignment targets. Cohen's Kappa = {kappa:.2f}"
            )

        # Metrics cards
        m1, m2, m3 = st.columns(3)
        m1.metric("Total Reviewed Items", len(df_comp))
        m2.metric(
            "Cohen's Kappa (Agreement)",
            f"{kappa:.2f}",
            delta="Target: >= 0.6",
            delta_color="normal" if kappa >= 0.6 else "inverse",
        )
        m3.metric("Pearson Correlation Coefficient", f"{correlation:.2f}")

        # Breakdown of disagreements
        st.subheader("Disagreement Classification Breakdown")

        # Categorize matches and errors
        too_strict = df_comp[(df_comp["human_binary"]) & (not df_comp["judge_binary"])]
        too_lenient = df_comp[(not df_comp["human_binary"]) & (df_comp["judge_binary"])]
        exact_agreements = df_comp[df_comp["human_binary"] == df_comp["judge_binary"]]

        b1, b2, b3 = st.columns(3)
        b1.metric(
            "Exact Agreements",
            f"{len(exact_agreements)}",
            delta=f"{len(exact_agreements) / len(df_comp):.1%}",
        )
        b2.metric(
            "Judge Too Strict (False Negatives)",
            f"{len(too_strict)}",
            delta=f"{len(too_strict) / len(df_comp):.1%}",
            delta_color="inverse",
        )
        b3.metric(
            "Judge Too Lenient (False Positives)",
            f"{len(too_lenient)}",
            delta=f"{len(too_lenient) / len(df_comp):.1%}",
            delta_color="inverse",
        )

        # Disagreement breakdown display table
        st.subheader("Raw Human vs Judge Scores Table")
        st.dataframe(
            df_comp[
                [
                    "request_id",
                    "human_score",
                    "judge_score",
                    "human_binary",
                    "judge_binary",
                ]
            ],
            use_container_width=True,
        )

# ----------------- Tab 4: Live Chatbot Sandbox (Test System) -----------------
with tab4:
    import time

    st.header("Live Chatbot Sandbox")
    st.write(
        "Use this interactive sandbox to chat with a simulated/live chatbot. "
        "Every interaction is intercepted by the observability middleware, masked for PII, and pushed to the queue database."
    )

    # Inform the user about the background worker and sampling rate configuration
    st.info(
        "💡 **How to test evaluation:** \n"
        "1. Open another terminal in your workspace and run the worker: `python -m eval_harness.worker`.\n"
        "2. The worker will pick up your chat messages from the queue and evaluate them in real-time."
    )

    st.warning(
        f"⚠️ **Note on Sampling:** Your current `.env` has `SAMPLING_RATE={settings.SAMPLING_RATE}`. "
        "Only that percentage of requests will be logged to the database queue. "
        "If you want to log every message for testing, edit `.env` and set `SAMPLING_RATE=1.0` (then restart the dashboard)."
    )

    # Initialize chat history
    if "chat_messages" not in st.session_state:
        st.session_state.chat_messages = []

    # Display chat messages
    for msg in st.session_state.chat_messages:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])
            if "request_id" in msg and msg["request_id"]:
                st.caption(
                    f"Request ID: `{msg['request_id']}` | Status: {msg.get('status', 'Logged to queue')}"
                )

                # Fetch scores if worker has processed it
                scores_df = query_db(
                    """
                    SELECT metric_name, score, explanation, status
                    FROM production_scores
                    WHERE request_id = ?
                    """,
                    (msg["request_id"],),
                )
                if not scores_df.empty:
                    st.dataframe(scores_df, use_container_width=True)

    # Chat input
    if prompt := st.chat_input(
        "Type a message to the chatbot (try entering PII like 'test@example.com' to see masking in action!):"
    ):
        # Display user message
        with st.chat_message("user"):
            st.markdown(prompt)
        st.session_state.chat_messages.append({"role": "user", "content": prompt})

        # Generate response
        with st.chat_message("assistant"):
            message_placeholder = st.empty()
            message_placeholder.markdown("*Thinking...*")

            start_time = time.time()
            try:
                # Call Gemini/OpenAI if configured, otherwise fallback to mock response
                from eval_harness.client import call_llm

                response = call_llm(prompt)
            except Exception:
                response = f"Hello! (Mock Response) I received your query. If you configure valid API credentials in .env, I will call the real LLM. You sent: '{prompt}'."

            latency_ms = (time.time() - start_time) * 1000
            cost = (len(prompt) + len(response)) * 0.000002

            # Use the sampler middleware to log the traffic
            from eval_harness import sampler

            request_id = sampler.log_production_traffic(
                config_hash="chatbot_production_v1.0",
                input_data=prompt,
                actual_output=response,
                latency_ms=latency_ms,
                cost=cost,
            )

            message_placeholder.markdown(response)

            status_text = (
                "Logged to queue (Processing...)"
                if request_id
                else "Skipped (Sampling rate check)"
            )
            st.session_state.chat_messages.append(
                {
                    "role": "assistant",
                    "content": response,
                    "request_id": request_id,
                    "status": status_text,
                }
            )

            if request_id:
                st.success(
                    f"Successfully logged request to queue database! Request ID: `{request_id}`"
                )
            else:
                st.warning(
                    "Request was not logged because SAMPLING_RATE did not select it, or SAMPLER_KILL_SWITCH is enabled."
                )

            st.rerun()
