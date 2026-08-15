from __future__ import annotations

import hashlib
import hmac
import time
import uuid
from dataclasses import dataclass
from pathlib import Path


class MediaNotFoundError(FileNotFoundError):
    pass


class MediaSignatureError(PermissionError):
    pass


@dataclass(frozen=True)
class MediaAsset:
    media_id: str
    path: Path


class MediaStore:
    def __init__(
        self,
        root: Path,
        public_base_url: str,
        ttl_seconds: int,
        signing_secret: str,
    ) -> None:
        self.root = root
        self.public_base_url = public_base_url.rstrip("/")
        self.ttl_seconds = ttl_seconds
        self.signing_secret = signing_secret.encode("utf-8")
        self.root.mkdir(parents=True, exist_ok=True)

    def save_mp3(self, data: bytes) -> MediaAsset:
        media_id = uuid.uuid4().hex
        path = self.root / f"{media_id}.mp3"
        path.write_bytes(data)
        return MediaAsset(media_id=media_id, path=path)

    def signed_url(self, asset: MediaAsset) -> str:
        expires = int(time.time()) + self.ttl_seconds
        signature = self._signature(asset.media_id, expires)
        return (
            f"{self.public_base_url}/media/{asset.media_id}.mp3"
            f"?expires={expires}&sig={signature}"
        )

    def resolve(self, media_id: str, expires: int, signature: str) -> Path:
        if not self._valid_media_id(media_id):
            raise MediaNotFoundError(media_id)
        if expires < int(time.time()):
            raise MediaSignatureError("media URL expired")
        if not hmac.compare_digest(signature, self._signature(media_id, expires)):
            raise MediaSignatureError("invalid media signature")

        path = self.root / f"{media_id}.mp3"
        if not path.is_file():
            raise MediaNotFoundError(media_id)
        return path

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

    def _signature(self, media_id: str, expires: int) -> str:
        payload = f"{media_id}.{expires}".encode("ascii")
        return hmac.new(self.signing_secret, payload, hashlib.sha256).hexdigest()

    @staticmethod
    def _valid_media_id(media_id: str) -> bool:
        return len(media_id) == 32 and all(c in "0123456789abcdef" for c in media_id)
