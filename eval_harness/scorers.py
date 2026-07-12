import re
import json
from abc import ABC, abstractmethod
from typing import Any, Dict, Optional, Tuple

class MetricResult:
    """
    Standardized evaluation result for a single metric.
    """
    def __init__(
        self,
        score: Optional[float],
        explanation: str,
        status: str = "SUCCESS"
    ):
        self.score = score
        self.explanation = explanation
        self.status = status # 'SUCCESS' or 'FAILED'

    def to_dict(self) -> Dict[str, Any]:
        return {
            "score": self.score,
            "explanation": self.explanation,
            "status": self.status
        }


class BaseScorer(ABC):
    """
    Abstract Base Class for all scorers (Rule-based & LLM-based).
    """
    @property
    @abstractmethod
    def name(self) -> str:
        """The name of the metric (e.g. 'json_format', 'regex_match')."""
        pass

    @property
    @abstractmethod
    def metric_type(self) -> str:
        """The type of metric ('RULE' or 'LLM_JUDGE')."""
        pass

    @abstractmethod
    def score(
        self,
        input_val: str,
        output_val: str,
        expected_val: Optional[str] = None
    ) -> MetricResult:
        """
        Grades the output.
        """
        pass


class JsonValidatorScorer(BaseScorer):
    """
    Evaluates if output is valid JSON.
    """
    @property
    def name(self) -> str:
        return "json_format"

    @property
    def metric_type(self) -> str:
        return "RULE"

    def score(
        self,
        input_val: str,
        output_val: str,
        expected_val: Optional[str] = None
    ) -> MetricResult:
        if not output_val:
            return MetricResult(
                score=0.0,
                explanation="Output is empty.",
                status="SUCCESS"
            )
        try:
            json.loads(output_val)
            return MetricResult(
                score=1.0,
                explanation="Output successfully parsed as valid JSON.",
                status="SUCCESS"
            )
        except json.JSONDecodeError as e:
            return MetricResult(
                score=0.0,
                explanation=f"Malformed JSON. Parse error: {str(e)}",
                status="SUCCESS"
            )


class RegexMatchScorer(BaseScorer):
    """
    Evaluates if output matches a specified regex pattern.
    """
    def __init__(self, pattern: str, case_sensitive: bool = True):
        self.pattern = pattern
        self.flags = 0 if case_sensitive else re.IGNORECASE
        try:
            self.compiled = re.compile(pattern, self.flags)
        except re.error as e:
            # Scorer creation itself failed due to invalid pattern
            raise ValueError(f"Invalid regular expression pattern '{pattern}': {e}")

    @property
    def name(self) -> str:
        return "regex_match"

    @property
    def metric_type(self) -> str:
        return "RULE"

    def score(
        self,
        input_val: str,
        output_val: str,
        expected_val: Optional[str] = None
    ) -> MetricResult:
        if not output_val:
            return MetricResult(
                score=0.0,
                explanation="Output is empty.",
                status="SUCCESS"
            )
        
        match = self.compiled.search(output_val)
        if match:
            return MetricResult(
                score=1.0,
                explanation=f"Regex match found for pattern: '{self.pattern}'",
                status="SUCCESS"
            )
        else:
            return MetricResult(
                score=0.0,
                explanation=f"No match found for pattern: '{self.pattern}'",
                status="SUCCESS"
            )


class LengthScorer(BaseScorer):
    """
    Evaluates if the output character length falls within specified min/max limits.
    """
    def __init__(self, min_len: Optional[int] = None, max_len: Optional[int] = None):
        self.min_len = min_len
        self.max_len = max_len
        if min_len is not None and max_len is not None and min_len > max_len:
            raise ValueError(f"min_len ({min_len}) cannot be greater than max_len ({max_len})")

    @property
    def name(self) -> str:
        return "length_check"

    @property
    def metric_type(self) -> str:
        return "RULE"

    def score(
        self,
        input_val: str,
        output_val: str,
        expected_val: Optional[str] = None
    ) -> MetricResult:
        val_len = len(output_val)
        
        if self.min_len is not None and val_len < self.min_len:
            return MetricResult(
                score=0.0,
                explanation=f"Output length ({val_len}) is below minimum length limit ({self.min_len}).",
                status="SUCCESS"
            )
            
        if self.max_len is not None and val_len > self.max_len:
            return MetricResult(
                score=0.0,
                explanation=f"Output length ({val_len}) is above maximum length limit ({self.max_len}).",
                status="SUCCESS"
            )

        return MetricResult(
            score=1.0,
            explanation=f"Output length ({val_len}) is within allowed limits (min={self.min_len}, max={self.max_len}).",
            status="SUCCESS"
        )


