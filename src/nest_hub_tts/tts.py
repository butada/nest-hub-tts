from __future__ import annotations

import base64
import json
import subprocess
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import httpx

from .config import Settings


class TTSFailure(RuntimeError):
    pass


class BatchFailure(TTSFailure):
    pass


@dataclass(frozen=True)
class GeneratedAudio:
    data: bytes
    mime_type: str


class GeminiTTS:
    endpoint = "https://generativelanguage.googleapis.com/v1beta/interactions"
    batch_endpoint_template = (
        "https://generativelanguage.googleapis.com/v1beta/models/{model}:batchGenerateContent"
    )

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.client = httpx.AsyncClient(timeout=settings.gemini_timeout_seconds)

    async def close(self) -> None:
        await self.client.aclose()

    async def synthesize(self, text: str, voice: str, style: str) -> GeneratedAudio:
        if not self.settings.gemini_api_key:
            raise TTSFailure("GEMINI_API_KEY is not configured")

        prompt = _build_prompt(text, style)
        payload = {
            "model": self.settings.gemini_model,
            "input": prompt,
            "response_format": {"type": "audio"},
            "generation_config": {"speech_config": [{"voice": voice}]},
        }

        try:
            response = await self.client.post(
                self.endpoint,
                headers={
                    "x-goog-api-key": self.settings.gemini_api_key,
                    "Content-Type": "application/json",
                },
                json=payload,
            )
        except httpx.HTTPError as exc:
            raise TTSFailure(f"Gemini API request failed: {exc}") from exc

        if response.is_error:
            raise TTSFailure(self._error_message(response))

        try:
            result = response.json()
        except json.JSONDecodeError as exc:
            raise TTSFailure("Gemini API returned invalid JSON") from exc

        audio = _find_audio(result)
        if audio is None:
            raise TTSFailure("Gemini API response did not contain audio data")

        return _audio_tuple_to_mp3(audio)

    async def submit_batch(self, request_id: str, text: str, voice: str, style: str) -> str:
        if not self.settings.gemini_api_key:
            raise BatchFailure("GEMINI_API_KEY is not configured")

        request = {
            "contents": [{"role": "user", "parts": [{"text": _build_prompt(text, style)}]}],
            "generationConfig": {
                "responseModalities": ["AUDIO"],
                "speechConfig": {
                    "voiceConfig": {
                        "prebuiltVoiceConfig": {"voiceName": voice},
                    }
                },
            },
        }
        payload = {
            "batch": {
                "display_name": f"nest-hub-tts-{request_id[:16]}",
                "input_config": {
                    "requests": {
                        "requests": [
                            {
                                "request": request,
                                "metadata": {"key": request_id},
                            }
                        ]
                    }
                },
            }
        }
        endpoint = self.batch_endpoint_template.format(
            model=quote(self.settings.gemini_model, safe="")
        )
        try:
            response = await self.client.post(
                endpoint,
                headers={
                    "x-goog-api-key": self.settings.gemini_api_key,
                    "Content-Type": "application/json",
                },
                json=payload,
            )
        except httpx.HTTPError as exc:
            raise BatchFailure(f"Gemini Batch API request failed: {exc}") from exc

        if response.is_error:
            raise BatchFailure(self._error_message(response))
        try:
            result = response.json()
        except json.JSONDecodeError as exc:
            raise BatchFailure("Gemini Batch API returned invalid JSON") from exc

        batch_name = result.get("name") if isinstance(result, dict) else None
        if not isinstance(batch_name, str) or not batch_name:
            raise BatchFailure("Gemini Batch API response did not contain a batch name")
        return batch_name

    async def get_batch(self, batch_name: str) -> dict[str, Any]:
        if not self.settings.gemini_api_key:
            raise BatchFailure("GEMINI_API_KEY is not configured")
        endpoint = f"https://generativelanguage.googleapis.com/v1beta/{batch_name}"
        try:
            response = await self.client.get(
                endpoint,
                headers={"x-goog-api-key": self.settings.gemini_api_key},
            )
        except httpx.HTTPError as exc:
            raise BatchFailure(f"Gemini Batch API status request failed: {exc}") from exc
        if response.is_error:
            raise BatchFailure(self._error_message(response))
        try:
            result = response.json()
        except json.JSONDecodeError as exc:
            raise BatchFailure("Gemini Batch API returned invalid JSON") from exc
        if not isinstance(result, dict):
            raise BatchFailure("Gemini Batch API returned an invalid batch status")
        return result

    @staticmethod
    def batch_state(batch: dict[str, Any]) -> str:
        state = batch.get("state")
        if state is None and isinstance(batch.get("metadata"), dict):
            state = batch["metadata"].get("state")
        if isinstance(state, dict):
            state = state.get("name") or state.get("value")
        if isinstance(state, str):
            normalized = state.upper()
            if normalized in {"JOB_STATE_SUCCEEDED", "SUCCEEDED", "DONE"}:
                return "succeeded"
            if normalized in {"JOB_STATE_FAILED", "FAILED"}:
                return "failed"
            if normalized in {"JOB_STATE_CANCELLED", "CANCELLED"}:
                return "failed"
            if normalized in {"JOB_STATE_EXPIRED", "EXPIRED"}:
                return "failed"
            if normalized in {"JOB_STATE_RUNNING", "RUNNING", "PROCESSING"}:
                return "running"
            if normalized in {"JOB_STATE_PENDING", "PENDING", "QUEUED", "STATE_UNSPECIFIED"}:
                return "submitted"

        if batch.get("done") is True:
            return "failed" if batch.get("error") else "succeeded"
        return "running"

    @staticmethod
    def batch_error(batch: dict[str, Any]) -> str | None:
        error = batch.get("error")
        if error is None and isinstance(batch.get("metadata"), dict):
            error = batch["metadata"].get("error")
        if error is None:
            return None
        if isinstance(error, dict):
            return str(error.get("message") or error)
        return str(error)

    @staticmethod
    def audio_from_batch(batch: dict[str, Any]) -> GeneratedAudio:
        responses = _find_batch_responses(batch)
        if not responses:
            raise BatchFailure("Gemini Batch API response did not contain inline responses")

        first = responses[0]
        if not isinstance(first, dict):
            raise BatchFailure("Gemini Batch API returned an invalid inline response")
        if first.get("error"):
            error = first["error"]
            raise BatchFailure(str(error.get("message") if isinstance(error, dict) else error))
        response = first.get("response") or first.get("output") or first
        audio = _find_audio(response)
        if audio is None:
            raise BatchFailure("Gemini Batch API response did not contain audio data")
        return _audio_tuple_to_mp3(audio)

    @staticmethod
    def _error_message(response: httpx.Response) -> str:
        try:
            body = response.json()
            error = body.get("error", body) if isinstance(body, dict) else body
            if isinstance(error, dict) and error.get("message"):
                return f"Gemini API returned HTTP {response.status_code}: {error['message']}"
        except (ValueError, TypeError):
            pass
        return f"Gemini API returned HTTP {response.status_code}"


