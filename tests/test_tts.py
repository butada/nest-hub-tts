import base64
import json

import httpx
import pytest

from nest_hub_tts.config import Settings
from nest_hub_tts.tts import GeminiTTS, _audio_from_dict, _find_audio


def test_find_audio_from_interactions_steps() -> None:
    encoded = base64.b64encode(b"audio").decode()
    result = _find_audio(
        {
            "steps": [
                {
                    "type": "model_output",
                    "content": [
                        {
                            "type": "audio",
                            "data": encoded,
                            "mime_type": "audio/mp3",
                        }
                    ],
                }
            ]
        }
    )
    assert result == (encoded, "audio/mp3", None, None)


def test_audio_mime_parameters_are_parsed() -> None:
    encoded = base64.b64encode(b"pcm").decode()
    result = _audio_from_dict(
        {
            "type": "audio",
            "data": encoded,
            "mime_type": "audio/l16; rate=24000; channels=1",
        }
    )
    assert result == (encoded, "audio/l16", 24000, 1)


@pytest.mark.asyncio
async def test_gemini_tts_reads_mp3_audio_response() -> None:
    encoded = base64.b64encode(b"audio").decode()

    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "steps": [
                    {
                        "type": "model_output",
                        "content": [
                            {"type": "audio", "data": encoded, "mime_type": "audio/mp3"}
                        ],
                    }
                ]
            },
        )

    tts = GeminiTTS(Settings(gemini_api_key="test-key"))
    await tts.client.aclose()
    tts.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    result = await tts.synthesize("テスト", "Kore", "自然に")
    await tts.close()

    assert result.data == b"audio"
    assert result.mime_type == "audio/mpeg"


@pytest.mark.asyncio
async def test_gemini_tts_submits_batch_generate_content_request() -> None:
    seen: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["json"] = json.loads(request.content)
        return httpx.Response(200, json={"name": "batches/test-batch"})

    tts = GeminiTTS(Settings(gemini_api_key="test-key"))
    await tts.client.aclose()
    tts.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    result = await tts.submit_batch("request-123", "テスト", "Kore", "自然に")
    await tts.close()

    assert result == "batches/test-batch"
    assert seen["url"] == (
        "https://generativelanguage.googleapis.com/v1beta/"
        "models/gemini-3.1-flash-tts-preview:batchGenerateContent"
    )
    payload = seen["json"]
    assert isinstance(payload, dict)
    request = payload["batch"]["input_config"]["requests"]["requests"][0]["request"]
    assert request["generationConfig"]["responseModalities"] == ["AUDIO"]
    assert request["generationConfig"]["speechConfig"]["voiceConfig"]["prebuiltVoiceConfig"][
        "voiceName"
    ] == "Kore"


def test_gemini_tts_reads_generate_content_batch_audio() -> None:
    encoded = base64.b64encode(b"audio").decode()
    result = GeminiTTS.audio_from_batch(
        {
            "state": "JOB_STATE_SUCCEEDED",
            "output": {
                "inlinedResponses": {
                    "inlinedResponses": [
                        {
                            "response": {
                                "candidates": [
                                    {
                                        "content": {
                                            "parts": [
                                                {
                                                    "inlineData": {
                                                        "data": encoded,
                                                        "mimeType": "audio/mp3",
                                                    }
                                                }
                                            ]
                                        }
                                    }
                                ]
                            }
                        }
                    ]
                }
            },
        }
    )
    assert result.data == b"audio"
    assert result.mime_type == "audio/mpeg"
