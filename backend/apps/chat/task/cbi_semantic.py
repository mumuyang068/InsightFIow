"""Metadata-driven analysis for a single published, authorized relation."""

import json
import re
from datetime import date, timedelta
from decimal import Decimal, localcontext
from collections.abc import Sequence
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import (
    Date,
    Integer,
    String,
    cast,
    column,
    func,
    literal,
    select,
    table,
    union_all,
)
from sqlalchemy.dialects.postgresql import dialect

Identifier = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{0,62}$")]
FilterValue = str | int
FilterSelection = FilterValue | list[FilterValue] | None
DecompositionSourceLimit = 1000
AdditiveOperators = {"sum", "sum_zero"}
Reason = Literal["semantic_unsupported", "semantic_scope", "semantic_metric_clarify"]
BusinessFact = Literal["payment", "refund"]
ReferenceRole = Literal["requested", "excluded", "context", "uncertain"]

FACT_METRICS = {
    "payment": re.compile(
        r"支付\s*(?:转化|转换|轉化|轉換)率|净支付(?:金额|额)|實付金額|实付金额|"
        r"\b(?:payment\s+conversion\s+rate|net\s+paid\s+amount)\b", re.IGNORECASE
    ),
    "refund": re.compile(
        r"退款(?:\s*(?:率|比例|金额|金額|额|額|笔数|次数|事实))?|"
        r"\brefund(?:s|\s+(?:rate|ratio|amount|count))?\b", re.IGNORECASE
    ),
}
FACT_CLAUSE = re.compile(r"[，。！？；,!?;\n]|但是?|不过|而是")
FACT_OMISSION = r"(?:无需|无须|不用|不必|不需要|不要|不予|不)"
FACT_OPERATION = r"(?:查看|查询|计算|统计|分析|查|算|看|比较)"
FACT_EXCLUSION_BEFORE = re.compile(
    rf"(?:{FACT_OMISSION}\s*(?:{FACT_OPERATION})?|排除|忽略|略过|"
    r"\b(?:exclude|omit|ignore|without|do not calculate)\b)\s*(?:这次的|本期的)?\s*$",
    re.IGNORECASE,
)
FACT_EXCLUSION_AFTER = re.compile(
    rf"^\s*(?:先|暂时|这次|本次)?\s*(?:{FACT_OMISSION}\s*{FACT_OPERATION}|略过|排除|忽略)"
    r"(?:一下|了)?\s*$"
)
FACT_REQUEST_BEFORE = re.compile(rf"(?:{FACT_OPERATION}|告诉我|给出|只要|还要)(?:本月|当月|该期|订单)?\s*$")
FACT_REQUEST_AFTER = re.compile(r"^\s*(?:是|为|有)?\s*(?:多少|如何|怎样|怎么样|几笔|几次)\s*$")
QUOTED_TEXT = re.compile(r"“[^”]*”|‘[^’]*’|\"[^\"]*\"|「[^」]*」|『[^』]*』")
EXPLICIT_MONTHS = re.compile(
    r"^\s*(?:请\s*)?(?:比较|对比)\s*(?P<year>[1-9][0-9]{3})\s*年\s*"
    r"(?P<month>1[0-2]|0?[1-9])\s*月\s*(?:与|和|及)\s*"
    r"(?:(?P<other_year>[1-9][0-9]{3})\s*年\s*)?(?P<other_month>1[0-2]|0?[1-9])\s*月"
    r"(?!\s*(?:\d|上旬|中旬|下旬|至|到|初|底))"
)
MONTH_PHRASE = r"(?:[1-9][0-9]{3}\s*年\s*)?(?:1[0-2]|0?[1-9])\s*月"
MONTH_ONLY_REVISION = re.compile(
    rf"\s*(?:请\s*)?(?:只\s*)?(?:把\s*)?(?:本期\s*)?"
    rf"(?:换到|换成|改成|改为|改到)\s*{MONTH_PHRASE}\s*[。！!]?"
)
EXPLICIT_LIMIT = re.compile(r"前\s*[\d一二两三四五六七八九十百]+|\btop\s*\d+", re.IGNORECASE)
EXPLICIT_BASE = re.compile(
    rf"^\s*(?:请\s*)?(?:以|用)\s*(?P<base>{MONTH_PHRASE})\s*为\s*"
    r"(?:基准|基期|对照期|参照期|参考期)\s*$"
)
EXPLICIT_RELATIVE = re.compile(
    rf"^\s*(?:请\s*)?(?:(?:给出|计算|看)\s*)?(?P<main>{MONTH_PHRASE})\s*相对(?:于)?\s*"
    rf"(?P<base>{MONTH_PHRASE})(?=\s*(?:$|的(?:变化|增幅|降幅|差额)))"
)
PLAIN_COMPARISON_TAIL = re.compile(
    r"\s*(?:的\s*(?P<metric>[^，,。！？?！；;\n]+?))?\s*"
    r"(?:[，,]\s*(?:并\s*)?(?:给出|展示|列出)\s*(?:两期)?\s*"
    r"(?:金额|差额|变化率|增幅|降幅)(?:\s*(?:、|与|和|及)\s*(?:金额|差额|变化率|增幅|降幅))*)?"
    r"\s*[。！？!?]?\s*"
)


class Metric(BaseModel):
    op: Literal["count_distinct", "sum", "sum_zero", "average", "repeat_count"]
    field: Identifier
    label: str
    unit: str
    description: str
    precision: int | None = Field(default=None, ge=0, le=6)


class Dimension(BaseModel):
    field: Identifier
    label: str
    identity: Identifier | None = None
    numeric: bool = False
    ordered: bool = False
    values: dict[str, str] = Field(default_factory=dict)


class Decomposition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    metrics: dict[Identifier, bool] = Field(min_length=1, max_length=64)
    dimensions: dict[Identifier, list[Identifier]] = Field(min_length=1, max_length=32)
    tolerance: Decimal = Field(default=Decimal("0"), ge=0, le=Decimal("0.001"))