def _find_audio(value: Any) -> tuple[str, str, int | None, int | None] | None:
    if isinstance(value, dict):
        if isinstance(value.get("output_audio"), dict):
            found = _audio_from_dict(value["output_audio"])
            if found:
                return found

        if value.get("type") == "audio":
            found = _audio_from_dict(value)
            if found:
                return found

        for key in ("inlineData", "inline_data"):
            if isinstance(value.get(key), dict):
                found = _audio_from_dict(value[key])
                if found:
                    return found

        for child in value.values():
            found = _find_audio(child)
            if found:
                return found

    elif isinstance(value, list):
        for child in value:
            found = _find_audio(child)
            if found:
                return found

    return None


def _audio_from_dict(value: dict[str, Any]) -> tuple[str, str, int | None, int | None] | None:
    data = value.get("data")
    if not isinstance(data, str) or not data:
        return None
    raw_mime_type = value.get("mime_type") or value.get("mimeType") or "audio/l16"
    mime_type, mime_params = _split_mime_type(str(raw_mime_type))
    sample_rate = value.get("sample_rate") or value.get("sampleRate")
    channels = value.get("channels")
    sample_rate = _as_int(sample_rate) or _as_int(mime_params.get("rate"))
    channels = _as_int(channels) or _as_int(mime_params.get("channels"))
    return data, mime_type, sample_rate, channels


