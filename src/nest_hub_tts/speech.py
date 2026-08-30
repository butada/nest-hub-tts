from __future__ import annotations

from dataclasses import dataclass

REPLAY_PREFIX = "これは再放送です。"
SPEECH_CACHE_VERSION = "speech-v2"


@dataclass(frozen=True)
class SpeechSpec:
    text: str
    voice: str
    style: str
    audio_profile: str
    audio_speed: float
    replay: bool

    @property
    def normalized_text(self) -> str:
        return self.text.replace("\r\n", "\n").replace("\r", "\n").strip()

    @property
    def spoken_text(self) -> str:
        if self.replay:
            return f"{REPLAY_PREFIX}\n{self.normalized_text}"
        return self.normalized_text

    def cache_key(self, model: str) -> dict[str, object]:
        return {
            "version": SPEECH_CACHE_VERSION,
            "model": model,
            "text": self.spoken_text,
            "voice": self.voice,
            "style": self.style,
            "audio_profile": self.audio_profile,
            "audio_speed": self.audio_speed,
        }
