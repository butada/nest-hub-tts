from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

ExecutionMode = Literal["realtime", "batch"]


class SpeakRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=10000)
    device_id: str = Field(min_length=1, max_length=100)
    execution: ExecutionMode = "realtime"
    voice: str | None = Field(default=None, min_length=1, max_length=100)
    style: str | None = Field(default=None, max_length=500)
    title: str | None = Field(default=None, max_length=200)


class SpeakResponse(BaseModel):
    request_id: str
    device_id: str
    execution: ExecutionMode
    status: Literal["playing", "buffering", "batch_submitted"]
    batch_name: str | None = None


class BatchJobResponse(BaseModel):
    request_id: str
    device_id: str
    execution: Literal["batch"] = "batch"
    batch_name: str
    status: Literal["submitted", "running", "succeeded", "failed"]
    error: str | None = None


class DeviceResponse(BaseModel):
    id: str
    name: str
    host: str
    model: str | None
    configured: bool = True
