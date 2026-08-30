from nest_hub_tts.speech import REPLAY_PREFIX, SpeechSpec


def test_replay_speech_spec_adds_prefix_to_spoken_text_and_cache_key() -> None:
    normal = SpeechSpec("本文", "Kore", "自然に", "clear_speech", 1.08, False)
    replay = SpeechSpec("本文", "Kore", "自然に", "clear_speech", 1.08, True)

    assert normal.spoken_text == "本文"
    assert replay.spoken_text == f"{REPLAY_PREFIX}\n本文"
    assert normal.cache_key("test-model") != replay.cache_key("test-model")
    normalized = SpeechSpec("本文\r\n", "Kore", "自然に", "clear_speech", 1.08, False)
    assert normalized.spoken_text == "本文"
