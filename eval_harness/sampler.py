import re
import uuid
import random
from typing import Optional
from eval_harness.config import settings
from eval_harness import database, logging

def mask_pii(text: str) -> str:
    """
    Scans text for common PII (Emails, Phones, Credit Cards, SSNs)
    and replaces them with generic placeholders.
    """
    if not text:
        return text

    # Email addresses
    email_pattern = r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b'
    # Phone numbers (various formats)
    phone_pattern = r'\+?\b(?:\d{1,3}[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b'
    # Credit Cards (16 digits, with optional spaces or dashes)
    cc_pattern = r'\b(?:\d{4}[-\s]?){3}\d{4}\b'
    # Social Security Numbers (xxx-xx-xxxx)
    ssn_pattern = r'\b\d{3}-\d{2}-\d{4}\b'

    masked = text
    masked = re.sub(email_pattern, "[EMAIL]", masked)
    masked = re.sub(phone_pattern, "[PHONE]", masked)
    masked = re.sub(cc_pattern, "[CREDIT_CARD]", masked)
    masked = re.sub(ssn_pattern, "[SSN]", masked)
    return masked

def should_sample() -> bool:
    """
    Determines whether a request should be sampled based on config rates and kill switch.
    """
    if settings.SAMPLER_KILL_SWITCH:
        return False
    
    rate = min(max(0.0, settings.SAMPLING_RATE), settings.MAX_SAMPLING_RATE)
    return random.random() < rate

def inject_canary_record(config_hash: str, canary_id: Optional[str] = None) -> Optional[str]:
    """
    Selects a canary case and inserts it into the production_traffic queue.
    """
    from eval_harness.canary_lib import CANARIES
    if canary_id:
        canary = next((c for c in CANARIES if c["canary_id"] == canary_id), None)
    else:
        canary = random.choice(CANARIES)

    if not canary:
        return None

    request_id = f"canary_{uuid.uuid4().hex[:12]}"
    
    try:
        with database.get_db_conn() as conn:
            conn.execute(
                """
                INSERT INTO production_traffic (request_id, config_hash, input_data, actual_output, latency_ms, cost, is_canary, canary_id)
                VALUES (?, ?, ?, ?, ?, ?, 1, ?)
                """,
                (request_id, config_hash, canary["input_data"], canary["actual_output"], 120.0, 0.000075, canary["canary_id"])
            )
        logging.log_info(
            f"Successfully injected canary request {request_id} ({canary['canary_id']})",
            run_id=None,
            case_id=None,
            request_id=request_id,
            config_hash=config_hash
        )
        return request_id
    except Exception as e:
        logging.log_error(
            f"Failed to inject canary request: {e}",
            run_id=None,
            case_id=None,
            exc_info=e
        )
        return None

def log_production_traffic(
    config_hash: str,
    input_data: str,
    actual_output: str,
    latency_ms: float,
    cost: float
) -> Optional[str]:
    """
    Samples a production request, masks PII, and writes it to the database queue if chosen.
    Returns the generated request_id if sampled, or None.
    """
    if not should_sample():
        return None

    request_id = f"req_{uuid.uuid4().hex[:12]}"
    masked_input = mask_pii(input_data)
    masked_output = mask_pii(actual_output)

    try:
        with database.get_db_conn() as conn:
            conn.execute(
                """
                INSERT INTO production_traffic (request_id, config_hash, input_data, actual_output, latency_ms, cost)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (request_id, config_hash, masked_input, masked_output, latency_ms, cost)
            )
        logging.log_info(
            f"Successfully sampled production request {request_id}",
            run_id=None,
            case_id=None,
            request_id=request_id,
            config_hash=config_hash
        )
        
        # Inject a canary request with 20% probability
        if random.random() < 0.2:
            inject_canary_record(config_hash)

        return request_id
    except Exception as e:
        logging.log_error(
            f"Failed to log sampled production traffic: {e}",
            run_id=None,
            case_id=None,
            exc_info=e
        )
        return None
