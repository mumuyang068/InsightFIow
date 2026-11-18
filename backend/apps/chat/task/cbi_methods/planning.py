import asyncio
import json
from time import monotonic

from langchain_core.messages import HumanMessage, SystemMessage

from apps.ai_model.model_factory import LLMFactory, get_default_config

from .contracts import Interpretation

PROMPT_VERSION = "cbi-method-interpret-v2"
PROMPT = """Map the user question to a single JSON object.
Exact keys: action, method, changes, members, clarification.
action: run, inspect, reread, clarify, or unsupported.
method: copy binding method. changes: ONLY explicitly changed fields from window_hours,
observation_asof, entry_start, entry_end, cohort_channel, repeat_channel, eligibility.
channels: ad,direct,search,email; null means explicitly reset only. eligibility: paid or net.
Never copy binding defaults into changes. For default settings use an empty changes object. Example:
{"action":"run","method":"ordered_attempt_funnel","changes":{"window_hours":48},"members":[],"clarification":null}
Membership inspection: action inspect and member IDs, empty list for all. No new calculation.
When reading just a saved result with original conditions: reread, empty changes, no other results.
First-purchase ad means cohort_channel ad only, repeat unrestricted unless explicitly asked.
Full population clears both filters with explicit null. Fixed-window repeat differs from D30 retention.
No invented dates, dataset, facts or numbers. If unavailable or ambiguous set clarification.
Return plain JSON with exactly those keys."""


async def interpret(question, binding, previous, saved=False):
    begin = monotonic()
    payload = {"question": question, "binding": binding.model_context(), "previous_plan": previous, "saved": saved}
    trace = {"prompt_version": PROMPT_VERSION, "input": payload, "status": "failed"}
    try:
        model = await get_default_config()
        trace["model"] = model.model_name
        reply = await asyncio.wait_for(LLMFactory.create_llm(model).llm.ainvoke([
            SystemMessage(content=PROMPT), HumanMessage(content=json.dumps(payload, ensure_ascii=False)),
        ]), timeout=45)
        raw = str(reply.content).strip()
        trace.update(raw=raw, usage=reply.usage_metadata)
        result = Interpretation.model_validate_json(raw.removeprefix("```json").removeprefix("```").removesuffix("```").strip())
        if result.method != binding.method:
            raise ValueError("model_method_binding_mismatch")
        trace["status"] = "accepted"
        return result, trace
    except Exception as exc:
        trace["error"] = type(exc).__name__
        return None, trace
    finally:
        trace["seconds"] = round(monotonic() - begin, 3)
