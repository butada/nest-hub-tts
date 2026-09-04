from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

from .speech import SpeechSpec


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class UsageLog:
    """Write privacy-conscious usage events as rotating JSON Lines."""

    def __init__(
        self,
        path: Path,
        max_bytes: int,
        backup_count: int,
        *,
        enabled: bool = True,
    ) -> None:
        self.path = path
        self.enabled = enabled
        self._handler: RotatingFileHandler | None = None
        self._logger: logging.Logger | None = None
        if not enabled:
            return

        path.parent.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(
            path,
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding="utf-8",
        )
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger = logging.getLogger(f"nest_hub_tts.usage.{id(self)}")
        logger.setLevel(logging.INFO)
        logger.propagate = False
        logger.addHandler(handler)
        self._handler = handler
        self._logger = logger

    def record_cache_decision(
        self,
        *,
        request_id: str,
        device_id: str,
        execution: str,
        client: str,
        cache_enabled: bool,
        cache_hit: bool,
        cache_key_id: str,
        model: str,
        spec: SpeechSpec,
    ) -> None:
        normalized_text = spec.normalized_text
        body_text = spec.body_text
        self.record(
            "cache_decision",
            request_id=request_id,
            device_id=device_id,
            execution=execution,
            client=client,
            cache_enabled=cache_enabled,
            cache_hit=cache_hit,
            cache_key_id=cache_key_id,
            model=model,
            voice=spec.voice,
            style_sha256=_sha256(spec.style),
            style_length=len(spec.style),
            audio_profile=spec.audio_profile,
            audio_speed=spec.audio_speed,
            replay_requested=spec.replay,
            replay_detected=spec.is_replay,
            input_has_replay_prefix=spec.input_has_replay_prefix,
            text_sha256=_sha256(normalized_text),
            body_text_sha256=_sha256(body_text),
            spoken_text_sha256=_sha256(spec.spoken_text),
            text_length=len(normalized_text),
            body_text_length=len(body_text),
            spoken_text_length=len(spec.spoken_text),
        )

    def record(self, event: str, **fields: Any) -> None:
        if self._logger is None:
            return
        payload = {
            "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "event": event,
            **fields,
        }
        self._logger.info(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))

    def close(self) -> None:
        if self._handler is None or self._logger is None:
            return
        self._logger.removeHandler(self._handler)
        self._handler.close()
        self._handler = None
        self._logger = None
