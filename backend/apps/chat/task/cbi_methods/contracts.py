from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .binding import BindingId, Store, time_at
from .compatibility import COMPATIBILITY
Channel = Literal["ad", "direct", "search", "email"]


class Change(BaseModel):
    model_config = ConfigDict(extra="forbid")

    window_hours: Literal[24, 48, 720] | None = None
    observation_asof: str | None = None
    entry_start: str | None = None
    entry_end: str | None = None
    cohort_channel: Channel | None = None
    repeat_channel: Channel | None = None
    eligibility: Literal["paid", "net"] | None = None


class Interpretation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["run", "inspect", "reread", "clarify", "unsupported"]
    method: Literal["ordered_attempt_funnel", "fixed_window_repeat"]
    changes: Change
    members: list[str] = Field(max_length=20)
    clarification: str | None = Field(default=None, max_length=800)


class MethodPlan(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    method: Literal["ordered_attempt_funnel", "fixed_window_repeat"]
    version: Literal["1"]
    binding: BindingId
    binding_version: str
    store: Store
    timezone: Literal["Asia/Shanghai"]
    unit: Literal["attempt", "customer"]
    entry_start: str
    entry_end: str
    window_hours: Literal[24, 48, 720]
    observation_asof: str
    population: Literal["mature_only"]
    steps: tuple[str, ...] = ()
    eligibility: Literal["paid", "net"] | None = None
    cohort_channel: Channel | None = None
    repeat_channel: Channel | None = None
    read_strategy: str
    boundary: Literal["left_closed_right_open_entry_inclusive_deadline"]


def binding_for(binding):
    return COMPATIBILITY[binding] if isinstance(binding, str) else binding


def observation_seconds(binding, value):
    return time_at(binding_for(binding), value)


def validate_plan(plan, binding=None):
    spec = binding_for(binding or plan["binding"])
    if (plan["method"], plan["store"], plan["binding"], plan["binding_version"], plan["version"], plan["timezone"]) != (
        spec.method, spec.store, spec.id, spec.version, spec.method_version, spec.timezone,
    ):
        raise ValueError("method_binding_mismatch")
    funnel = spec.method == "ordered_attempt_funnel"
    if plan["unit"] != ("attempt" if funnel else "customer"):
        raise ValueError("method_binding_mismatch")
    if plan["window_hours"] not in spec.allowed_windows:
        raise ValueError("window_outside_binding")
    if funnel:
        if (
            plan["observation_asof"] not in spec.observations
            or (plan["entry_start"], plan["entry_end"]) != (spec.entry_start, spec.entry_end)
            or any(plan[key] is not None for key in ("eligibility", "cohort_channel", "repeat_channel"))
        ):
            raise ValueError("funnel_binding_capability")
    else:
        values = [date.fromisoformat(plan[k]) for k in ("entry_start", "entry_end", "observation_asof")]
        if [val.isoformat() for val in values] != [plan[k] for k in ("entry_start", "entry_end", "observation_asof")]:
            raise ValueError("date_requires_yyyy_mm_dd")
        if not date.fromisoformat(spec.entry_start) <= values[0] < values[1] <= values[2] <= date.fromisoformat(spec.observation_asof):
            raise ValueError("repeat_binding_capability")
        if not plan["eligibility"]:
            raise ValueError("repeat_window_or_eligibility")
        if spec.channels and plan["observation_asof"] != spec.observation_asof:
            raise ValueError("r11_snapshot_fixed")
        if not spec.channels and (plan["cohort_channel"] or plan["repeat_channel"]):
            raise ValueError("channel_unavailable")


def build_plan(binding, change, previous, previous_id):
    spec = binding_for(binding)
    if previous and (not previous_id or previous["binding"] != spec.id or previous["binding_version"] != spec.version):
        raise ValueError("parent_binding_mismatch")
    funnel = spec.method == "ordered_attempt_funnel"
    starting = {
        "method": spec.method, "version": spec.method_version, "binding": spec.id, "binding_version": spec.version,
        "store": spec.store, "timezone": spec.timezone, "unit": "attempt" if funnel else "customer",
        "entry_start": spec.entry_start, "entry_end": spec.entry_end, "window_hours": spec.window_hours,
        "observation_asof": spec.observation_asof, "population": "mature_only",
        "steps": ("view", "cart", "pay") if funnel else (), "eligibility": None if funnel else "paid",
        "read_strategy": spec.reading, "boundary": "left_closed_right_open_entry_inclusive_deadline",
    }
    merged = {**(previous or starting), **change.model_dump(exclude_unset=True)}
    merged["steps"] = tuple(merged["steps"])
    result = MethodPlan.model_validate(merged).model_dump(mode="json")
    validate_plan(result, spec)
    return result


def exact_delta(result, reference):
    a = Decimal(result["numerator"]) / result["denominator"]
    b = Decimal(reference["numerator"]) / reference["denominator"]
    return float(round((a-b)*100, 6))
