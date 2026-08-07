"""Environment-driven configuration for property-hunter.

Values come from environment variables or a local ``.env`` file (loaded via
python-dotenv). No secret defaults: credentials must be supplied at runtime.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent.parent


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _env_int(name: str, default: int) -> int:
    raw = _env(name)
    if not raw:
        return default
    return int(raw)


def _env_float(name: str, default: float) -> float:
    raw = _env(name)
    if not raw:
        return default
    return float(raw)


def _env_bool(name: str, default: bool) -> bool:
    raw = _env(name).lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


@dataclass
class ScopeConfig:
    """Search scope: operation-type-region slug as published by inmoup.com.ar."""

    operation: str = "venta"
    type: str = "departamentos"
    region: str = "capital-federal"

    @property
    def slug(self) -> str:
        return f"{self.type}-en-{self.operation}-en-{self.region}"


@dataclass
class CollectConfig:
    delay_seconds: float = 2.0
    timeout_seconds: float = 30.0
    max_retries: int = 3
    max_pages_per_search: int = 400
    user_agent: str = "property-hunter/0.1 (personal investment research)"


@dataclass
class BaselineConfig:
    min_observations_per_zone: int = 5
    window_days: int = 90


@dataclass
class RuleConfig:
    undervaluation_threshold: float = 0.10
    yield_threshold: float = 0.06
    price_drop_threshold: float = 0.05
    price_drop_lookback_days: int = 30
    undervaluation_enabled: bool = True
    yield_enabled: bool = True
    price_drop_enabled: bool = True


@dataclass
class MLConfig:
    min_train_samples: int = 200
    test_split: float = 0.2
    random_state: int = 42


@dataclass
class SMTPConfig:
    host: str = ""
    port: int = 587
    tls: bool = True
    user: str = ""
    password: str = ""
    sender: str = ""
    recipient: str = ""


@dataclass
class NotifyConfig:
    max_attempts: int = 3
    retry_backoff_base_seconds: float = 1.0


@dataclass
class LLMConfig:
    base_url: str = ""
    model: str = ""
    api_key: str = ""
    timeout_seconds: float = 30.0

    @property
    def enabled(self) -> bool:
        return bool(self.base_url and self.model and self.api_key)


@dataclass
class ScheduleConfig:
    daily_hour: int = 9
    daily_minute: int = 0


@dataclass
class Settings:
    db_path: Path
    scope: ScopeConfig
    collect: CollectConfig
    baselines: BaselineConfig
    rules: RuleConfig
    ml: MLConfig
    smtp: SMTPConfig
    notify: NotifyConfig
    llm: LLMConfig
    schedule: ScheduleConfig
    log_level: str
    _extra: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_env(cls) -> "Settings":
        db_path = Path(_env("DB_PATH", "data/property_hunter.db"))
        if not db_path.is_absolute():
            db_path = BASE_DIR / db_path
        return cls(
            db_path=db_path,
            scope=ScopeConfig(
                operation=_env("SCOPE_OPERATION", "venta"),
                type=_env("SCOPE_TYPE", "departamentos"),
                region=_env("SCOPE_REGION", "capital-federal"),
            ),
            collect=CollectConfig(
                delay_seconds=_env_float("COLLECT_DELAY_SECONDS", 2.0),
                timeout_seconds=_env_float("COLLECT_TIMEOUT_SECONDS", 30.0),
                max_retries=_env_int("COLLECT_MAX_RETRIES", 3),
                max_pages_per_search=_env_int("MAX_PAGES_PER_SEARCH", 400),
                user_agent=_env("USER_AGENT", "property-hunter/0.1 (personal investment research)"),
            ),
            baselines=BaselineConfig(
                min_observations_per_zone=_env_int("MIN_OBSERVATIONS_PER_ZONE", 5),
                window_days=_env_int("BASELINE_WINDOW_DAYS", 90),
            ),
            rules=RuleConfig(
                undervaluation_threshold=_env_float("UNDERVALUATION_THRESHOLD", 0.10),
                yield_threshold=_env_float("YIELD_THRESHOLD", 0.06),
                price_drop_threshold=_env_float("PRICE_DROP_THRESHOLD", 0.05),
                price_drop_lookback_days=_env_int("PRICE_DROP_LOOKBACK_DAYS", 30),
                undervaluation_enabled=_env_bool("UNDERVALUATION_ENABLED", True),
                yield_enabled=_env_bool("YIELD_ENABLED", True),
                price_drop_enabled=_env_bool("PRICE_DROP_ENABLED", True),
            ),
            ml=MLConfig(
                min_train_samples=_env_int("ML_MIN_TRAIN_SAMPLES", 200),
                test_split=_env_float("ML_TEST_SPLIT", 0.2),
            ),
            smtp=SMTPConfig(
                host=_env("SMTP_HOST"),
                port=_env_int("SMTP_PORT", 587),
                tls=_env_bool("SMTP_TLS", True),
                user=_env("SMTP_USER"),
                password=_env("SMTP_PASSWORD"),
                sender=_env("SMTP_FROM"),
                recipient=_env("ALERT_EMAIL"),
            ),
            notify=NotifyConfig(
                max_attempts=_env_int("NOTIFY_MAX_ATTEMPTS", 3),
                retry_backoff_base_seconds=_env_float("NOTIFY_RETRY_BACKOFF_BASE_SECONDS", 1.0),
            ),
            llm=LLMConfig(
                base_url=_env("LLM_BASE_URL"),
                model=_env("LLM_MODEL"),
                api_key=_env("LLM_API_KEY"),
                timeout_seconds=_env_float("LLM_TIMEOUT_SECONDS", 30.0),
            ),
            schedule=ScheduleConfig(
                daily_hour=_env_int("SCHEDULE_DAILY_HOUR", 9),
                daily_minute=_env_int("SCHEDULE_DAILY_MINUTE", 0),
            ),
            log_level=_env("LOG_LEVEL", "INFO"),
        )
