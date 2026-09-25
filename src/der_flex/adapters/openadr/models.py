from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ValuesMap(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: str = Field(min_length=1, max_length=128)
    values: list[float | int | str | bool]


class IntervalPeriod(BaseModel):
    model_config = ConfigDict(extra="forbid")
    start: datetime
    duration: str


class Interval(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: int
    interval_period: IntervalPeriod | None = Field(default=None, alias="intervalPeriod")
    payloads: list[ValuesMap]


class EventPayloadDescriptor(BaseModel):
    model_config = ConfigDict(extra="allow")
    object_type: Literal["EVENT_PAYLOAD_DESCRIPTOR"] = Field(alias="objectType")
    payload_type: str = Field(alias="payloadType")
    units: str | None = None


class OpenADREvent(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: str
    object_type: Literal["EVENT"] = Field(alias="objectType")
    program_id: str = Field(alias="programID")
    targets: list[str]
    interval_period: IntervalPeriod | None = Field(default=None, alias="intervalPeriod")
    intervals: list[Interval]
    payload_descriptors: list[EventPayloadDescriptor] = Field(
        default_factory=list, alias="payloadDescriptors"
    )

    @model_validator(mode="after")
    def exactly_one_zone_target(self) -> OpenADREvent:
        zones = [target for target in self.targets if target.startswith("ES-")]
        if len(zones) != 1:
            raise ValueError("event must target exactly one supported zone")
        return self

    @property
    def zone_id(self) -> str:
        return next(target for target in self.targets if target.startswith("ES-"))


class ReportPayloadDescriptor(BaseModel):
    object_type: Literal["REPORT_PAYLOAD_DESCRIPTOR"] = Field(
        default="REPORT_PAYLOAD_DESCRIPTOR", alias="objectType"
    )
    payload_type: str = Field(alias="payloadType")
    units: str | None = None
    confidence: int | None = Field(default=None, ge=0, le=100)


class ReportResource(BaseModel):
    resource_name: str = Field(alias="resourceName")
    interval_period: IntervalPeriod = Field(alias="intervalPeriod")
    intervals: list[Interval]


class OpenADRReportRequest(BaseModel):
    event_id: str = Field(alias="eventID")
    client_name: str = Field(alias="clientName")
    report_name: str = Field(alias="reportName")
    payload_descriptors: list[ReportPayloadDescriptor] = Field(alias="payloadDescriptors")
    resources: list[ReportResource]
