from __future__ import annotations

import asyncio
import hmac
import logging
import uuid
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response, status
from fastapi.responses import FileResponse, JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .audio_cache import AudioCache, AudioCacheNotFoundError, AudioCacheSignatureError
from .batch import BatchJobStore, BatchRunner, batch_response_to_dict
from .cast import CastController, CastFailure
from .config import get_settings
from .media import MediaNotFoundError, MediaSignatureError, MediaStore
from .models import AudioUrlResponse, BatchJobResponse, DeviceResponse, SpeakRequest, SpeakResponse
from .speech import SpeechSpec
from .tts import BatchFailure, GeminiTTS, TTSFailure

logger = logging.getLogger("nest_hub_tts")
settings = get_settings()
media_store = MediaStore(
    settings.media_dir,
    settings.media_public_base_url,
    settings.media_ttl_seconds,
    settings.media_signing_secret,
)
audio_cache = AudioCache(
    settings.audio_cache_dir,
    settings.media_public_base_url,
    settings.audio_cache_ttl_seconds,
    settings.audio_url_ttl_seconds,
    settings.media_signing_secret,
)
tts = GeminiTTS(settings)
cast_controller = CastController(settings)
batch_store = BatchJobStore(settings.batch_db_path)
batch_runner = BatchRunner(
    settings,
    tts,
    media_store,
    cast_controller,
    batch_store,
    audio_cache,
)
bearer = HTTPBearer(auto_error=False)


async def _cleanup_loop() -> None:
    while True:
        await asyncio.sleep(60)
        removed = media_store.cleanup()
        removed_cache = audio_cache.cleanup()
        if removed or removed_cache:
            logger.info(
                "removed_expired_files media_count=%s cache_count=%s",
                removed,
                removed_cache,
            )


@asynccontextmanager
async def lifespan(_: FastAPI):
    cleanup_task = asyncio.create_task(_cleanup_loop())
    await batch_runner.start()
    yield
    cleanup_task.cancel()
    await batch_runner.close()
    await tts.close()
    await cast_controller.close()
    media_store.cleanup()
    audio_cache.cleanup()


app = FastAPI(title="Nest Hub TTS", version="0.1.0", lifespan=lifespan)


async def require_auth(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),  # noqa: B008
) -> None:
    if not settings.api_token:
        return
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Bearer token required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if not hmac.compare_digest(credentials.credentials, settings.api_token):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid token")


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/v1/devices", response_model=list[DeviceResponse], dependencies=[Depends(require_auth)])
async def devices() -> list[DeviceResponse]:
    return [
        DeviceResponse(
            id=device_id,
            name=device.name,
            host=device.host,
            model=device.model,
        )
        for device_id, device in settings.cast_devices.items()
    ]


@app.get(
    "/v1/jobs/{request_id}",
    response_model=BatchJobResponse,
    dependencies=[Depends(require_auth)],
)
async def job_status(request_id: str) -> BatchJobResponse:
    row = batch_store.get(request_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"Unknown request_id: {request_id}")
    data = batch_response_to_dict(row)
    if data["audio_id"]:
        data["audio_url"] = audio_cache.signed_url(data["audio_id"])
    return BatchJobResponse(**data)


@app.get("/media/{media_id}.mp3")
async def media(
    media_id: str,
    expires: int = Query(..., ge=0),
    sig: str = Query(..., min_length=1),
) -> Response:
    try:
        path = media_store.resolve(media_id, expires, sig)
    except MediaSignatureError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except MediaNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Media not found") from exc
    return FileResponse(path, media_type="audio/mpeg", filename=f"{media_id}.mp3")


@app.get("/audio/{audio_id}.mp3")
async def cached_audio_public(
    audio_id: str,
    expires: int = Query(..., ge=0),
    sig: str = Query(..., min_length=1),
) -> Response:
    try:
        path = audio_cache.resolve_signed(audio_id, expires, sig)
    except AudioCacheSignatureError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except AudioCacheNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Cached audio not found") from exc
    return FileResponse(path, media_type="audio/mpeg", filename=f"{audio_id}.mp3")


@app.get(
    "/v1/audio/{audio_id}.mp3",
    dependencies=[Depends(require_auth)],
)
async def cached_audio_api(audio_id: str) -> Response:
    try:
        audio = audio_cache.get_by_id(audio_id)
    except AudioCacheNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Cached audio not found") from exc
    if audio is None:
        raise HTTPException(status_code=404, detail="Cached audio not found")
    return FileResponse(audio.path, media_type="audio/mpeg", filename=f"{audio_id}.mp3")


