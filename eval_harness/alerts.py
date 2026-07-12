import httpx
from typing import Optional
from eval_harness.config import settings
from eval_harness import logging

def fire_webhook_alert(rolling_regression_rate: float, threshold: float) -> bool:
    """
    Sends a POST request to ALERT_WEBHOOK_URL if rolling_regression_rate exceeds threshold.
    Returns True if an alert was successfully triggered, False otherwise.
    """
    if not settings.ALERT_WEBHOOK_URL:
        logging.log_warn("Webhook alert trigger skipped: ALERT_WEBHOOK_URL is not configured.")
        return False

    if rolling_regression_rate <= threshold:
        return False

    payload = {
        "event": "REGRESSION_ALERT",
        "message": f"Rolling regression rate ({rolling_regression_rate:.2%}) exceeded threshold of {threshold:.2%}!",
        "rolling_regression_rate": rolling_regression_rate,
        "threshold": threshold
    }

    try:
        logging.log_info(
            f"Firing regression alert to {settings.ALERT_WEBHOOK_URL}",
            rolling_regression_rate=rolling_regression_rate,
            threshold=threshold
        )
        response = httpx.post(settings.ALERT_WEBHOOK_URL, json=payload, timeout=5.0)
        if response.status_code in (200, 201, 202, 204):
            logging.log_info(f"Alert webhook successfully received: HTTP {response.status_code}")
            return True
        else:
            logging.log_error(f"Alert webhook returned non-2xx response: HTTP {response.status_code} {response.text}")
            return False
    except Exception as e:
        logging.log_error(f"Failed to transmit alert webhook: {e}", exc_info=e)
        return False

def fire_monitor_broken_alert(canary_id: str, category: str, metric_name: str, expected_score: float, actual_score: float) -> bool:
    """
    Sends a POST request to MONITOR_ALERT_WEBHOOK_URL notifying that a canary verification failed.
    """
    if not settings.MONITOR_ALERT_WEBHOOK_URL:
        logging.log_warn("Monitor alert trigger skipped: MONITOR_ALERT_WEBHOOK_URL is not configured.")
        return False

    payload = {
        "event": "MONITOR_BROKEN_ALERT",
        "message": f"CRITICAL: Observability Monitor is Broken! Canary check '{canary_id}' failed expected outcome.",
        "canary_id": canary_id,
        "category": category,
        "metric_name": metric_name,
        "expected_score": expected_score,
        "actual_score": actual_score
    }

    try:
        logging.log_info(
            f"Firing monitor broken alert to {settings.MONITOR_ALERT_WEBHOOK_URL}",
            canary_id=canary_id,
            metric_name=metric_name
        )
        response = httpx.post(settings.MONITOR_ALERT_WEBHOOK_URL, json=payload, timeout=5.0)
        if response.status_code in (200, 201, 202, 204):
            logging.log_info(f"Monitor webhook successfully received: HTTP {response.status_code}")
            return True
        else:
            logging.log_error(f"Monitor webhook returned non-2xx response: HTTP {response.status_code} {response.text}")
            return False
    except Exception as e:
        logging.log_error(f"Failed to transmit monitor alert webhook: {e}", exc_info=e)
        return False
