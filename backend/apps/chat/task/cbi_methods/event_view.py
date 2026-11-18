from fastapi import HTTPException
from sqlalchemy import text

from apps.db.db import get_session

from .binding import SHANGHAI, absolute


def event_rows(source, topic, binding):
    schema, name = binding.view.split(".")
    sql = f'''SELECT event_id, attempt_id, event_name, occurred_at, store_id
        FROM "{schema}"."{name}" WHERE store_id = :store AND occurred_at <= :asof
        ORDER BY occurred_at, event_id LIMIT 100001'''
    args = {"store":binding.store,"asof":absolute(binding.observation_asof)}
    with get_session(source) as db:
        db.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
        db.execute(text("SET LOCAL statement_timeout = '10s'"))
        reader, mode = db.execute(text("select current_user,current_setting('transaction_read_only')")).one()
        if reader != topic.reader or mode != "on":
            raise HTTPException(409, "analysis_source_not_readonly")
        rows = db.execute(text(sql),args).all()
    if len(rows) > 100000:
        raise HTTPException(422, "event_topic_row_limit")
    epoch = absolute(binding.epoch)
    events = []
    ids = set()
    for id_, attempt, name, at, store in rows:
        if not id_ or not attempt or at.tzinfo is None or at.microsecond or store != binding.store or name not in {"view","cart","pay"}:
            raise HTTPException(422, "invalid_event_view_row")
        if id_ in ids:
            raise HTTPException(422, "duplicate_event_view_id")
        ids.add(id_)
        events.append({"id":id_,"attempt":attempt,"event":name,"t":int((at-epoch).total_seconds()),
                       "observed_at":at.astimezone(SHANGHAI).isoformat()})
    return events, {"statement":sql,"params":{"store":binding.store,"asof":args["asof"].isoformat()},
                    "reader":reader,"read_only":True,"row_count":len(events),"binding_version":binding.version,
                    "view":binding.view}
