from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class SpeakRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=10000)
    device_id: str = Field(min_length=1, max_length=100)
    voice: str | None = Field(default=None, min_length=1, max_length=100)
    style: str | None = Field(default=None, max_length=500)
    title: str | None = Field(default=None, max_length=200)


class SpeakResponse(BaseModel):
    request_id: str
    device_id: str
    status: Literal["playing", "buffering"]


class DeviceResponse(BaseModel):
    id: str
    name: str
    host: str
    model: str | None
    configured: bool = True
