"""Validate editable semantics against the authorized source columns."""

import hashlib
import json

from pydantic import ValidationError

from apps.chat.task.cbi_semantic import TopicSemantics


class ConfigProblem(ValueError):
    def __init__(self, fields: dict[str, str]):
        super().__init__("invalid_theme_configuration")
        self.fields = fields


_NUMERIC = {
    "bigint",
    "smallint",
    "integer",
    "int",
    "int2",
    "int4",
    "int8",
    "numeric",
    "decimal",
    "real",
    "double precision",
    "float",
    "float4",
    "float8",
}


def numeric_type(type_: str) -> bool:
    return type_.lower().split("(")[0].strip() in _NUMERIC


def available_operators(
    field: str, type_: str, baseline: TopicSemantics | None = None
) -> list[str]:
    operators = ["count_distinct"]
    if numeric_type(type_):
        operators.extend(("sum", "sum_zero", "average"))
    if baseline and any(
        m.field == field and m.op == "repeat_count" for m in baseline.metrics.values()
    ):
        operators.append("repeat_count")
    return operators


def signature(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode()
    ).hexdigest()


def validate_content(
    raw: dict, baseline: TopicSemantics | None, types: dict[str, str]
) -> TopicSemantics:
    errors = dict.fromkeys(
        raw.keys() - TopicSemantics.model_fields.keys(), "field_not_editable"
    )
    defaults = {}
    if baseline:
        for key in ("guidance", "detail", "grain", "missing_facts"):
            if key in TopicSemantics.model_fields:
                expected = getattr(baseline, key)
                defaults[key] = expected
                if raw.get(key, expected) != expected:
                    errors[key] = "field_not_editable"
    try:
        config = TopicSemantics.model_validate({**defaults, **raw})
    except ValidationError as exc:
        for err in exc.errors(include_input=False, include_context=False):
            path = ".".join(map(str, err["loc"]))
            errors[path or "default_metric/start/end"] = err["type"]
        raise ConfigProblem(errors) from exc

    if not 0 < (config.end - config.start).days <= 731:
        errors["end"] = "invalid_default_period"
    if types.get(config.date_field, "").lower() != "date":
        errors["date_field"] = "approved_date_column_required"
    if len(config.metrics) > 64 or len(config.dimensions) > 32:
        errors["metrics/dimensions"] = "too_many_definitions"
    for key, metric in config.metrics.items():
        addr = f"metrics.{key}"
        if metric.field not in types:
            errors[f"{addr}.field"] = "field_not_available"
        elif metric.op not in available_operators(
            metric.field, types[metric.field], baseline
        ):
            errors[f"{addr}.op"] = "operator_incompatible"
        if (
            not metric.label.strip()
            or len(metric.label) > 80
            or len(metric.unit) > 32
            or len(metric.description) > 500
        ):
            errors[addr] = "invalid_metric_text"
    for key, dimension in config.dimensions.items():
        addr = f"dimensions.{key}"
        if dimension.field not in types:
            errors[f"{addr}.field"] = "field_not_available"
        elif dimension.numeric != numeric_type(types[dimension.field]):
            errors[f"{addr}.numeric"] = "dimension_type_mismatch"
        if dimension.identity and dimension.identity not in types:
            errors[f"{addr}.identity"] = "field_not_available"
        if not dimension.label.strip() or len(dimension.label) > 80:
            errors[f"{addr}.label"] = "invalid_dimension_text"
    for key in config.detail:
        if key not in types:
            errors[f"detail.{key}"] = "field_not_available"
    for index, key in enumerate(getattr(config, "grain", ())):
        if key not in types:
            errors[f"grain.{index}"] = "field_not_available"
    if config.quality:
        for index, key in enumerate(config.quality.sample_keys):
            if key not in types:
                errors[f"quality.sample_keys.{index}"] = "field_not_available"
        for index, rule in enumerate(config.quality.rules):
            for field_index, key in enumerate(rule.fields):
                path = f"quality.rules.{index}.fields.{field_index}"
                if key not in types:
                    errors[path] = "field_not_available"
                elif rule.type == "range" and not numeric_type(types[key]):
                    errors[path] = "operator_incompatible"
    if errors:
        raise ConfigProblem(errors)
    return config
