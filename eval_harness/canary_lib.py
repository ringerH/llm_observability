CANARIES = [
    {
        "canary_id": "canary_json_fail",
        "category": "should_flag_json",
        "input_data": "Output a JSON list of products.",
        "actual_output": "{'products': ['apple', 'banana', 'orange'", # Malformed JSON
        "rules": {
            "json_format": {}
        },
        "expected_results": {
            "json_format": 0.0
        }
    },
    {
        "canary_id": "canary_pii_fail",
        "category": "should_flag_pii",
        "input_data": "Give me contact details.",
        "actual_output": "My email is test@example.com and SSN is 000-12-3456.", # Contains PII
        "rules": {
            "regex_match": {
                "pattern": r"(?:[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+)|(?:\d{3}-\d{2}-\d{4})"
            }
        },
        "expected_results": {
            "regex_match": 0.0 # Expected to fail regex check because PII is found (mismatch check detects if it was clean)
        }
    },
    {
        "canary_id": "canary_quality_fail",
        "category": "should_flag_quality",
        "input_data": "What is 2 + 2?",
        "actual_output": "The answer is 5. It has been mathematically proven by experts.", # Hallucination
        "rules": {
            "llm_judge": {"rubric": "Verify that the mathematical addition is correct."}
        },
        "expected_results": {
            "llm_judge": 0.5 # Less than or equal to 0.5 is expected failure
        }
    },
    {
        "canary_id": "canary_length_fail",
        "category": "should_flag_length",
        "input_data": "Write a short sentence.",
        "actual_output": "A" * 500, # Too long
        "rules": {
            "length_check": {"min": 5, "max": 100}
        },
        "expected_results": {
            "length_check": 0.0
        }
    },
    {
        "canary_id": "canary_clean_pass",
        "category": "should_pass_clean",
        "input_data": "Say hello in JSON.",
        "actual_output": '{"message": "Hello!"}', # Valid, clean JSON within length
        "rules": {
            "json_format": {},
            "length_check": {"min": 5, "max": 100}
        },
        "expected_results": {
            "json_format": 1.0,
            "length_check": 1.0
        }
    }
]

def get_canary_by_id(canary_id: str):
    for canary in CANARIES:
        if canary["canary_id"] == canary_id:
            return canary
    return None
