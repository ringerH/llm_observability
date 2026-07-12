import streamlit as st
import pandas as pd
import json
import sqlite3
from typing import Dict, Any, List
from eval_harness.config import settings
from eval_harness import database, alerts

st.set_page_config(
    page_title="LLM Evaluation & Observability Dashboard",
    layout="wide",
    initial_sidebar_state="expanded"
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

tab1, tab2 = st.tabs(["CI/CD Offline Evaluations", "Live Production Monitoring"])

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
        st.info("No evaluation runs found in the database. Run your CLI evaluation pipeline first.")
    else:
        # Run Selector
        run_options = runs_df.apply(
            lambda row: f"{row['run_id']} - {row['model_name']} ({row['status']}) {'[BASELINE]' if row['is_baseline'] else ''}",
            axis=1
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
            (selected_run_id,)
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
            m6.metric("Judge Disagreement", f"{summary_dict.get('judge_disagreement_rate', 0.0):.2%}")

        # Case Results details
        st.subheader("Case Details")
        case_results_df = query_db(
            """
            SELECT cr.case_id, cr.actual_output, cr.latency_ms, cr.cost, cr.error_message, tc.input_data, tc.expected_output
            FROM eval_case_results cr
            JOIN test_cases tc ON cr.case_id = tc.case_id
            WHERE cr.run_id = ?
            """,
            (selected_run_id,)
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
                (selected_run_id, case_id)
            )

            status_color = "red" if case_row["error_message"] or (not metrics_df.empty and (metrics_df["score"] < 0.5).any()) else "green"
            
            with st.expander(f"Case: {case_id} ({'FAILED' if status_color == 'red' else 'PASSED'})"):
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
                        
                st.write(f"**Latency:** {case_row['latency_ms']:.1f}ms | **Cost:** ${case_row['cost']:.6f}")
                
                if not metrics_df.empty:
                    st.write("**Scorer Evaluations:**")
                    st.table(metrics_df)

# ----------------- Tab 2: Live Production Monitoring -----------------
with tab2:
    st.header("Live Production Metrics")

    # Fetch live traffic summary stats
    traffic_count_df = query_db("SELECT count(*) as cnt, sum(cost) as total_cost, avg(latency_ms) as avg_lat FROM production_traffic")
    total_reqs = traffic_count_df.iloc[0]["cnt"]
    total_prod_cost = traffic_count_df.iloc[0]["total_cost"] or 0.0
    avg_prod_lat = traffic_count_df.iloc[0]["avg_lat"] or 0.0

    # Calculate rolling regression rate
    rolling_regression_rate = database.get_rolling_regression_rate(limit=100, threshold=settings.REGRESSION_THRESHOLD)

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
        delta_color="inverse" if alert_triggered else "normal"
    )

    if alert_triggered:
        st.error(f"WARNING: Rolling regression rate {rolling_regression_rate:.2%} is exceeding safe thresholds!")
        # Option to manually trigger / fire webhook
        if st.button("Trigger Webhook Alert"):
            fired = alerts.fire_webhook_alert(rolling_regression_rate, settings.REGRESSION_THRESHOLD)
            if fired:
                st.success("Alert notification sent successfully.")
            else:
                st.error("Failed to send webhook alert.")

    # Historical trends
    st.subheader("Performance Trends")
    
    # Cost, Latency and quality score trends over time
    trends_df = query_db(
        """
        SELECT t.sampled_at, t.latency_ms, t.cost, avg(s.score) as avg_score
        FROM production_traffic t
        LEFT JOIN production_scores s ON t.request_id = s.request_id
        GROUP BY t.request_id
        ORDER BY t.sampled_at ASC
        """
    )

    if trends_df.empty:
        st.info("No production traffic logs found in database. Run production mock calls to view dashboards.")
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

    # Raw Production Traffic Log explorer
    st.subheader("Sampled Logs Explorer")
    prod_logs_df = query_db(
        """
        SELECT t.request_id, t.config_hash, t.input_data, t.actual_output, t.latency_ms, t.cost, t.sampled_at
        FROM production_traffic t
        ORDER BY t.sampled_at DESC
        """
    )
    st.dataframe(prod_logs_df, use_container_width=True)
