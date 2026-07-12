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
