import os
import time
from pathlib import Path

import pytest

from nest_hub_tts.audio_cache import (
    AudioCache,
    AudioCacheNotFoundError,
    AudioCacheSignatureError,
)
from nest_hub_tts.media import MediaNotFoundError, MediaSignatureError, MediaStore


def test_media_store_signs_and_resolves(tmp_path: Path) -> None:
    store = MediaStore(tmp_path, "http://192.168.50.203:8080", 3600, "secret")
    asset = store.save_mp3(b"fake mp3")
    url = store.signed_url(asset)
    assert url.startswith("http://192.168.50.203:8080/media/")

    query = url.split("?", 1)[1].split("&")
    expires = int(query[0].split("=", 1)[1])
    signature = query[1].split("=", 1)[1]
    assert store.resolve(asset.media_id, expires, signature) == asset.path


def test_media_store_rejects_bad_signature(tmp_path: Path) -> None:
    store = MediaStore(tmp_path, "http://localhost:8080", 3600, "secret")
    asset = store.save_mp3(b"fake mp3")
    with pytest.raises(MediaSignatureError):
        store.resolve(asset.media_id, 9999999999, "bad")


def test_media_store_rejects_unknown_file(tmp_path: Path) -> None:
    store = MediaStore(tmp_path, "http://localhost:8080", 3600, "secret")
    media_id = "0" * 32
    signature = store._signature(media_id, 9999999999)
    with pytest.raises(MediaNotFoundError):
        store.resolve(media_id, 9999999999, signature)


def test_audio_cache_reuses_key_and_resolves_signed_url(tmp_path: Path) -> None:
    cache = AudioCache(
        tmp_path,
        "http://192.168.50.7:8005",
        3600,
        3600,
        "secret",
    )
    key = {"text": "テスト", "voice": "Kore", "profile": "clear_speech"}
    first = cache.save(key, b"fake mp3")
    second = cache.save(key, b"different bytes")
    assert first.audio_id == second.audio_id
    assert first.path.read_bytes() == b"fake mp3"

    url = cache.signed_url(first)
    assert url.startswith("http://192.168.50.7:8005/audio/")
    query = url.split("?", 1)[1].split("&")
    expires = int(query[0].split("=", 1)[1])
    signature = query[1].split("=", 1)[1]
    assert cache.resolve_signed(first.audio_id, expires, signature) == first.path


def test_audio_cache_rejects_invalid_access(tmp_path: Path) -> None:
    cache = AudioCache(tmp_path, "http://localhost:8080", 3600, 3600, "secret")
    with pytest.raises(AudioCacheNotFoundError):
        cache.get_by_id("0" * 64)
    with pytest.raises(AudioCacheSignatureError):
        cache.resolve_signed("0" * 64, 9999999999, "bad")


def test_audio_cache_returns_latest_audio(tmp_path: Path) -> None:
    cache = AudioCache(tmp_path, "http://localhost:8080", 3600, 3600, "secret")
    first = cache.save({"text": "最初"}, b"first")
    second = cache.save({"text": "最新"}, b"second")
    old = time.time() - 10
    os.utime(first.path, (old, old))

    latest = cache.latest()

    assert latest == second
    assert latest is not None
    assert latest.path.read_bytes() == b"second"


def test_audio_cache_latest_is_empty_when_no_audio(tmp_path: Path) -> None:
    cache = AudioCache(tmp_path, "http://localhost:8080", 3600, 3600, "secret")

    assert cache.latest() is None
