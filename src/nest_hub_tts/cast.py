from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any

import pychromecast

from .config import DeviceSettings, Settings


class CastFailure(RuntimeError):
    pass


@dataclass(frozen=True)
class CastResult:
    status: str


class CastController:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._casts: dict[str, Any] = {}

    async def play(
        self,
        device_id: str,
        device: DeviceSettings,
        media_url: str,
        title: str,
    ) -> CastResult:
        return await asyncio.to_thread(
            self._play_sync,
            device_id,
            device,
            media_url,
            title,
        )

    async def close(self) -> None:
        await asyncio.to_thread(self._close_sync)

    def _play_sync(
        self,
        device_id: str,
        device: DeviceSettings,
        media_url: str,
        title: str,
    ) -> CastResult:
        cast = self._get_cast(device_id, device)
        try:
            cast.wait(timeout=self.settings.cast_timeout_seconds)
            cast.media_controller.play_media(
                media_url,
                "audio/mp3",
                title=title[:200],
                autoplay=True,
            )
        except Exception as exc:
            self._casts.pop(device_id, None)
            raise CastFailure(f"could not send media to {device.name}: {exc}") from exc

        deadline = time.monotonic() + self.settings.cast_timeout_seconds
        saw_buffering = False
        while time.monotonic() < deadline:
            status = cast.media_controller.status
            state = getattr(status, "player_state", None)
            content_id = getattr(status, "content_id", None)
            if content_id == media_url and state == "PLAYING":
                return CastResult(status="playing")
            if content_id == media_url and state == "BUFFERING":
                saw_buffering = True
            time.sleep(0.2)

        if saw_buffering:
            return CastResult(status="buffering")
        raise CastFailure(f"Nest Hub did not start media playback: {device.name}")

    def _get_cast(self, device_id: str, device: DeviceSettings) -> Any:
        cached = self._casts.get(device_id)
        if cached is not None and getattr(cached.socket_client, "is_connected", False):
            return cached

        try:
            casts, browser = pychromecast.get_chromecasts(
                known_hosts=[device.host],
                timeout=self.settings.cast_timeout_seconds,
            )
        except Exception as exc:
            raise CastFailure(f"could not discover {device.name}: {exc}") from exc
        finally:
            browser = locals().get("browser")
            if browser is not None:
                pychromecast.discovery.stop_discovery(browser)

        target = next((cast for cast in casts if self._matches(cast, device)), None)
        if target is None:
            raise CastFailure(
                f"configured Nest Hub was not found: {device.name} ({device.host})"
            )
        self._casts[device_id] = target
        return target

    @staticmethod
    def _matches(cast: Any, device: DeviceSettings) -> bool:
        info = cast.cast_info
        if device.uuid and str(info.uuid).lower() == device.uuid.lower():
            return True
        if info.host != device.host or info.cast_type == "group":
            return False
        if device.model and info.model_name != device.model:
            return False
        return True

    def _close_sync(self) -> None:
        for cast in self._casts.values():
            disconnect = getattr(cast, "disconnect", None)
            if disconnect:
                try:
                    disconnect()
                except Exception:
                    pass
        self._casts.clear()