class LlmJudgeScorer(BaseScorer):
    """
    Evaluates output using an LLM-as-Judge.
    To detect and report variance (inconsistency), it runs multiple trials
    and computes the mean and standard deviation of the scores.
    """
    def __init__(self, rubric: Optional[str] = None, trials: int = 3, mock: bool = True):
        self.rubric = rubric
        self.trials = max(1, trials)
        self.mock = mock

    def _parse_non_json_fallback(self, text: str) -> Tuple[float, str]:
        """
        Attempts to extract score and explanation from a response that failed standard JSON decoding.
        """
        # 1. Try to find a JSON-like substring block using curly braces
        json_block_match = re.search(r"(\{.*\})", text, re.DOTALL)
        if json_block_match:
            try:
                data = json.loads(json_block_match.group(1).strip())
                score_val = float(data["score"])
                explanation_val = data.get("explanation", "")
                if 0.0 <= score_val <= 1.0:
                    return score_val, explanation_val
            except Exception:
                pass

        # 2. Try regex extraction for score
        score_match = re.search(r'"score"\s*:\s*([0-9.]+)', text, re.IGNORECASE)
        if not score_match:
            score_match = re.search(r'\b(?:score|grade|rating|val|value)\b\s*(?:is\s+)?[:=-]?\s*([0-9.]+)', text, re.IGNORECASE)

        score_val = None
        if score_match:
            try:
                val = float(score_match.group(1))
                if 0.0 <= val <= 1.0:
                    score_val = val
            except ValueError:
                pass

        if score_val is None:
            raise ValueError("Could not extract a valid score between 0.0 and 1.0 from response.")

        # 3. Try regex extraction for explanation
        explanation_match = re.search(r'"explanation"\s*:\s*"(.*?)"', text, re.DOTALL | re.IGNORECASE)
        if not explanation_match:
            explanation_match = re.search(r'\bexplanation\b\s*[:=-]?\s*(.*)', text, re.DOTALL | re.IGNORECASE)
        
        explanation_val = ""
        if explanation_match:
            explanation_val = explanation_match.group(1).strip().strip('"').strip("'")
        else:
            # Fallback to cleaning the whole text as explanation
            explanation_val = text.strip()

        return score_val, explanation_val

    @property
    def name(self) -> str:
        return "llm_judge"

    @property
    def metric_type(self) -> str:
        return "LLM_JUDGE"

    def score(
        self,
        input_val: str,
        output_val: str,
        expected_val: Optional[str] = None
    ) -> MetricResult:
        if not output_val:
            return MetricResult(
                score=0.0,
                explanation="Output is empty.",
                status="SUCCESS"
            )

        # Mock mode check
        if self.mock:
            import hashlib
            # Return simulated scores with some slight variance
            import random
            import statistics
            # Hash to get stable seed based on input/output
            seed_val = int(hashlib.md5(f"{input_val}||{output_val}".encode("utf-8")).hexdigest()[:8], 16)
            random.seed(seed_val)
            scores = [round(random.uniform(0.7, 0.9), 2) for _ in range(self.trials)]
            mean_score = round(sum(scores) / len(scores), 2)
            std_dev = round(statistics.pstdev(scores), 3) if len(scores) > 1 else 0.0
            return MetricResult(
                score=mean_score,
                explanation=f"[MOCK JUDGE] Clean run across {self.trials} trials. Scores: {scores}. Mean: {mean_score}, StdDev: {std_dev}",
                status="SUCCESS"
            )

        from eval_harness import client
        
        prompt_template = (
            "You are an impartial expert judge evaluating the response of an AI assistant.\n"
            "Given the following context:\n"
            "- User input: {input_val}\n"
            "- Assistant output: {output_val}\n"
            "- Expected output: {expected_val}\n"
            "- Rubric: {rubric}\n\n"
            "Evaluate the assistant output and provide a score between 0.0 (worst) and 1.0 (best) and a short explanation.\n"
            "You MUST output your response in raw JSON format with these exact keys:\n"
            "{{\n"
            "  \"score\": <float between 0.0 and 1.0>,\n"
            "  \"explanation\": \"<string explaining the grade>\"\n"
            "}}\n"
            "Do not output any markdown formatting (like ```json) or other conversational text."
        )
        
        prompt = prompt_template.format(
            input_val=input_val,
            output_val=output_val,
            expected_val=expected_val or "N/A",
            rubric=self.rubric or "Grade based on general correctness and usefulness."
        )

        scores = []
        explanations = []
        errors = []

        for trial in range(self.trials):
            try:
                # Call LLM with JSON constraint and timeout
                response_str = client.call_llm(prompt, response_json=True, timeout_sec=12.0)
                
                # Parse response
                try:
                    data = json.loads(response_str)
                    score_val = float(data["score"])
                    explanation_val = data.get("explanation", "")
                    
                    if not (0.0 <= score_val <= 1.0):
                        raise ValueError(f"Score out of bounds: {score_val}")
                        
                    scores.append(score_val)
                    explanations.append(f"Trial {trial+1}: {explanation_val} (Score: {score_val})")
                except (json.JSONDecodeError, KeyError, ValueError) as json_err:
                    # Try fallback parser for raw/malformed text
                    try:
                        score_val, explanation_val = self._parse_non_json_fallback(response_str)
                        scores.append(score_val)
                        explanations.append(f"Trial {trial+1} (Fallback): {explanation_val} (Score: {score_val})")
                    except Exception as fallback_err:
                        errors.append(
                            f"Trial {trial+1} JSON Parse Error: {str(json_err)} and fallback failed: {str(fallback_err)}. "
                            f"Response was: {response_str}"
                        )
                    
            except Exception as call_err:
                errors.append(f"Trial {trial+1} Call Error: {type(call_err).__name__} {str(call_err)}")

        # Evaluate trial results
        if not scores:
            # Failure-first: All trials failed to return grade or parse successfully
            err_details = "; ".join(errors)
            return MetricResult(
                score=None,
                explanation=f"Judge failed to evaluate. Error details: {err_details}",
                status="FAILED"
            )

        import statistics
        mean_score = sum(scores) / len(scores)
        std_dev = statistics.pstdev(scores) if len(scores) > 1 else 0.0
        
        combined_explanation = "; ".join(explanations)
        if errors:
            combined_explanation += f" | Non-fatal errors in some trials: {'; '.join(errors)}"
        
        combined_explanation += f" | Stats: Mean={mean_score:.2f}, StdDev={std_dev:.3f} across {len(scores)}/{self.trials} successful trials."

        return MetricResult(
            score=mean_score,
            explanation=combined_explanation,
            status="SUCCESS"
        )

