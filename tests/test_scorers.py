import pytest
from eval_harness import scorers

def test_json_validator_scorer():
    scorer = scorers.JsonValidatorScorer()
    assert scorer.name == "json_format"
    assert scorer.metric_type == "RULE"
    
    # Valid JSON
    res_valid = scorer.score("input", '{"key": "value", "list": [1, 2]}')
    assert res_valid.score == 1.0
    assert "successfully parsed" in res_valid.explanation
    assert res_valid.status == "SUCCESS"
    
    # Invalid JSON
    res_invalid = scorer.score("input", '{"key": "value", malformed_json}')
    assert res_invalid.score == 0.0
    assert "Malformed JSON" in res_invalid.explanation
    assert res_invalid.status == "SUCCESS"
    
    # Empty output
    res_empty = scorer.score("input", "")
    assert res_empty.score == 0.0
    assert res_empty.status == "SUCCESS"

def test_regex_match_scorer():
    # Valid patterns
    scorer = scorers.RegexMatchScorer(pattern=r"^hello \w+$", case_sensitive=False)
    assert scorer.name == "regex_match"
    
    res1 = scorer.score("input", "Hello World")
    assert res1.score == 1.0
    assert res1.status == "SUCCESS"
    
    res2 = scorer.score("input", "bye world")
    assert res2.score == 0.0
    
    # Invalid pattern
    with pytest.raises(ValueError) as exc:
        scorers.RegexMatchScorer(pattern="[invalid regex")
    assert "Invalid regular expression" in str(exc.value)

def test_length_scorer():
    # Valid range
    scorer = scorers.LengthScorer(min_len=5, max_len=15)
    assert scorer.name == "length_check"
    
    # Normal inside bounds
    res_ok = scorer.score("input", "hello world") # len = 11
    assert res_ok.score == 1.0
    assert res_ok.status == "SUCCESS"
    
    # Too short
    res_short = scorer.score("input", "hey") # len = 3
    assert res_short.score == 0.0
    assert "below minimum" in res_short.explanation
    
    # Too long
    res_long = scorer.score("input", "this string is way too long")
    assert res_long.score == 0.0
    assert "above maximum" in res_long.explanation

    # Boundary configuration validation
    with pytest.raises(ValueError):
        scorers.LengthScorer(min_len=20, max_len=10)

def test_llm_judge_mock():
    # Test LlmJudgeScorer mock behavior
    scorer = scorers.LlmJudgeScorer(rubric="Politeness", trials=3, mock=True)
    assert scorer.name == "llm_judge"
    assert scorer.metric_type == "LLM_JUDGE"

    res = scorer.score("user input", "assistant output", "expected output")
    assert res.status == "SUCCESS"
    assert res.score is not None
    assert 0.0 <= res.score <= 1.0
    assert "Mean:" in res.explanation
    assert "StdDev:" in res.explanation

def test_llm_judge_all_trials_failed(monkeypatch):
    # Simulate all LLM calls failing
    from eval_harness import client
    def mock_fail(*args, **kwargs):
        raise RuntimeError("API Timeout")
    monkeypatch.setattr(client, "call_llm", mock_fail)

    scorer = scorers.LlmJudgeScorer(rubric="Politeness", trials=3, mock=False)
    res = scorer.score("input", "output")
    
    assert res.status == "FAILED"
    assert res.score is None
    assert "API Timeout" in res.explanation

def test_llm_judge_partial_trials_failed(monkeypatch):
    # Simulate Trial 1 success, Trial 2 JSON Parse Error, Trial 3 success
    from eval_harness import client
    
    call_count = 0
    responses = [
        '{"score": 0.9, "explanation": "Very polite"}',
        'invalid json response that will fail parsing',
        '{"score": 0.7, "explanation": "Somewhat polite"}'
    ]
    
    def mock_call(*args, **kwargs):
        nonlocal call_count
        res = responses[call_count]
        call_count += 1
        return res
        
    monkeypatch.setattr(client, "call_llm", mock_call)

    scorer = scorers.LlmJudgeScorer(rubric="Politeness", trials=3, mock=False)
    res = scorer.score("input", "output")

    assert res.status == "SUCCESS"
    # Average of 0.9 and 0.7 is 0.8
    assert res.score == 0.8
    assert "JSON Parse Error" in res.explanation
    assert "Mean=0.80" in res.explanation


def test_llm_judge_non_json_fallback(monkeypatch):
    # Simulate a raw text response that isn't valid JSON but contains score and explanation
    from eval_harness import client
    
    call_count = 0
    responses = [
        "Score: 0.85\nExplanation: This is a fallback test",
        '{"score": 0.95, "explanation": "This is valid JSON"}',
        "Here is the evaluation: grade is 0.75 and the explanation is ok"
    ]
    
    def mock_call(*args, **kwargs):
        nonlocal call_count
        res = responses[call_count]
        call_count += 1
        return res
        
    monkeypatch.setattr(client, "call_llm", mock_call)

    scorer = scorers.LlmJudgeScorer(rubric="Politeness", trials=3, mock=False)
    res = scorer.score("input", "output")

    assert res.status == "SUCCESS"
    # Average of 0.85, 0.95, and 0.75 is 0.85
    assert res.score == 0.85
    assert "Trial 1 (Fallback)" in res.explanation
    assert "Trial 3 (Fallback)" in res.explanation
    assert "Mean=0.85" in res.explanation


