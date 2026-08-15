from __future__ import annotations

import asyncio
import logging
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .cast import CastController, CastFailure
from .config import DeviceSettings, Settings
from .media import MediaStore
from .tts import BatchFailure, GeminiTTS, TTSFailure

logger = logging.getLogger("nest_hub_tts.batch")


@dataclass(frozen=True)
class BatchJob:
    request_id: str
    batch_name: str
    device_id: str
    device: DeviceSettings
    title: str


class BatchJobStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS batch_jobs (
                    request_id TEXT PRIMARY KEY,
                    batch_name TEXT NOT NULL UNIQUE,
                    device_id TEXT NOT NULL,
                    device_name TEXT NOT NULL,
                    device_host TEXT NOT NULL,
                    device_uuid TEXT,
                    device_model TEXT,
                    title TEXT NOT NULL,
                    status TEXT NOT NULL,
                    error TEXT,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )

    def create(self, job: BatchJob) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO batch_jobs (
                    request_id, batch_name, device_id, device_name, device_host,
                    device_uuid, device_model, title, status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'submitted')
                """,
                (
                    job.request_id,
                    job.batch_name,
                    job.device_id,
                    job.device.name,
                    job.device.host,
                    job.device.uuid,
                    job.device.model,
                    job.title,
                ),
            )

    def update_status(self, request_id: str, status: str, error: str | None = None) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE batch_jobs
                SET status = ?, error = ?, updated_at = CURRENT_TIMESTAMP
                WHERE request_id = ?
                """,
                (status, error, request_id),
            )

    def get(self, request_id: str) -> sqlite3.Row | None:
        with self._connect() as connection:
            return connection.execute(
                "SELECT * FROM batch_jobs WHERE request_id = ?",
                (request_id,),
            ).fetchone()

    def active(self) -> list[sqlite3.Row]:
        with self._connect() as connection:
            return list(
                connection.execute(
                    "SELECT * FROM batch_jobs WHERE status IN ('submitted', 'running')"
                ).fetchall()
            )


def job_from_row(row: sqlite3.Row) -> BatchJob:
    return BatchJob(
        request_id=row["request_id"],
        batch_name=row["batch_name"],
        device_id=row["device_id"],
        device=DeviceSettings(
            name=row["device_name"],
            host=row["device_host"],
            uuid=row["device_uuid"],
            model=row["device_model"],
        ),
        title=row["title"],
    )


class BatchRunner:
    def __init__(
        self,
        settings: Settings,
        tts: GeminiTTS,
        media_store: MediaStore,
        cast_controller: CastController,
        store: BatchJobStore,
    ) -> None:
        self.settings = settings
        self.tts = tts
        self.media_store = media_store
        self.cast_controller = cast_controller
        self.store = store
        self._tasks: set[asyncio.Task[None]] = set()

    async def start(self) -> None:
        for row in self.store.active():
            self._start_task(job_from_row(row))

    async def close(self) -> None:
        tasks = list(self._tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()

    async def submit(
        self,
        request_id: str,
        device_id: str,
        device: DeviceSettings,
        text: str,
        voice: str,
        style: str,
        title: str,
    ) -> str:
        batch_name = await self.tts.submit_batch(request_id, text, voice, style)
        job = BatchJob(
            request_id=request_id,
            batch_name=batch_name,
            device_id=device_id,
            device=device,
            title=title,
        )
        self.store.create(job)
        self._start_task(job)
        return batch_name

    def _start_task(self, job: BatchJob) -> None:
        task = asyncio.create_task(self._run(job))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _run(self, job: BatchJob) -> None:
        self.store.update_status(job.request_id, "running")
        try:
            while True:
                batch = await self.tts.get_batch(job.batch_name)
                state = self.tts.batch_state(batch)
                if state in {"submitted", "running"}:
                    await asyncio.sleep(self.settings.gemini_batch_poll_interval_seconds)
                    continue
                if state != "succeeded":
                    error = self.tts.batch_error(batch) or f"Gemini Batch API state: {state}"
                    raise BatchFailure(error)

                generated = self.tts.audio_from_batch(batch)
                self.media_store.cleanup()
                asset = self.media_store.save_mp3(generated.data)
                media_url = self.media_store.signed_url(asset)
                await self.cast_controller.play(
                    job.device_id,
                    job.device,
                    media_url,
                    job.title,
                )
                self.store.update_status(job.request_id, "succeeded")
                logger.info(
                    "batch_succeeded request_id=%s batch_name=%s device_id=%s",
                    job.request_id,
                    job.batch_name,
                    job.device_id,
                )
                return
        except (BatchFailure, TTSFailure, CastFailure) as exc:
            self.store.update_status(job.request_id, "failed", str(exc))
            logger.warning(
                "batch_failed request_id=%s batch_name=%s error=%s",
                job.request_id,
                job.batch_name,
                exc,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.store.update_status(job.request_id, "failed", str(exc))
            logger.exception(
                "batch_unexpected_error request_id=%s batch_name=%s",
                job.request_id,
                job.batch_name,
            )


def batch_response_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "request_id": row["request_id"],
        "device_id": row["device_id"],
        "batch_name": row["batch_name"],
        "status": row["status"],
        "error": row["error"],
    }