def _build_prompt(text: str, style: str) -> str:
    return (
        "次の指示に従って、指定されたテキストだけを日本語で読み上げてください。"
        "テキストの内容を要約、翻訳、追加、変更しないでください。\n"
        f"発話スタイル: {style}\n"
        f"発話テキスト:\n{text}"
    )


def _audio_tuple_to_mp3(
    audio: tuple[str, str, int | None, int | None],
) -> GeneratedAudio:
    raw_data, mime_type, sample_rate, channels = audio
    try:
        decoded = base64.b64decode(raw_data)
    except (ValueError, TypeError) as exc:
        raise TTSFailure("Gemini audio data was not valid base64") from exc

    if mime_type.lower() in {"audio/mp3", "audio/mpeg"}:
        return GeneratedAudio(data=decoded, mime_type="audio/mpeg")

    return GeneratedAudio(
        data=_transcode_to_mp3(
            decoded,
            mime_type=mime_type,
            sample_rate=sample_rate or 24000,
            channels=channels or 1,
        ),
        mime_type="audio/mpeg",
    )


def _find_batch_responses(value: dict[str, Any]) -> list[Any] | None:
    candidates: list[Any] = [value]
    for key in ("response", "output", "dest"):
        nested = value.get(key)
        if isinstance(nested, dict):
            candidates.append(nested)
            for nested_key in ("response", "output", "dest"):
                deeper = nested.get(nested_key)
                if isinstance(deeper, dict):
                    candidates.append(deeper)

    for candidate in candidates:
        inlined = candidate.get("inlinedResponses")
        if isinstance(inlined, dict):
            inlined = inlined.get("responses") or inlined.get("inlinedResponses")
        if isinstance(inlined, list):
            return inlined
    return None


def _split_mime_type(value: str) -> tuple[str, dict[str, str]]:
    parts = [part.strip() for part in value.split(";")]
    mime_type = parts[0].lower()
    params: dict[str, str] = {}
    for part in parts[1:]:
        if "=" not in part:
            continue
        key, parameter = part.split("=", 1)
        params[key.strip().lower()] = parameter.strip()
    return mime_type, params


def _as_int(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _transcode_to_mp3(
    data: bytes,
    *,
    mime_type: str,
    sample_rate: int,
    channels: int,
) -> bytes:
    command = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y"]
    if mime_type.lower() in {"audio/l16", "audio/pcm", "audio/pcm_s16le"}:
        command.extend(
            [
                "-f",
                "s16le",
                "-ar",
                str(sample_rate),
                "-ac",
                str(channels),
                "-i",
                "pipe:0",
            ]
        )
    else:
        command.extend(["-i", "pipe:0"])

    command.extend(
        [
            "-vn",
            "-ac",
            "1",
            "-ar",
            "24000",
            "-codec:a",
            "libmp3lame",
            "-b:a",
            "96k",
            "-f",
            "mp3",
            "pipe:1",
        ]
    )

    try:
        result = subprocess.run(
            command,
            input=data,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
    except FileNotFoundError as exc:
        raise TTSFailure("ffmpeg is required when Gemini does not return MP3") from exc

    if result.returncode != 0 or not result.stdout:
        detail = result.stderr.decode("utf-8", errors="replace")[-500:]
        raise TTSFailure(f"ffmpeg could not convert Gemini audio: {detail}")
    return result.stdout
