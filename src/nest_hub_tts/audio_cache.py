from __future__ import annotations

import hashlib
import hmac
import json
import time
import uuid
from dataclasses import dataclass
from pathlib import Path


class AudioCacheNotFoundError(FileNotFoundError):
    pass


class AudioCacheSignatureError(PermissionError):
    pass


@dataclass(frozen=True)
class CachedAudio:
    audio_id: str
    path: Path


class AudioCache:
    def __init__(
        self,
        root: Path,
        public_base_url: str,
        ttl_seconds: int,
        url_ttl_seconds: int,
        signing_secret: str,
    ) -> None:
        self.root = root
        self.public_base_url = public_base_url.rstrip("/")
        self.ttl_seconds = ttl_seconds
        self.url_ttl_seconds = url_ttl_seconds
        self.signing_secret = signing_secret.encode("utf-8")
        self.root.mkdir(parents=True, exist_ok=True)

    def key_id(self, cache_key: dict[str, object]) -> str:
        serialized = json.dumps(
            cache_key,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()

    def get(self, cache_key: dict[str, object]) -> CachedAudio | None:
        return self.get_by_id(self.key_id(cache_key), missing_ok=True)

    def save(self, cache_key: dict[str, object], data: bytes) -> CachedAudio:
        if not data:
            raise ValueError("cannot cache empty audio")
        audio_id = self.key_id(cache_key)
        path = self._path(audio_id)
        if not path.is_file():
            temporary_path = self.root / f".{uuid.uuid4().hex}.tmp"
            temporary_path.write_bytes(data)
            temporary_path.replace(path)
        path.touch()
        return CachedAudio(audio_id=audio_id, path=path)

    def get_by_id(self, audio_id: str, *, missing_ok: bool = False) -> CachedAudio | None:
        if not self._valid_audio_id(audio_id):
            if missing_ok:
                return None
            raise AudioCacheNotFoundError(audio_id)
        path = self._path(audio_id)
        if not path.is_file():
            if missing_ok:
                return None
            raise AudioCacheNotFoundError(audio_id)
        if path.stat().st_mtime < time.time() - self.ttl_seconds:
            try:
                path.unlink()
            except FileNotFoundError:
                pass
            if missing_ok:
                return None
            raise AudioCacheNotFoundError(audio_id)
        path.touch()
        return CachedAudio(audio_id=audio_id, path=path)

    def signed_url(self, audio: CachedAudio | str) -> str:
        audio_id = audio.audio_id if isinstance(audio, CachedAudio) else audio
        expires = int(time.time()) + self.url_ttl_seconds
        signature = self._signature(audio_id, expires)
        return f"{self.public_base_url}/audio/{audio_id}.mp3?expires={expires}&sig={signature}"

    def resolve_signed(self, audio_id: str, expires: int, signature: str) -> Path:
        if not self._valid_audio_id(audio_id):
            raise AudioCacheNotFoundError(audio_id)
        if expires < int(time.time()):
            raise AudioCacheSignatureError("audio URL expired")
        if not hmac.compare_digest(signature, self._signature(audio_id, expires)):
            raise AudioCacheSignatureError("invalid audio signature")
        audio = self.get_by_id(audio_id)
        if audio is None:
            raise AudioCacheNotFoundError(audio_id)
        return audio.path

    def cleanup(self) -> int:
        cutoff = time.time() - self.ttl_seconds
        removed = 0
        for path in self.root.glob("*.mp3"):
            try:
                if path.stat().st_mtime < cutoff:
                    path.unlink()
                    removed += 1
            except FileNotFoundError:
                continue
        return removed

    def _path(self, audio_id: str) -> Path:
        return self.root / f"{audio_id}.mp3"

    def _signature(self, audio_id: str, expires: int) -> str:
        payload = f"audio.{audio_id}.{expires}".encode("ascii")
        return hmac.new(self.signing_secret, payload, hashlib.sha256).hexdigest()

    @staticmethod
    def _valid_audio_id(audio_id: str) -> bool:
        return len(audio_id) == 64 and all(c in "0123456789abcdef" for c in audio_id)
