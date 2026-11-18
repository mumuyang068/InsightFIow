from datetime import date, datetime, time, timedelta, timezone
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

BindingId = Annotated[str, Field(min_length=1, max_length=48, pattern=r"^[a-z][a-z0-9_]*$")]
Identifier = Annotated[str, Field(pattern=r"^[a-z_][a-z0-9_]*\.[a-z_][a-z0-9_]*$")]
Store = Annotated[str, Field(min_length=1, max_length=48, pattern=r"^[A-Za-z0-9_-]+$")]
SHANGHAI = timezone(timedelta(hours=8))
FIXTURE_VERSION = "CBI-METHOD-FIXTURE-v1"


class EventConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)

    id: BindingId
    label: str = Field(min_length=1, max_length=100)
    datasource: int = Field(gt=0)
    view: Identifier
    store: Store
    timezone: Literal["Asia/Shanghai"] = "Asia/Shanghai"
    method: Literal["ordered_attempt_funnel"] = "ordered_attempt_funnel"
    method_version: Literal["1"] = "1"
    entry_start: str
    entry_end: str
    observation_asof: str
    window_hours: Literal[24, 48] = 24
    allowed_windows: list[Literal[24, 48]] = Field(default_factory=lambda: [24, 48], min_length=1, max_length=2)

    @model_validator(mode="after")
    def validate_window(self):
        dates = [date.fromisoformat(item) for item in (self.entry_start, self.entry_end, self.observation_asof)]
        if [item.isoformat() for item in dates] != [self.entry_start, self.entry_end, self.observation_asof]:
            raise ValueError("date_requires_yyyy_mm_dd")
        if not dates[0] < dates[1] <= dates[2] or (dates[2] - dates[0]).days > 366:
            raise ValueError("ordered_entry_and_observation_required")
        if self.window_hours not in self.allowed_windows or len(set(self.allowed_windows)) != len(self.allowed_windows):
            raise ValueError("default_window_not_allowed")
        return self


class BindingSnapshot(BaseModel):
    id: BindingId
    version: str
    datasource: int | None = None
    workspace: int | None = None
    label: str
    method: Literal["ordered_attempt_funnel", "fixed_window_repeat"]
    method_version: str = "1"
    store: Store
    timezone: Literal["Asia/Shanghai"] = "Asia/Shanghai"
    entry_start: str
    entry_end: str
    observation_asof: str
    window_hours: int
    allowed_windows: tuple[int, ...]
    observations: tuple[str, ...] = ()
    epoch: str | None = None
    adapter: Literal["fixture", "event_view"] = "fixture"
    view: Identifier | None = None
    reading: str
    test_only: bool = False
    channels: bool = False

    def model_context(self):
        return self.model_dump(mode="json", include={
            "id", "label", "method", "method_version", "version", "store", "timezone", "entry_start",
            "entry_end", "observation_asof", "window_hours", "allowed_windows", "observations", "reading",
        })


def configured(config, workspace, version):
    return BindingSnapshot(
        **config.model_dump(), workspace=workspace, version=f"v{version}", epoch=config.entry_start,
        observations=(config.observation_asof,), adapter="event_view", reading="registered_event_view",
    )


def time_at(binding, value):
    if binding.epoch is None:
        return int(value)
    return (date.fromisoformat(value) - date.fromisoformat(binding.epoch)).days * 86400


def absolute(value):
    return datetime.combine(date.fromisoformat(value), time.min, tzinfo=SHANGHAI)
