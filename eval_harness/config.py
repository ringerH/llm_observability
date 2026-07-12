import os
import sys
from typing import Optional
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

    GEMINI_API_KEY: Optional[str] = Field(default=None)
    OPENAI_API_KEY: Optional[str] = Field(default=None)
    DATABASE_PATH: str = Field(default="eval.db")
    SAMPLING_RATE: float = Field(default=0.1)
    MAX_SAMPLING_RATE: float = Field(default=0.5)
    SAMPLER_KILL_SWITCH: bool = Field(default=False)
    REGRESSION_THRESHOLD: float = Field(default=0.05)
    ALERT_WEBHOOK_URL: Optional[str] = Field(default=None)
    MONITOR_ALERT_WEBHOOK_URL: Optional[str] = Field(default=None)

    @field_validator("GEMINI_API_KEY", "OPENAI_API_KEY")
    @classmethod
    def validate_api_keys(cls, v: Optional[str], info) -> Optional[str]:
        if not v:
            return None
        
        val_upper = v.upper()
        placeholders = ["CHANGE_ME", "YOUR_", "PLACEHOLDER", "TEMPLATE"]
        
        # Check for placeholder substrings
        if any(p in val_upper for p in placeholders):
            raise ValueError(
                f"Environment variable '{info.field_name}' contains a placeholder value: '{v}'"
            )
            
        # Check for suspiciously short keys
        if len(v) < 10:
            raise ValueError(
                f"Environment variable '{info.field_name}' value is suspiciously short: '{v}'"
            )
            
        return v

    @field_validator("SAMPLING_RATE", "MAX_SAMPLING_RATE")
    @classmethod
    def validate_sampling_rates(cls, v: float, info) -> float:
        if not (0.0 <= v <= 1.0):
            raise ValueError(
                f"Environment variable '{info.field_name}' must be between 0.0 and 1.0, got: {v}"
            )
        return v

    @field_validator("REGRESSION_THRESHOLD")
    @classmethod
    def validate_regression_threshold(cls, v: float) -> float:
        if v < 0.0:
            raise ValueError(
                f"Environment variable 'REGRESSION_THRESHOLD' must be non-negative, got: {v}"
            )
        return v


# Global settings instance
settings = None

def init_settings() -> Settings:
    global settings
    try:
        settings = Settings()
        return settings
    except Exception as e:
        print(f"CRITICAL CONFIGURATION ERROR: Environment validation failed.", file=sys.stderr)
        print(str(e), file=sys.stderr)
        sys.exit(1)

# Initialize on module import
init_settings()
