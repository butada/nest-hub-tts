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
    def input_has_replay_prefix(self) -> bool:
        return self.normalized_text.startswith(REPLAY_PREFIX)

    @property
    def body_text(self) -> str:
        if self.input_has_replay_prefix:
            return self.normalized_text[len(REPLAY_PREFIX) :].lstrip()
        return self.normalized_text

    @property
    def is_replay(self) -> bool:
        return self.replay or self.input_has_replay_prefix

    @property
    def spoken_text(self) -> str:
        if not self.is_replay:
            return self.body_text
        if not self.body_text:
            return REPLAY_PREFIX
        return f"{REPLAY_PREFIX}\n{self.body_text}"

    def cache_key(self, model: str) -> dict[str, object]:
        return self._cache_key(model, self.spoken_text)

    def body_cache_key(self, model: str) -> dict[str, object]:
        return self._cache_key(model, self.body_text)

    def replay_prefix_cache_key(self, model: str) -> dict[str, object]:
        return self._cache_key(model, REPLAY_PREFIX)

    def _cache_key(self, model: str, text: str) -> dict[str, object]:
        return {
            "version": SPEECH_CACHE_VERSION,
            "model": model,
            "text": text,
            "voice": self.voice,
            "style": self.style,
            "audio_profile": self.audio_profile,
            "audio_speed": self.audio_speed,
        }
