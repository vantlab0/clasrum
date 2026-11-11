from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent
REPO_DIR = BACKEND_DIR.parent


class Settings(BaseSettings):
    """Runtime configuration, loaded from environment / backend/.env."""

    model_config = SettingsConfigDict(
        env_file=(str(REPO_DIR / ".env"), str(BACKEND_DIR / ".env")),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- core -------------------------------------------------------------
    app_name: str = "AI Personal Classroom"
    version: str = "0.1.0"
    environment: str = "development"  # development | test | production
    debug: bool = True
    api_prefix: str = "/api/v1"

    database_url: str = "sqlite+aiosqlite:///./dev.db"
    db_echo: bool = False

    jwt_secret: str = "dev-only-change-me-please-0123456789abcdef"
    jwt_algorithm: str = "HS256"
    jwt_expires_minutes: int = 60 * 24 * 14

    cors_origins: list[str] = ["*"]

    # --- Qwen Realtime ----------------------------------------------------
    qwen_api_key: str = ""
    qwen_realtime_url: str = "wss://dashscope.aliyuncs.com/api-ws/v1/realtime"
    qwen_model: str = "qwen-omni-turbo-realtime"
    qwen_voice: str = "Cherry"
    qwen_input_sample_rate: int = 16000
    qwen_output_sample_rate: int = 24000
    qwen_turn_detection: str = "server_vad"
    # When no API key is configured (or fake mode is on) the backend serves a
    # deterministic offline tutor. Useful for local dev, CI and demos.
    qwen_fake_mode: bool = False
    qwen_connect_timeout_seconds: float = 10.0

    # --- Structured notes generation -------------------------------------
    notes_model: str = "qwen-plus"
    notes_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    notes_api_key: str = ""  # falls back to qwen_api_key
    notes_timeout_seconds: float = 120.0
    notes_max_transcript_chars: int = 60000

    # --- board / lesson behaviour -----------------------------------------
    board_snapshot_interval_events: int = 40
    board_snapshot_keep: int = 5
    transcript_batch_size: int = 20

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")

    @property
    def effective_notes_api_key(self) -> str:
        return self.notes_api_key or self.qwen_api_key

    @property
    def qwen_configured(self) -> bool:
        return bool(self.qwen_api_key) and not self.qwen_fake_mode


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
