import json
from pathlib import Path

from nest_hub_tts.speech import REPLAY_PREFIX, SpeechSpec
from nest_hub_tts.usage_log import UsageLog


def _spec(text: str, *, replay: bool = False) -> SpeechSpec:
    return SpeechSpec(
        text=text,
        voice="Kore",
        style="自然に話す",
        audio_profile="clear_speech",
        audio_speed=1.08,
        replay=replay,
    )


def test_usage_log_records_cache_fields_without_plain_text(tmp_path: Path) -> None:
    path = tmp_path / "usage.jsonl"
    usage_log = UsageLog(path, 1024 * 1024, 2)
    usage_log.record_cache_decision(
        request_id="request-1",
        device_id="living-room",
        execution="realtime",
        client="127.0.0.1",
        cache_enabled=True,
        cache_hit=False,
        cache_key_id="a" * 64,
        model="test-model",
        spec=_spec("秘密の本文"),
    )
    usage_log.record(
        "speak_result",
        request_id="request-1",
        outcome="succeeded",
        tts_generated=True,
    )
    usage_log.close()

    lines = path.read_text(encoding="utf-8").splitlines()
    decision, result = (json.loads(line) for line in lines)
    assert decision["event"] == "cache_decision"
    assert decision["cache_hit"] is False
    assert decision["cache_key_id"] == "a" * 64
    assert decision["text_length"] == 5
    assert "秘密の本文" not in path.read_text(encoding="utf-8")
    assert "自然に話す" not in path.read_text(encoding="utf-8")
    assert result["event"] == "speak_result"
    assert result["tts_generated"] is True


def test_usage_log_matches_body_hash_when_replay_prefix_is_in_text(tmp_path: Path) -> None:
    path = tmp_path / "usage.jsonl"
    usage_log = UsageLog(path, 1024 * 1024, 2)
    common = {
        "device_id": "living-room",
        "execution": "realtime",
        "client": "127.0.0.1",
        "cache_enabled": True,
        "cache_hit": False,
        "model": "test-model",
    }
    usage_log.record_cache_decision(
        request_id="normal",
        cache_key_id="a" * 64,
        spec=_spec("同じ本文"),
        **common,
    )
    usage_log.record_cache_decision(
        request_id="replay",
        cache_key_id="b" * 64,
        spec=_spec(f"{REPLAY_PREFIX}同じ本文"),
        **common,
    )
    usage_log.close()

    normal, replay = (json.loads(line) for line in path.read_text(encoding="utf-8").splitlines())
    assert normal["text_sha256"] != replay["text_sha256"]
    assert normal["body_text_sha256"] == replay["body_text_sha256"]
    assert normal["input_has_replay_prefix"] is False
    assert replay["input_has_replay_prefix"] is True
