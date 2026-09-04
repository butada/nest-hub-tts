from __future__ import annotations

from dataclasses import dataclass

from .audio_cache import AudioCache
from .speech import REPLAY_PREFIX, SpeechSpec
from .tts import GeminiTTS, concatenate_mp3


@dataclass(frozen=True)
class ReplayAudioResult:
    data: bytes
    body_cache_hit: bool
    prefix_cache_hit: bool
    tts_request_count: int
    composed: bool


async def build_replay_audio(
    tts: GeminiTTS,
    audio_cache: AudioCache,
    spec: SpeechSpec,
    model: str,
) -> ReplayAudioResult:
    if not spec.is_replay:
        raise ValueError("replay audio requested for non-replay speech")

    body_key = spec.body_cache_key(model)
    prefix_key = spec.replay_prefix_cache_key(model)
    body_audio = audio_cache.get(body_key) if spec.body_text else None
    prefix_audio = audio_cache.get(prefix_key)
    body_cache_hit = body_audio is not None or not spec.body_text
    prefix_cache_hit = prefix_audio is not None

    # A cold replay cache is cheaper as one full TTS request. Component caching
    # starts paying off once either the normal body or fixed prefix already exists.
    if body_audio is None and spec.body_text and prefix_audio is None:
        generated = await tts.synthesize(
            spec.spoken_text,
            spec.voice,
            spec.style,
            spec.audio_profile,
            spec.audio_speed,
        )
        return ReplayAudioResult(
            data=generated.data,
            body_cache_hit=False,
            prefix_cache_hit=False,
            tts_request_count=1,
            composed=False,
        )

    tts_request_count = 0
    if prefix_audio is None:
        generated_prefix = await tts.synthesize(
            REPLAY_PREFIX,
            spec.voice,
            spec.style,
            spec.audio_profile,
            spec.audio_speed,
        )
        prefix_audio = audio_cache.save(prefix_key, generated_prefix.data)
        tts_request_count += 1

    parts = [prefix_audio.path.read_bytes()]
    if spec.body_text:
        if body_audio is None:
            generated_body = await tts.synthesize(
                spec.body_text,
                spec.voice,
                spec.style,
                spec.audio_profile,
                spec.audio_speed,
            )
            body_audio = audio_cache.save(body_key, generated_body.data)
            tts_request_count += 1
        parts.append(body_audio.path.read_bytes())

    return ReplayAudioResult(
        data=concatenate_mp3(parts),
        body_cache_hit=body_cache_hit,
        prefix_cache_hit=prefix_cache_hit,
        tts_request_count=tts_request_count,
        composed=len(parts) > 1,
    )
