from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class DeviceSettings(BaseModel):
    name: str
    host: str
    uuid: str | None = None
    model: str | None = "Google Nest Hub"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    host: str = "0.0.0.0"
    port: int = 8080

    gemini_api_key: str | None = None
    gemini_model: str = "gemini-3.1-flash-tts-preview"
    gemini_tts_voice: str = "Kore"
    gemini_tts_style: str = "自然で聞き取りやすく、与えられたテキストを内容を変えずに読み上げる"
    gemini_timeout_seconds: float = 60.0
    gemini_batch_poll_interval_seconds: float = Field(default=10.0, ge=1.0)
    audio_profile: str = "clear_speech"
    audio_speed: float = Field(default=1.08, ge=0.5, le=2.0)
    max_text_length: int = Field(default=4000, ge=1)

    api_token: str | None = None

    media_public_base_url: str = "http://127.0.0.1:8080"
    media_dir: Path = Path("./data/media")
    media_ttl_seconds: int = Field(default=3600, ge=60)
    media_signing_secret: str = "dev-only-media-secret"

    batch_db_path: Path = Path("./data/jobs.sqlite3")
    audio_cache_dir: Path = Path("./data/audio-cache")
    audio_cache_ttl_seconds: int = Field(default=2592000, ge=60)
    audio_url_ttl_seconds: int = Field(default=86400, ge=60)

    cast_devices_json: str = "{}"
    cast_timeout_seconds: float = Field(default=10.0, ge=1.0)

    @property
    def cast_devices(self) -> dict[str, DeviceSettings]:
        try:
            raw = json.loads(self.cast_devices_json or "{}")
        except json.JSONDecodeError as exc:
            raise ValueError("CAST_DEVICES_JSON must be valid JSON") from exc

        if not isinstance(raw, dict):
            raise ValueError("CAST_DEVICES_JSON must be a JSON object")

        return {
            device_id: DeviceSettings.model_validate(device)
            for device_id, device in raw.items()
        }


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
