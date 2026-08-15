from pathlib import Path

import pytest

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
