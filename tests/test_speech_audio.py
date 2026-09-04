from pathlib import Path

import pytest

from nest_hub_tts.audio_cache import AudioCache
from nest_hub_tts.speech import REPLAY_PREFIX, SpeechSpec
from nest_hub_tts.speech_audio import build_replay_audio
from nest_hub_tts.tts import GeneratedAudio


def _spec(text: str, *, replay: bool = False) -> SpeechSpec:
    return SpeechSpec(text, "Kore", "自然に", "natural", 1.0, replay)


class FakeTTS:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def synthesize(
        self,
        text: str,
        voice: str,
        style: str,
        audio_profile: str,
        audio_speed: float,
    ) -> GeneratedAudio:
        self.calls.append(text)
        return GeneratedAudio(data=text.encode("utf-8"), mime_type="audio/mpeg")


@pytest.mark.asyncio
async def test_replay_reuses_body_and_caches_fixed_prefix(tmp_path: Path, monkeypatch) -> None:
    cache = AudioCache(tmp_path, "http://localhost:8080", 3600, 3600, "secret")
    tts = FakeTTS()
    monkeypatch.setattr("nest_hub_tts.speech_audio.concatenate_mp3", lambda parts: b"|".join(parts))
    spec = _spec("本文", replay=True)
    cache.save(spec.body_cache_key("test-model"), b"body-audio")

    result = await build_replay_audio(tts, cache, spec, "test-model")

    assert result.data == f"{REPLAY_PREFIX}|body-audio".encode("utf-8")
    assert result.body_cache_hit is True
    assert result.prefix_cache_hit is False
    assert result.tts_request_count == 1
    assert tts.calls == [REPLAY_PREFIX]
    assert cache.get(spec.replay_prefix_cache_key("test-model")) is not None


@pytest.mark.asyncio
async def test_replay_cold_cache_uses_one_full_tts_request(tmp_path: Path, monkeypatch) -> None:
    cache = AudioCache(tmp_path, "http://localhost:8080", 3600, 3600, "secret")
    tts = FakeTTS()
    monkeypatch.setattr("nest_hub_tts.speech_audio.concatenate_mp3", lambda parts: b"|".join(parts))
    spec = _spec("本文", replay=True)

    result = await build_replay_audio(tts, cache, spec, "test-model")

    assert result.data == spec.spoken_text.encode("utf-8")
    assert result.tts_request_count == 1
    assert result.composed is False
    assert tts.calls == [spec.spoken_text]


@pytest.mark.asyncio
async def test_replay_with_both_components_uses_no_tts_request(tmp_path: Path, monkeypatch) -> None:
    cache = AudioCache(tmp_path, "http://localhost:8080", 3600, 3600, "secret")
    tts = FakeTTS()
    monkeypatch.setattr("nest_hub_tts.speech_audio.concatenate_mp3", lambda parts: b"|".join(parts))
    spec = _spec("本文", replay=True)
    cache.save(spec.body_cache_key("test-model"), b"body-audio")
    cache.save(spec.replay_prefix_cache_key("test-model"), b"prefix-audio")

    result = await build_replay_audio(tts, cache, spec, "test-model")

    assert result.data == b"prefix-audio|body-audio"
    assert result.body_cache_hit is True
    assert result.prefix_cache_hit is True
    assert result.tts_request_count == 0
    assert tts.calls == []