class QualityRule(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    id: Identifier
    type: Literal["unique", "not_null", "range", "freshness"]
    fields: list[Identifier] = Field(min_length=1, max_length=4)
    description: str = Field(min_length=1, max_length=300)
    suggestion: str = Field(min_length=1, max_length=300)
    severity: Literal["warning", "failure"]
    metrics: list[Identifier] = Field(min_length=1, max_length=64)
    dimensions: list[Identifier] = Field(default_factory=list, max_length=32)
    minimum: Decimal | None = Field(default=None, allow_inf_nan=False)
    maximum: Decimal | None = Field(default=None, allow_inf_nan=False)
    max_age_days: int | None = Field(default=None, ge=0, le=3650)

    @model_validator(mode="after")
    def validate_parameters(self):
        if any(
            len(set(items)) != len(items)
            for items in (self.fields, self.metrics, self.dimensions)
        ):
            raise ValueError("Repeated quality rule reference")
        if self.type != "unique" and len(self.fields) != 1:
            raise ValueError("This quality rule requires one field")
        lower, upper = self.minimum, self.maximum
        if self.type == "range":
            if lower is None and upper is None:
                raise ValueError("Range requires a bound")
            if lower is not None and upper is not None and lower > upper:
                raise ValueError("Invalid range bounds")
        elif lower is not None or upper is not None:
            raise ValueError("Bounds only apply to a range rule")
        if (self.type == "freshness") != (self.max_age_days is not None):
            raise ValueError("Maximum age is required only for freshness")
        return self


class QualityPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_mode: Literal["static", "rolling"]
    sample_keys: list[Identifier] = Field(default_factory=list, max_length=4)
    rules: list[QualityRule] = Field(max_length=16)

    @model_validator(mode="after")
    def validate_rules(self):
        if len(self.sample_keys) != len(set(self.sample_keys)):
            raise ValueError("Repeated sample key")
        if len(self.rules) != len({rule.id for rule in self.rules}):
            raise ValueError("Repeated quality rule ID")
        if self.source_mode != "rolling" and any(
            rule.type == "freshness" for rule in self.rules
        ):
            raise ValueError("Freshness requires a rolling source")
        return self


class TopicSemantics(BaseModel):
    date_field: Identifier
    start: date
    end: date
    default_metric: Identifier
    metrics: dict[Identifier, Metric]
    dimensions: dict[Identifier, Dimension]
    detail: dict[Identifier, str]
    guidance: list[str] = Field(default_factory=list, max_length=16)
    description: str = Field(default="", max_length=600)
    decomposition: Decomposition | None = None
    quality: QualityPolicy | None = None
    grain: list[Identifier] = Field(default_factory=list, max_length=4)
    missing_facts: list[BusinessFact] = Field(default_factory=list, max_length=2)

    @model_validator(mode="after")
    def validate_scope(self):
        if (
            self.default_metric not in self.metrics
            or self.start >= self.end
            or not self.detail
        ):
            raise ValueError("Invalid topic definition")
        if len(self.grain) != len(set(self.grain)) or len(self.missing_facts) != len(set(self.missing_facts)):
            raise ValueError("Repeated topic grain or missing fact")
        if config := self.decomposition:
            for key in config.metrics:
                if (
                    key not in self.metrics
                    or self.metrics[key].op not in AdditiveOperators
                ):
                    raise ValueError(
                        "Decomposition requires registered additive metrics"
                    )
            for key, children in config.dimensions.items():
                if (
                    key not in self.dimensions
                    or key in {"none", "day"}
                    or key in children
                    or len(children) != len(set(children))
                    or not set(children).issubset(config.dimensions)
                ):
                    raise ValueError("Invalid registered decomposition dimensions")
                dimension = self.dimensions[key]
                if dimension.identity and not any(
                    item.field == dimension.identity and not item.identity
                    for item in self.dimensions.values()
                ):
                    raise ValueError(
                        "Decomposition identity requires a registered filter dimension"
                    )
        if self.quality:
            for rule in self.quality.rules:
                if (
                    not set(rule.metrics) <= self.metrics.keys()
                    or not set(rule.dimensions) <= self.dimensions.keys()
                ):
                    raise ValueError(f"Unregistered quality impact: {rule.id}")
                if rule.type == "freshness" and rule.fields != [self.date_field]:
                    raise ValueError(f"Freshness requires the topic date: {rule.id}")
        return self

    @property
    def planning_fields(self):
        return {
            self.date_field,
            *(m.field for m in self.metrics.values()),
            *(d.field for d in self.dimensions.values()),
            *(d.identity for d in self.dimensions.values() if d.identity),
        }

    @property
    def fields(self):
        return self.planning_fields | set(self.detail) | set(self.grain)


class MetricReference(BaseModel):
    model_config = ConfigDict(extra="forbid")

    metric_id: Identifier
    role: ReferenceRole
    quote: str = Field(min_length=1, max_length=400)


class FactReference(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fact: BusinessFact
    role: ReferenceRole
    quote: str = Field(min_length=1, max_length=400)


class SemanticIntent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation: Literal["new", "revise", "unsupported"]
    start: date | None = None
    end: date | None = None
    comparison_action: Literal["keep", "replace", "clear"] | None = None
    compare_start: date | None = None
    compare_end: date | None = None
    comparison_direction_uncertain: bool = False
    group: str | None = None
    metrics: list[str] | None = Field(None, max_length=5)
    filters: dict[str, FilterSelection] | None = None
    top: int | None = Field(None, ge=1, le=100)
    detail: bool | None = None
    totals: bool = False
    investigate: bool = False
    investigation_members: bool = False
    requires_refund_evidence: bool = False
    overall_metrics: list[str] | None = Field(None, max_length=5)
    metric_references: list[MetricReference] = Field(
        default_factory=list, max_length=16
    )
    fact_references: list[FactReference] = Field(default_factory=list, max_length=16)


class PlanRejected(ValueError):
    def __init__(self, reason: Reason):
        self.reason = reason
        super().__init__(reason)


def requested_missing_fact(
    facts: list[BusinessFact], question: str, references: Sequence[FactReference] = (),
    *, requires_refund_evidence: bool = False,
) -> BusinessFact | None:
    # Bind model roles to individual occurrences in the original question.
    # A bare token is not enough evidence to discard a possibly requested fact.
    grounded = []
    for ref in references:
        start = question.find(ref.quote)
        if ref.fact in facts and start >= 0 and start == question.rfind(ref.quote):
            grounded.append((ref, start, start + len(ref.quote)))
    quoted = list(QUOTED_TEXT.finditer(question))
    not_required = set()
    uncertain = False
    offset = 0
    for delimiter in (*FACT_CLAUSE.finditer(question), None):
        end = delimiter.start() if delimiter else len(question)
        clause = question[offset:end]
        for fact in facts:
            for match in FACT_METRICS[fact].finditer(clause):
                start, stop = offset + match.start(), offset + match.end()
                before, after = clause[:match.start()], clause[match.end():]
                if FACT_EXCLUSION_BEFORE.search(before) or FACT_EXCLUSION_AFTER.match(after):
                    not_required.add(fact)
                    continue
                is_quoted = any(item.start() < start and stop < item.end() for item in quoted)
                if not is_quoted and (
                    FACT_REQUEST_BEFORE.search(before) or FACT_REQUEST_AFTER.match(after)
                    or fact == "refund" and re.search(r"(?:因为|由于)\s*$", before)
                    or fact == "refund" and re.match(r"\s*(?:导致|造成|引起)", after)
                ):
                    return fact
                roles = {
                    ref.role for ref, left, right in grounded
                    if ref.fact == fact and left <= start and stop <= right
                    and (ref.role == "requested" or left < start or stop < right)
                }
                if roles == {"requested"}:
                    return fact
                if not roles or roles - {"excluded", "context"}:
                    uncertain = True
                else:
                    not_required.add(fact)
        if delimiter:
            offset = delimiter.end()
    if uncertain or requires_refund_evidence and "refund" in facts and "refund" not in not_required:
        raise PlanRejected("semantic_metric_clarify")
    return None


def _whole_month(first, end):
    return (first is not None and end is not None and first.day == end.day == 1
            and (end.year * 12 + end.month) - (first.year * 12 + first.month) == 1)


def _named_month(value, options):
    year, _, month = value.removesuffix("月").partition("年")
    named = [day for day in options if day.month == int(month or year)
             and (not month or day.year == int(year))]
    if len(named) != 1:
        raise PlanRejected("semantic_scope")
    return named[0]


def resolve_comparison_direction(question: str, intent: SemanticIntent, metric_labels=()):
    if intent.comparison_direction_uncertain:
        raise PlanRejected("semantic_scope")
    if (intent.operation != "new" or not _whole_month(intent.start, intent.end)
            or not _whole_month(intent.compare_start, intent.compare_end)):
        return intent, "model"
    windows = {intent.start: intent.end, intent.compare_start: intent.compare_end}
    if len(windows) != 2:
        return intent, "model"
    explicit_bases = set()
    for clause in FACT_CLAUSE.split(question):
        if match := EXPLICIT_BASE.match(clause):
            explicit_bases.add(_named_month(match["base"], windows))
        if match := EXPLICIT_RELATIVE.match(clause):
            current = _named_month(match["main"], windows)
            baseline = _named_month(match["base"], windows)
            if current == baseline:
                raise PlanRejected("semantic_scope")
            explicit_bases.add(baseline)
    if len(explicit_bases) > 1:
        raise PlanRejected("semantic_scope")
    if explicit_bases:
        baseline, = explicit_bases
        reason = "explicit_month_roles"
    else:
        pair = EXPLICIT_MONTHS.match(question)
        tail = PLAIN_COMPARISON_TAIL.fullmatch(question[pair.end():]) if pair else None
        if not tail or (tail["metric"] and tail["metric"].strip() not in metric_labels):
            return intent, "model"
        year = int(pair["year"])
        current = date(year, int(pair["month"]), 1)
        baseline = date(int(pair["other_year"] or year), int(pair["other_month"]), 1)
        if {current, baseline} != windows.keys() or current == baseline:
            raise PlanRejected("semantic_scope")
        reason = "unqualified_month_pair"
    current, = windows.keys() - {baseline}
    if (current, baseline) == (intent.start, intent.compare_start):
        return intent, reason
    return intent.model_copy(update={
        "start": current, "end": windows[current],
        "compare_start": baseline, "compare_end": windows[baseline],
    }), reason


def instructions(topic: TopicSemantics, prior, unavailable_metrics=None, *, allowed_fields=None):
    fields = {
        "metrics": {
            key: {"label": m.label, "meaning": m.description}
            for key, m in topic.metrics.items()
        },
        "default_metric": topic.default_metric,
        "dimensions": {
            key: {"label": d.label, "values": d.values}
            for key, d in topic.dimensions.items()
        },
        "detail": [label for field, label in topic.detail.items()
                   if allowed_fields is None or field in allowed_fields],
        "topic_guidance": topic.guidance,
        "default_period": {"start": str(topic.start), "end": str(topic.end)},
        "prior": prior,
    }
    if topic.decomposition:
        fields["registered_decomposition"] = topic.decomposition.model_dump(mode="json")
    if topic.missing_facts:
        fields["unavailable_business_facts"] = topic.missing_facts
    if unavailable_metrics:
        fields["unavailable_metrics_for_recognition_only"] = unavailable_metrics
    return (
        "Return exactly one JSON object for the approved data topic. Do not emit SQL or answers. "
        "operation: new, revise (follow-up using prior), or unsupported. "
        "Keys: start/end (ISO dates, inclusive start and EXCLUSIVE end), compare_start/compare_end "
        "(new comparison period only), comparison_action (keep, replace or clear), "
        "group (day, none or a registered dimension identifier), "
        "metrics (registered identifiers), filters (registered dimensions to exact source values, arrays mean any of the listed values, null clears a filter), "
        "top (explicit requested limit, null for all rows up to the execution cap), detail (true for original records, false for an explicit change to an aggregate), overall_metrics (identifiers requested for the whole scope along with a breakdown), totals (show overall for the same grouped metrics). "
        "On revise, omit top, detail and totals unless the user explicitly changes them. Never fill defaults for these fields on a date-only or filter-only revision. "
        "For an explicit overall total, set group=none and detail=false; for a current-period-only total also set comparison_action=clear. "
        "If asked to look only at the groups in the previous ranked result, use revise and omit that group's filter. The server will resolve the actual members. "
        "Read the complete question before assigning periods. Explicit relative direction or a named base takes precedence. "
        "For example, 'compare August and July, using August as the base' makes July the main window. "
        "Use first-mentioned period as main only when a simple two-period request leaves both roles unspecified. "
        "If the stated roles conflict or cannot be resolved, set comparison_direction_uncertain=true. "
        "Preserve explicitly requested date limits. Never merge the two periods. The periods cannot overlap. "
        "For a month, end is the first day of the next month; for a year, the first day of the next year. "
        "No date: omit dates to inherit on revise or use the topic defaults on new. An omitted year can be inferred from prior dates. "
        "A change of dimension keeps the prior main period and filters. Never copy filters into a new analysis. "
        "In a revision, explicitly asking for the full scope removes prior filters by setting each prior filter to null. "
        "Group by day for daily trend; group by none for an overall metric. Use revise for 'this month' or an explicitly selected result. "
        "On revise, keep the prior comparison unless the user changes it. For a filter-only or metric-only revision, "
        "use comparison_action=keep with neither comparison date. For a new comparison window, use replace with both dates. "
        "For cancel comparison or explicitly only the main period (including only the main-period daily trend), use clear with neither date. "
        "If the user wants comparison and grouping together, keep the comparison and choose exactly one registered additive metric and decomposition dimension. "
        "For any follow-up with comparison and grouping, explicitly return investigate as a JSON boolean. "
        "Set investigate=true when the user asks why the selected result changed, asks to investigate that change, "
        "or explicitly asks for a grouped comparison followed by a check of the leading subgroups. "
        "For example, first compare by region, then examine the main stores, is a multi-step investigation: "
        "use the first requested dimension and investigate=true even if only one further breakdown is named. "
        "Set investigate=false for a single requested partition, including a single follow-up inside a selected group, "
        "and honor an explicit instruction to stop at the requested dimension or omit further checks. "
        "For a follow-up asking which items changed inside a named group in prior.known_group_members, "
        "use revise with its exact key in filters at filter_key, select the requested registered child, "
        "and inherit both periods and the metric. A question asking which products or other items rose or fell "
        "inside one named group asks only for that one breakdown, so set investigate=false unless the current "
        "question explicitly asks to check a cause or to continue beyond that breakdown. "
        "Do not inherit investigate=true from earlier answers. Do not reject a demonstrated valid child relationship. "
        "Keep comparison dates null when comparison_action is keep. "
        "A request to look for offsetting internal changes asks for a registered partition even when net change is zero. "
        "For investigation use operation revise on the selected comparison; retain both date windows and unchanged filters. "
        "Choose a requested registered dimension or omit group to let the server choose from the approved relations. "
        "Only include an explicit registered metric if it is requested or uniquely applicable. "
        "Set investigation_members=true only if the investigation explicitly asks to inspect the relevant records. "
        "Set requires_refund_evidence=true and investigate=true if the user asks whether refunds caused the change. "
        "Never infer actual payment or refund facts from billing amounts. Ordinary unregistered facts remain unsupported. "
        "For unavailable_business_facts mentioned in this question, return fact_references with fact, role and a verbatim quote including the governing words. "
        "Roles: requested for a current calculation, excluded for negation before or after the fact, context for quoted or prior discussion, uncertain when unclear. "
        "A current request requiring a missing fact is unsupported. An excluded or background fact leaves an available requested calculation valid. "
        "On new, no comparison is inherited. When grouping, emit only the metrics requested for the breakdown. "
        "Use the registered source values for named categories and event names. Put the requested ranking metric first. "
        "Analyze the meaning of each reference to a metric. Return metric_references with metric_id, role, and a verbatim quote from this question. "
        "Use only metrics explicitly requested in the current question, preserving explicit multi-metric requests. Quote the words expressing each requested metric, including synonyms. "
        "For a revise operation that changes only dates, grouping or filters, omit metric choices and inherit the prior metric projection. "
        "For a new calculation with no metric requested, use the published default_metric. Do not expand unspecified metrics to the available metric list. "
        "Dates, generic change phrases and comparisons do not name a metric and must not appear as requested metric references. "
        "Use requested for calculations the user wants, excluded for what the user says to omit, context for past discussion or background that they do not want calculated, and uncertain when genuinely ambiguous. "
        "Only use provided metric identifiers in metric_references. Do not invent IDs for unsupported topics or facts. "
        "Include each occurrence of an unavailable metric, including synonyms, uppercase IDs, exclusions, and background. A quote can include the surrounding clause but must be an exact substring. "
        "Only metrics registered in this version can enter metrics or overall_metrics. If any requested metric is in unavailable_metrics_for_recognition_only, return operation unsupported and a requested reference to it, even when other metrics are available. Never substitute a similar metric. "
        "A clearly excluded or background unavailable metric must not stop calculation of available requested metrics. Do not require references to inherited metrics when the user changes only dates or filters. "
        "When a question asks for a distribution and an overall average, put the count in metrics and the registered average in overall_metrics. "
        "Use detail=true for a record list, not for a ranked aggregate. "
        "Use unsupported when an essential fact or relationship is unregistered. Respect the topic guidance. "
        "Do not silently reinterpret unsupported metrics as available proxies. "
        + json.dumps(fields, ensure_ascii=False)
        + "\nJSON schema: "
        + json.dumps(SemanticIntent.model_json_schema(), ensure_ascii=False)
    )


def requests_all_groups(question, topic, group):
    dimension = topic.dimensions.get(group)
    if dimension is None or EXPLICIT_LIMIT.search(question):
        return False
    label = re.escape(dimension.label)
    return bool(re.search(rf"(?:看|查看|列出|展示)\s*(?:全部|所有|全量)\s*{label}", question))


def requested_prior_members(question, topic, prior):
    plan = prior["semantic_plan"]
    dimension = topic.dimensions.get(plan.get("group"))
    if not dimension:
        return None
    count = re.search(
        rf"(?:只|仅)(?:看|查)\s*(?:原来|刚才|上次)(?:的|这|那)?\s*"
        rf"(?P<count>10|[1-9一二两三四五六七八九十])\s*个\s*{re.escape(dimension.label)}",
        question,
    )
    if count is None:
        return None
    raw_count = count["count"]
    number = (int(raw_count) if raw_count.isascii()
              else "一二三四五六七八九十".index(raw_count.replace("两", "二")) + 1)
    rows = prior.get("presentation", {}).get("table", {}).get("rows", ())
    key = "dimension_id" if dimension.identity else "dimension"
    values = [row.get(key) for row in rows]
    filter_key = plan["group"]
    if dimension.identity:
        filter_key = next((key for key, candidate in topic.dimensions.items()
                           if candidate.field == dimension.identity and not candidate.identity), None)
    if not filter_key or len(values) != number or any(value is None for value in values):
        raise PlanRejected("semantic_scope")
    if len(set(values)) != number:
        raise PlanRejected("semantic_scope")
    return {filter_key: values}


def resolve(topic: TopicSemantics, intent: SemanticIntent, prior=None):
    if intent.operation == "unsupported":
        raise PlanRejected("semantic_unsupported")
    old = prior if prior and intent.operation == "revise" else {}
    has_comparison_dates = intent.compare_start is not None
    if (
        bool(intent.start) != bool(intent.end)
        or has_comparison_dates != (intent.compare_end is not None)
        or (intent.comparison_action in {"keep", "clear"} and has_comparison_dates)
        or (intent.comparison_action == "replace" and not has_comparison_dates)
    ):
        raise PlanRejected("semantic_scope")

    compare_start, compare_end = intent.compare_start, intent.compare_end
    if not has_comparison_dates and intent.comparison_action != "clear":
        if old.get("compare_start") and old.get("compare_end"):
            compare_start = date.fromisoformat(old["compare_start"])
            compare_end = date.fromisoformat(old["compare_end"])
    start = intent.start or date.fromisoformat(old.get("start", str(topic.start)))
    end = intent.end or date.fromisoformat(old.get("end", str(topic.end)))
    group = intent.group or old.get("group", "none")
    detail = old.get("detail", False) if intent.detail is None else intent.detail
    if old and "detail" not in old and intent.detail is None:
        raise PlanRejected("semantic_scope")
    same_shape = bool(old and group == old.get("group") and detail == old.get("detail"))
    if "top" in intent.model_fields_set:
        top = intent.top if intent.top is not None else 100
    elif same_shape and (detail or group != "none"):
        top = old.get("top")
        if type(top) is not int or not 1 <= top <= 100:
            raise PlanRejected("semantic_scope")
    else:
        top = 100 if group == "day" else 50 if detail else 25
    metrics = list(
        dict.fromkeys(intent.metrics or old.get("metrics") or [topic.default_metric])
    )
    overall = list(dict.fromkeys(
        intent.overall_metrics if intent.overall_metrics is not None
        else metrics if intent.totals
        else old.get("overall_metrics", ()) if same_shape and "totals" not in intent.model_fields_set
        else ()
    ))
    filters = dict(old.get("filters", {}))
    if len(intent.filters or {}) > 8:
        raise PlanRejected("semantic_scope")
    for key, value in (intent.filters or {}).items():
        if value is None:
            filters.pop(key, None)
        else:
            filters[key] = value
    if (
        group not in {"none", "day", *topic.dimensions}
        or any(k not in topic.metrics for k in [*metrics, *overall])
        or any(k not in topic.dimensions for k in filters)
    ):
        raise PlanRejected("semantic_unsupported")
    if not 0 < (end - start).days <= (92 if group == "day" else 731):
        raise PlanRejected("semantic_scope")
    for key, value in filters.items():
        dimension = topic.dimensions[key]
        values = value if isinstance(value, list) else [value]
        if not 1 <= len(values) <= 10:
            raise PlanRejected("semantic_scope")
        normalized = []
        for item in values:
            if dimension.numeric:
                if (
                    isinstance(item, str)
                    and len(item) <= 15
                    and item.isascii()
                    and item.isdecimal()
                ):
                    item = int(item)
                elif not isinstance(item, int) or isinstance(item, bool):
                    raise PlanRejected("semantic_scope")
            elif not isinstance(item, str) or not item.strip() or len(item) > 256:
                raise PlanRejected("semantic_scope")
            elif dimension.values:
                aliases = {label: raw for raw, label in dimension.values.items()}
                item = aliases.get(item, item)
                if item not in dimension.values:
                    raise PlanRejected("semantic_scope")
            normalized.append(item)
        filters[key] = (
            list(dict.fromkeys(normalized))
            if isinstance(value, list)
            else normalized[0]
        )
    if compare_start:
        if (
            detail
            or not 0 < (compare_end - compare_start).days <= 731
            or (start < compare_end and compare_start < end)
        ):
            raise PlanRejected("semantic_scope")
        if group != "none":
            require_decomposition(topic, group, metrics, overall)
    if group != "none" and any(topic.metrics[k].op == "repeat_count" for k in metrics):
        raise PlanRejected("semantic_unsupported")
    return {
        "start": str(start),
        "end": str(end),
        "compare_start": str(compare_start) if compare_start else None,
        "compare_end": str(compare_end) if compare_end else None,
        "group": group,
        "metrics": metrics,
        "filters": filters,
        "top": top,
        "detail": detail,
        "overall_metrics": overall,
    }


def require_decomposition(topic: TopicSemantics, group, metrics, overall=()):
    config = topic.decomposition
    if (
        not config
        or group not in config.dimensions
        or len(metrics) != 1
        or metrics[0] not in config.metrics
        or topic.metrics[metrics[0]].op not in AdditiveOperators
        or overall
    ):
        raise PlanRejected("semantic_scope")
    return config


def decomposition_filter(topic: TopicSemantics, group: str, key):
    """Find the registered selection for a stable key, if the group is identifiable."""
    if key is None:
        return None
    dimension = topic.dimensions[group]
    if not dimension.identity:
        return {group: key}
    for name, candidate in topic.dimensions.items():
        if candidate.field == dimension.identity and not candidate.identity:
            return {name: key}
    return None


def _comparison_groups(base, reference, dimension, metric):
    def aggregate(period, relation, *, total):
        key = relation.c[dimension.identity or dimension.field]
        label = relation.c[dimension.field]
        field = relation.c[metric.field]
        shape = select(
            literal(period).label("period"),
            literal("total" if total else "group").label("cell"),
            (cast(None, key.type) if total else key).label("dimension_key"),
            (cast(None, String) if total else func.min(cast(label, String))).label(
                "dimension"
            ),
            cast(func.sum(field), String).label("amount"),
            func.count().label("observations"),
            func.count(field).label("known"),
        ).select_from(relation)
        if total:
            return shape
        limited = (
            shape.group_by(key)
            .order_by(key.nulls_last())
            .limit(DecompositionSourceLimit + 1)
        )
        return select(*limited.subquery().c)

    periods = {"current": base, "baseline": reference}
    return union_all(
        *(
            aggregate(period, relation, total=True)
            for period, relation in periods.items()
        ),
        *(
            aggregate(period, relation, total=False)
            for period, relation in periods.items()
        ),
    )


def build_query(topic: TopicSemantics, view: str, plan):
    schema, name = view.split(".")
    group = (
        None if plan["group"] in {"none", "day"} else topic.dimensions[plan["group"]]
    )
    base_fields = {
        topic.date_field,
        *(topic.dimensions[k].field for k in plan["filters"]),
    }
    if plan["detail"]:
        base_fields.update(topic.detail)
        order_fields = topic.grain or [next(iter(topic.detail))]
        base_fields.update(order_fields)
    else:
        base_fields.update(
            topic.metrics[k].field
            for k in (*plan["metrics"], *plan.get("overall_metrics", []))
        )
        if group:
            base_fields.update(
                {group.field, *([group.identity] if group.identity else [])}
            )
    numeric = {d.field for d in topic.dimensions.values() if d.numeric}
    fields = [
        column(
            k, Date if k == topic.date_field else Integer if k in numeric else String
        )
        for k in sorted(base_fields)
    ]
    source = table(name, *fields, schema=schema)

    def filtered(start, end):
        predicates = [
            source.c[topic.date_field] >= date.fromisoformat(start),
            source.c[topic.date_field] < date.fromisoformat(end),
            *(
                (
                    source.c[topic.dimensions[k].field].in_(v)
                    if isinstance(v, list)
                    else source.c[topic.dimensions[k].field] == v
                )
                for k, v in sorted(plan["filters"].items())
            ),
        ]
        return select(*source.c).where(*predicates).cte()

    base = filtered(plan["start"], plan["end"])

    def metrics(relation, keys=None):
        exprs = []
        for k in plan["metrics"] if keys is None else keys:
            definition = topic.metrics[k]
            field = relation.c[definition.field]
            if definition.op == "count_distinct":
                value = func.count(field.distinct())
            elif definition.op == "sum":
                value = func.sum(field)
            elif definition.op == "sum_zero":
                value = func.coalesce(func.sum(field), 0)
            elif definition.op == "average":
                value = func.avg(field)
            else:
                qualified = (
                    select(field)
                    .correlate(None)
                    .where(field.is_not(None))
                    .group_by(field)
                    .having(func.count() >= 2)
                )
                value = (
                    select(func.count())
                    .select_from(qualified.subquery())
                    .scalar_subquery()
                )
            exprs.append(value.label(k))
        return exprs

    def aggregate_row(relation, keys, *extra):
        statement = select(*extra, *metrics(relation, keys))
        # repeat_count is already a scalar aggregate over the full filtered set.
        # An outer FROM would emit one copy per input row (or none for an empty set).
        if any(topic.metrics[key].op != "repeat_count" for key in keys):
            return statement.select_from(relation)
        return statement

    baseline = plan["compare_start"]
    if plan["detail"]:
        query = (
            select(*(base.c[k] for k in topic.detail))
            .order_by(base.c[topic.date_field].desc(), *(base.c[key] for key in order_fields))
            .limit(plan["top"] + 1)
        )
    elif baseline:
        reference = filtered(baseline, plan["compare_end"])
        if group:
            require_decomposition(
                topic, plan["group"], plan["metrics"], plan.get("overall_metrics")
            )
            query = _comparison_groups(
                base, reference, group, topic.metrics[plan["metrics"][0]]
            )
        else:
            current_set = aggregate_row(
                base, plan["metrics"], literal("current").label("period")
            )
            previous_set = aggregate_row(
                reference, plan["metrics"], literal("baseline").label("period")
            )
            summary = current_set.union_all(previous_set)
            if topic.decomposition and (exact_keys := [
                key for key in plan["metrics"] if key in topic.decomposition.metrics
            ]):
                totals = summary.subquery()
                query = select(*totals.c, *(
                    cast(totals.c[key], String).label("_comparison_exact_" + key)
                    for key in exact_keys
                ))
            else:
                query = summary
    elif group or plan["group"] == "day":
        dimension = base.c[group.field if group else topic.date_field]
        identity = base.c[group.identity] if group and group.identity else None
        dimensions = [dimension, *([identity] if identity is not None else [])]
        shape = select(
            dimension.label("dimension"),
            *([identity.label("dimension_id")] if identity is not None else []),
            *metrics(base),
        ).group_by(*dimensions)
        ordered = (
            [dimension]
            if not group or group.ordered
            else [metrics(base)[0].desc().nulls_last(), *dimensions]
        )
        shape = shape.order_by(*ordered).limit(plan["top"] + 1).subquery()
        query = select(*shape.c)
        if overall := plan.get("overall_metrics"):
            totals = aggregate_row(base, overall).subquery()
            query = select(
                *shape.c, *(totals.c[k].label("all_" + k) for k in overall)
            ).join(totals, literal(True))
        query = query.order_by(
            shape.c.dimension
            if not group or group.ordered
            else shape.c[plan["metrics"][0]].desc().nulls_last(),
            shape.c.dimension,
            *([shape.c.dimension_id] if "dimension_id" in shape.c else []),
        )
    else:
        query = aggregate_row(base, plan["metrics"])
    sql = str(
        query.compile(
            dialect=dialect(paramstyle="named"), compile_kwargs={"literal_binds": True}
        )
    )
    return query, sql, base_fields


def _decomposition_observation(cell, *, empty_zero, complete=True):
    if cell is None or cell["observations"] == 0:
        return (Decimal(0), "absent") if empty_zero and complete else (None, "unknown")
    if cell["known"] != cell["observations"] or cell["amount"] is None:
        return None, "unknown"
    value = Decimal(str(cell["amount"]))
    return (value, "observed") if value.is_finite() else (None, "unknown")


def _decimal_text(value):
    if value is None:
        return None
    value = format(value, "f") if value else "0"
    return value.rstrip("0").rstrip(".") if "." in value else value


def _decomposition_data(topic, plan, rows):
    config = require_decomposition(
        topic, plan["group"], plan["metrics"], plan.get("overall_metrics")
    )
    metric = plan["metrics"][0]
    totals = {}
    grouped = {"current": {}, "baseline": {}}
    for row in rows:
        side = row["period"]
        if side not in grouped:
            raise ValueError("Unexpected decomposition period")
        if row["cell"] == "total" and side not in totals:
            totals[side] = row
        elif row["cell"] == "group" and row["dimension_key"] not in grouped[side]:
            grouped[side][row["dimension_key"]] = row
        else:
            raise ValueError("Unexpected decomposition group or summary")
    if set(totals) != set(grouped):
        raise ValueError("Expected one decomposition summary per period")

    coverage = {
        side: len(side_groups) <= DecompositionSourceLimit
        for side, side_groups in grouped.items()
    }
    source_complete = all(coverage.values())
    empty_zero = config.metrics[metric]
    total = {
        side: _decomposition_observation(cell, empty_zero=empty_zero)[0]
        for side, cell in totals.items()
    }
    breakdown = []
    current_groups, baseline_groups = grouped["current"], grouped["baseline"]
    for key in current_groups.keys() | baseline_groups.keys():
        current_cell, baseline_cell = current_groups.get(key), baseline_groups.get(key)
        current, current_status = _decomposition_observation(
            current_cell, empty_zero=empty_zero, complete=coverage["current"]
        )
        baseline, baseline_status = _decomposition_observation(
            baseline_cell, empty_zero=empty_zero, complete=coverage["baseline"]
        )
        label = (
            (current_cell or baseline_cell)["dimension"] if key is not None else None
        )
        if label is None and key is not None:
            label = str(key)
        breakdown.append(
            {
                "key": key,
                "label": label,
                "current": current,
                "baseline": baseline,
                "delta": current - baseline
                if current is not None and baseline is not None
                else None,
                "current_status": current_status,
                "baseline_status": baseline_status,
            }
        )
    breakdown.sort(
        key=lambda row: (
            row["delta"] is None,
            -abs(row["delta"] or 0),
            row["key"] is None,
            row["key"],
        )
    )
    reason = None
    if not source_complete:
        reason = "source_truncated"
    elif any(total[side] is None for side in grouped) or any(
        row["delta"] is None for row in breakdown
    ):
        reason = "missing_values"
    elif (
        any(
            sum(cell["observations"] for cell in grouped[side].values())
            != totals[side]["observations"]
            or abs(sum(row[side] for row in breakdown) - total[side]) > config.tolerance
            for side in grouped
        )
        or abs(
            sum(row["delta"] for row in breakdown)
            - (total["current"] - total["baseline"])
        )
        > config.tolerance
    ):
        reason = "reconciliation_failed"

    unseen = breakdown[plan["top"] :]
    total["delta"] = (
        total["current"] - total["baseline"]
        if total["current"] is not None and total["baseline"] is not None
        else None
    )
    total["rate"] = (
        round(total["delta"] / total["baseline"] * 100, 6)
        if total["delta"] is not None and total["baseline"] > 0
        else None
    )
    other = (
        {
            field: _decimal_text(sum(row[field] for row in unseen))
            for field in ("current", "baseline", "delta")
        }
        if reason is None and unseen
        else None
    )
    visible = [
        {
            **row,
            **{
                field: _decimal_text(row[field])
                for field in ("current", "baseline", "delta")
            },
        }
        for row in breakdown[: plan["top"]]
    ]
    return {
        "metric": metric,
        "label": topic.metrics[metric].label,
        "unit": topic.metrics[metric].unit,
        "dimension": plan["group"],
        "dimension_label": topic.dimensions[plan["group"]].label,
        "rows": visible,
        "total": {key: _decimal_text(value) for key, value in total.items()},
        "truncated": not source_complete or bool(unseen),
        "omitted": len(unseen) if source_complete else None,
        "other": other,
        "reconciled": reason is None,
        "reason": reason,
        "coverage": {
            "source_complete": source_complete,
            "source_limit_per_period": DecompositionSourceLimit,
            "current_groups": len(current_groups),
            "baseline_groups": len(baseline_groups),
            "tolerance": _decimal_text(config.tolerance),
        },
    }


def _present_decomposition(topic, plan, rows):
    with localcontext() as precision:
        precision.prec = 120
        data = _decomposition_data(topic, plan, rows)
    display_precision = topic.metrics[data["metric"]].precision
    numeric_format = {"precision": display_precision} if display_precision is not None else {}
    data.update(numeric_format)
    columns = [
        {"key": "dimension", "label": data["dimension_label"], "numeric": False},
        *(
            {"key": key, "label": label, "numeric": True, "unit": data["unit"], **numeric_format}
            for key, label in (
                ("current", "本期"),
                ("baseline", "基期"),
                ("delta", "差额"),
            )
        ),
    ]
    if topic.dimensions[plan["group"]].identity:
        columns.insert(
            1,
            {
                "key": "dimension_key",
                "label": data["dimension_label"] + " ID",
                "numeric": False,
            },
        )
    outputs = [
        {
            "id": data["metric"] + "_" + key,
            "label": data["label"] + " · " + label,
            "unit": "%" if key == "rate" else data["unit"],
            "value": value,
            **numeric_format,
            "status": "verified" if value is not None else "missing",
        }
        for key, label in (
            ("current", "本期"),
            ("baseline", "基期"),
            ("delta", "差额"),
            ("rate", "变化率（%）"),
        )
        for value in (data["total"][key],)
    ]
    table_rows = [
        {
            "dimension": row["label"],
            **(
                {"dimension_key": row["key"]}
                if topic.dimensions[plan["group"]].identity
                else {}
            ),
            **{key: row[key] for key in ("current", "baseline", "delta")},
        }
        for row in data["rows"]
    ]
    return (
        {
            "series_mode": "none",
            "series_semantics": "unknown",
            "title": f"{data['label']} · {data['dimension_label']}",
            "unit": data["unit"],
            "limit": plan["top"],
            "truncated": data["truncated"],
            "rows": [],
            "decomposition": data,
            "table": {"columns": columns, "rows": table_rows},
        },
        outputs,
    )


def present(topic: TopicSemantics, plan, rows):
    if plan["compare_start"] and plan["group"] != "none":
        return _present_decomposition(topic, plan, rows)
    labels = {
        k: (m.label + "（" + m.unit + "）" if m.unit else m.label)
        for k, m in topic.metrics.items()
    }
    ordered_group = topic.dimensions.get(plan["group"])
    metric_ids = plan["metrics"]
    trend = plan["group"] == "day"
    grouped = plan["group"] != "none" and not plan["detail"]
    truncated = (grouped or plan["detail"]) and len(rows) > plan["top"]
    outputs, chart = [], []
    columns = []
    title = "分析结果"
    if plan["detail"]:
        columns = list(topic.detail.items())
        title = "原始记录明细"
    elif grouped:
        name = ordered_group.label if ordered_group else "业务日"
        columns = [("dimension", name)]
        if ordered_group and ordered_group.identity:
            columns.append(
                (
                    "dimension_id",
                    topic.dimensions.get(ordered_group.identity, ordered_group).label,
                )
            )
        columns += [(k, labels[k]) for k in metric_ids]
        metric = metric_ids[0]
        title = f"{topic.metrics[metric].label} · {name}"
        chart = [
            {
                "label": str(row["dimension"])
                if row["dimension"] is not None
                else None,
                "value": row[metric],
            }
            for row in rows[: plan["top"]]
        ]
        aggregate = rows[0] if rows else {}
        if trend:
            start, end = (
                date.fromisoformat(plan["start"]),
                date.fromisoformat(plan["end"]),
            )
            by_day = {r["dimension"]: r for r in rows}
            rows = [
                by_day.get(
                    str(start + timedelta(days=i)),
                    {"dimension": str(start + timedelta(days=i))},
                )
                for i in range((end - start).days)
            ]
            chart = [{"label": r["dimension"], "value": r.get(metric)} for r in rows]
        if aggregate:
            outputs.extend(
                {
                    "id": k,
                    "label": topic.metrics[k].label,
                    "unit": topic.metrics[k].unit,
                    "value": aggregate.get("all_" + k),
                    **({"precision": topic.metrics[k].precision}
                       if topic.metrics[k].precision is not None else {}),
                    "status": "verified",
                }
                for k in plan.get("overall_metrics", [])
            )
    else:
        columns = [("period", "比较范围")] if plan["compare_start"] else []
        columns += [(k, labels[k]) for k in metric_ids]
        title = "期间对比" if plan["compare_start"] else "指标概览"
        if plan["compare_start"]:
            by_period = {row.get("period"): row for row in rows}
            if len(rows) != 2 or set(by_period) != {"current", "baseline"}:
                raise ValueError("Expected one semantic summary row per period")
            values = {
                key: {"period": name}
                for key, name in (
                    ("current", "本期"),
                    ("baseline", "基期"),
                    ("delta", "差额"),
                    ("rate", "变化率（%）"),
                )
            }
        elif len(rows) != 1:
            raise ValueError("Expected one semantic summary row")
        for k in metric_ids:
            m = topic.metrics[k]
            if not plan["compare_start"]:
                value = rows[0][k]
                outputs.append(
                    {
                        "id": k,
                        "label": m.label,
                        "unit": m.unit,
                        "value": value,
                        **({"precision": m.precision} if m.precision is not None else {}),
                        "status": "verified" if value is not None else "missing",
                    }
                )
                continue
            a, b = by_period["current"][k], by_period["baseline"][k]
            delta = (
                Decimal(str(a)) - Decimal(str(b))
                if a is not None and b is not None
                else None
            )
            rate = (
                round(delta / Decimal(str(b)) * 100, 6)
                if delta is not None and b > 0
                else None
            )
            for key, value in (
                ("current", a),
                ("baseline", b),
                ("delta", delta),
                ("rate", rate),
            ):
                value = float(value) if isinstance(value, Decimal) else value
                values[key][k] = value
                outputs.append(
                    {
                        "id": k + "_" + key,
                        "label": m.label + " · " + values[key]["period"],
                        "unit": "%" if key == "rate" else m.unit,
                        "value": value,
                        **({"precision": m.precision} if m.precision is not None else {}),
                        "status": "verified" if value is not None else "missing",
                    }
                )
        if plan["compare_start"]:
            rows = list(values.values())
    keys = [k for k, _ in columns]
    display_rows = [{key: r.get(key) for key in keys} for r in rows[: plan["top"]]]
    presentation = {
        "series_mode": "none",
        "series_semantics": "unknown",
        "title": title,
        "limit": plan["top"],
        "truncated": truncated,
        "rows": chart,
        "table": {
            "columns": [
                {
                    "key": k,
                    "label": v,
                    "numeric": k in topic.metrics
                    or any(
                        m.field == k and m.op in {"sum", "sum_zero", "average"}
                        for m in topic.metrics.values()
                    ),
                    **({"precision": topic.metrics[k].precision}
                       if k in topic.metrics and topic.metrics[k].precision is not None else {}),
                }
                for k, v in columns
            ],
            "rows": display_rows,
        },
    }
    if grouped:
        presentation["shape"] = "trend" if trend else "category"
        presentation["unit"] = topic.metrics[metric_ids[0]].unit
    return presentation, outputs
