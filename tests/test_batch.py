import sqlite3
from pathlib import Path

from nest_hub_tts.batch import BatchJob, BatchJobStore, job_from_row
from nest_hub_tts.config import DeviceSettings


def test_batch_job_store_persists_audio_settings_and_result(tmp_path: Path) -> None:
    store = BatchJobStore(tmp_path / "jobs.sqlite3")
    job = BatchJob(
        request_id="request-1",
        batch_name="batches/test-1",
        device_id="living-room",
        device=DeviceSettings(name="リビング", host="192.168.50.57"),
        title="テスト",
        cache_key={"text": "テスト", "voice": "Kore"},
        audio_profile="clear_speech",
        audio_speed=1.08,
    )

    store.create(job)
    stored = store.get("request-1")
    assert stored is not None
    assert job_from_row(stored) == job

    store.update_status("request-1", "succeeded", audio_id="a" * 64)
    result = store.get("request-1")
    assert result is not None
    assert result["status"] == "succeeded"
    assert result["audio_id"] == "a" * 64


def test_batch_job_store_migrates_previous_schema(tmp_path: Path) -> None:
    path = tmp_path / "jobs.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.execute(
            """
            CREATE TABLE batch_jobs (
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

    BatchJobStore(path)
    with sqlite3.connect(path) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(batch_jobs)")}
    assert {"cache_key", "audio_profile", "audio_speed", "audio_id"} <= columns
