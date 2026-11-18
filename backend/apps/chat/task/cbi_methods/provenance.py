from uuid import uuid4


def compose_definition(plan, binding, change, parent_id, origin, origin_ref):
    submitted = change.model_fields_set
    sources = {}
    for key in plan:
        if key in submitted:
            sources[key] = {"kind": origin, "id": origin_ref}
        elif parent_id:
            sources[key] = {"kind": "run", "id": parent_id}
        else:
            sources[key] = {"kind": "binding", "id": binding.id, "version": binding.version}
    return {
        "id": uuid4().hex, "plan": plan, "parent_run": parent_id, "inputs": {}, "store": plan["store"],
        "binding_label": getattr(binding, "label", binding.id),
        "sources": sources, "submitted": change.model_dump(exclude_unset=True),
    }