@app.get(
    "/v1/audio/{audio_id}",
    response_model=AudioUrlResponse,
    dependencies=[Depends(require_auth)],
)
async def cached_audio_url(audio_id: str) -> AudioUrlResponse:
    try:
        audio = audio_cache.get_by_id(audio_id)
    except AudioCacheNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Cached audio not found") from exc
    if audio is None:
        raise HTTPException(status_code=404, detail="Cached audio not found")
    return AudioUrlResponse(audio_id=audio.audio_id, audio_url=audio_cache.signed_url(audio))


@app.post(
    "/v1/speak",
    response_model=SpeakResponse,
    responses={202: {"model": SpeakResponse, "description": "Batch job accepted"}},
    dependencies=[Depends(require_auth)],
)
async def speak(payload: SpeakRequest, request: Request) -> SpeakResponse:
    request_id = uuid.uuid4().hex
    device = settings.cast_devices.get(payload.device_id)
    if device is None:
        raise HTTPException(status_code=404, detail=f"Unknown device_id: {payload.device_id}")
    if len(payload.text) > settings.max_text_length:
        raise HTTPException(
            status_code=413,
            detail=f"text exceeds MAX_TEXT_LENGTH ({settings.max_text_length})",
        )

    spec = SpeechSpec(
        text=payload.text,
        voice=payload.voice or settings.gemini_tts_voice,
        style=payload.style or settings.gemini_tts_style,
        audio_profile=payload.audio_profile or settings.audio_profile,
        audio_speed=payload.audio_speed
        if payload.audio_speed is not None
        else settings.audio_speed,
        replay=payload.replay,
    )
    cache_key = spec.cache_key(settings.gemini_model)
    cached = audio_cache.get(cache_key) if payload.cache else None

    if cached is not None:
        try:
            result = await cast_controller.play(
                payload.device_id,
                device,
                audio_cache.signed_url(cached),
                payload.title or "Nest Hub TTS",
            )
        except CastFailure as exc:
            logger.warning("cached_cast_failed request_id=%s error=%s", request_id, exc)
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        return SpeakResponse(
            request_id=request_id,
            device_id=payload.device_id,
            execution=payload.execution,
            status=result.status,
            cache_hit=True,
            tts_generated=False,
            audio_id=cached.audio_id,
            audio_url=audio_cache.signed_url(cached),
        )

    if payload.execution == "batch":
        try:
            batch_name = await batch_runner.submit(
                request_id,
                payload.device_id,
                device,
                spec.spoken_text,
                spec.voice,
                spec.style,
                payload.title or "Nest Hub TTS",
                cache_key if payload.cache else None,
                spec.audio_profile,
                spec.audio_speed,
            )
        except (BatchFailure, TTSFailure) as exc:
            logger.warning("batch_submit_failed request_id=%s error=%s", request_id, exc)
            raise HTTPException(status_code=502, detail=str(exc)) from exc

        logger.info(
            "batch_submitted request_id=%s batch_name=%s device_id=%s client=%s",
            request_id,
            batch_name,
            payload.device_id,
            request.client.host if request.client else "unknown",
        )
        response = SpeakResponse(
            request_id=request_id,
            device_id=payload.device_id,
            execution="batch",
            status="batch_submitted",
            batch_name=batch_name,
            cache_hit=False,
            tts_generated=None,
        )
        return JSONResponse(status_code=202, content=response.model_dump())  # type: ignore[return-value]

    media_store.cleanup()
    try:
        generated = await tts.synthesize(
            spec.spoken_text,
            spec.voice,
            spec.style,
            spec.audio_profile,
            spec.audio_speed,
        )
        audio_id = None
        audio_url = None
        if payload.cache:
            cached = audio_cache.save(cache_key, generated.data)
            media_url = audio_cache.signed_url(cached)
            audio_id = cached.audio_id
            audio_url = media_url
        else:
            asset = media_store.save_mp3(generated.data)
            media_url = media_store.signed_url(asset)
        result = await cast_controller.play(
            payload.device_id,
            device,
            media_url,
            payload.title or "Nest Hub TTS",
        )
    except TTSFailure as exc:
        logger.warning("tts_failed request_id=%s error=%s", request_id, exc)
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except CastFailure as exc:
        logger.warning("cast_failed request_id=%s error=%s", request_id, exc)
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    logger.info(
        "speak_succeeded request_id=%s device_id=%s status=%s client=%s",
        request_id,
        payload.device_id,
        result.status,
        request.client.host if request.client else "unknown",
    )
    return SpeakResponse(
        request_id=request_id,
        device_id=payload.device_id,
        execution="realtime",
        status=result.status,
        cache_hit=False,
        tts_generated=True,
        audio_id=audio_id,
        audio_url=audio_url,
    )


@app.exception_handler(Exception)
async def unexpected_error(_: Request, exc: Exception) -> JSONResponse:
    logger.exception("unexpected_error", exc_info=exc)
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})
