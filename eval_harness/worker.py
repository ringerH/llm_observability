import time
import sys
import signal
import threading
import json
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Dict, Any, List, Optional
from eval_harness.config import settings
from eval_harness import database, scorers, logging

# Global shutdown event and status flags
shutdown_event = threading.Event()
worker_healthy = True

class HealthCheckHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        # Suppress request logs to keep stdout clean
        pass

    def do_GET(self):
        global worker_healthy
        if self.path == "/health":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"status": "healthy"}')
        elif self.path == "/ready":
            # Check database connection health
            db_ok = False
            try:
                with database.get_db_conn() as conn:
                    conn.execute("SELECT 1")
                db_ok = True
            except Exception:
                pass
            
            if db_ok and worker_healthy:
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"status": "ready"}')
            else:
                self.send_response(503)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"status": "unhealthy"}')
        else:
            self.send_response(404)
            self.end_headers()

def start_health_server(port: int = 8000) -> HTTPServer:
    server = HTTPServer(("0.0.0.0", port), HealthCheckHandler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    return server

def signal_handler(signum, frame):
    logging.log_info(f"Signal {signum} received. Initiating graceful shutdown...")
    shutdown_event.set()

def process_single_request(req: Dict[str, Any]) -> None:
    request_id = req["request_id"]
    config_hash = req["config_hash"]
    input_data = req["input_data"]
    actual_output = req["actual_output"]

    # 1. Fetch rules configuration from database for this config_hash
    with database.get_db_conn() as conn:
        config_row = conn.execute(
            "SELECT parameters FROM prompts_config WHERE config_hash = ?", (config_hash,)
        ).fetchone()

    rules = {}
    if config_row:
        try:
            params = json.loads(config_row["parameters"])
            rules = params.get("rules", {})
        except Exception as e:
            logging.log_error(f"Failed to parse parameters for config {config_hash}: {e}")

    # Fallback to length check if no rules are stored
    if not rules:
        rules = {"length_check": {"min": 1}}

    # 2. Run scorers on the production output
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
                rubric_val = rule_config.get("rubric") if isinstance(rule_config, dict) else None
                trials_val = rule_config.get("trials", 3) if isinstance(rule_config, dict) else 3
                # Force mock LLM judge scoring for production metrics to avoid live API charges/delay
                scorer_inst = scorers.LlmJudgeScorer(rubric=rubric_val, trials=trials_val, mock=True)

            if scorer_inst:
                res = scorer_inst.score(input_val=input_data, output_val=actual_output)
                database.save_production_score(
                    request_id=request_id,
                    metric_name=rule_name,
                    metric_type=scorer_inst.metric_type,
                    score=res.score,
                    explanation=res.explanation,
                    status=res.status
                )
            else:
                logging.log_warn(f"Unknown rule scorer: {rule_name}")

        except Exception as ex:
            logging.log_error(
                f"Failed to evaluate production metric '{rule_name}' for request {request_id}",
                exc_info=ex
            )
            database.save_production_score(
                request_id=request_id,
                metric_name=rule_name,
                metric_type="RULE" if rule_name != "llm_judge" else "LLM_JUDGE",
                score=0.0,
                explanation=f"Worker evaluation error: {type(ex).__name__} {str(ex)}",
                status="FAILED"
            )

def run_worker_loop():
    global worker_healthy
    logging.log_info("Background worker loop started.")

    while not shutdown_event.is_set():
        try:
            # Query one pending request that lacks scores
            with database.get_db_conn() as conn:
                row = conn.execute(
                    """
                    SELECT request_id, config_hash, input_data, actual_output, latency_ms, cost 
                    FROM production_traffic 
                    WHERE request_id NOT IN (SELECT DISTINCT request_id FROM production_scores)
                    LIMIT 1
                    """
                ).fetchone()

            if row:
                req = dict(row)
                logging.log_info(f"Processing production request {req['request_id']}")
                process_single_request(req)
                logging.log_info(f"Successfully processed production request {req['request_id']}")
            else:
                time.sleep(0.5)
                
            worker_healthy = True
        except Exception as e:
            logging.log_error(f"Error in worker main loop: {e}", exc_info=e)
            worker_healthy = False
            time.sleep(1.0)

def main():
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)

    health_port = 8000
    logging.log_info(f"Starting health server on port {health_port}...")
    server = start_health_server(health_port)

    try:
        run_worker_loop()
    finally:
        logging.log_info("Shutting down health server...")
        server.shutdown()
        server.server_close()
        logging.log_info("Graceful shutdown completed successfully.")

if __name__ == "__main__":
    main()
