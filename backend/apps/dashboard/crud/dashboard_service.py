import base64

from orjson import orjson
from sqlalchemy import select, and_, text
from fastapi import HTTPException

from apps.chat.curd.chat import get_chart_data_ds
from apps.chat.models.chat_model import ChatRecord
from apps.chat.task.cbi_analysis import check_saved_access
from apps.chat.task.cbi_methods.workflow import saved_funnel_result
from apps.dashboard.models.dashboard_model import CoreDashboard, CreateDashboard, QueryDashboard, DashboardBaseResponse
from common.core.deps import SessionDep, CurrentUser
import uuid
import time

from common.utils.tree_utils import build_tree_generic


def list_resource(session: SessionDep, dashboard: QueryDashboard, current_user: CurrentUser):
    sql = "SELECT id, name, type, node_type, pid, create_time FROM core_dashboard"
    filters = []
    params = {}
    oid = str(current_user.oid if current_user.oid is not None else 1)
    filters.append("workspace_id = :workspace_id")
    filters.append("create_by = :create_by")
    params["workspace_id"] = oid
    params["create_by"] = str(current_user.id)
    if dashboard.node_type is not None and dashboard.node_type != "":
        filters.append("node_type = :node_type")
        params["node_type"] = dashboard.node_type

    if filters:
        sql += " WHERE " + " AND ".join(filters)
    sql += " ORDER BY create_time DESC"
    result = session.execute(text(sql), params)
    nodes = [DashboardBaseResponse(**row) for row in result.mappings()]
    tree = build_tree_generic(nodes, root_pid="root")
    return tree


def _saved_record(session, item):
    state = item["analysis_state"]
    marker = state.get("multi")
    record_id = str(item.get("record_id") or (marker or {}).get("record_id") or "")
    if not record_id and not marker:
        # Earlier canvases embedded the summary receipt but no separate link.
        record_id = next((
            step.get("record_id") or "" for step in state.get("steps", ())
            if step.get("kind") in {"summary", "trend", "category", "semantic"} and step.get("status") == "verified"
        ), "")
    if isinstance(record_id, str) and record_id.isdecimal():
        record = session.get(ChatRecord, int(record_id))
    elif marker and str(marker.get("task_id", "")).isdecimal() and marker.get("run_id"):
        record = session.scalars(select(ChatRecord).where(
            ChatRecord.chat_id == int(marker["task_id"]),
            ChatRecord.analysis_state["multi"]["run_id"].as_string() == marker["run_id"],
        )).first()
    else:
        record = None
    if (not record or not record.analysis_state
            or str(record.datasource) != str(item.get("datasource"))
            or bool(record.analysis_state.get("multi")) != bool(marker)):
        raise HTTPException(404, "analysis_not_found")
    return record


def _freeze_adapted_views(session, user, encoded, previous=None):
    if not encoded:
        return encoded
    views = orjson.loads(encoded)
    existing = orjson.loads(previous) if previous else {}
    for key, item in views.items():
        if not item.get("analysis_state"):
            item.pop("snapshot_frozen", None)
            continue
        record = _saved_record(session, item)
        check_saved_access(session, user, record)
        saved = existing.get(key, {})
        if saved.get("snapshot_frozen") is True and saved.get("record_id") == str(record.id):
            item.update({field: saved[field] for field in ("analysis_state", "data", "sql")})
        else:
            item["analysis_state"] = record.analysis_state
            item["data"] = {"data": orjson.loads(record.data)["data"]} if record.data else {"data": []}
            item["sql"] = record.sql
        item["record_id"] = str(record.id)
        item["snapshot_frozen"] = True
    return orjson.dumps(views).decode()


def load_resource(session: SessionDep, dashboard: QueryDashboard, current_user: CurrentUser):
    sql = text("""
               SELECT cd.*,
                      creator.name AS create_name,
                      updater.name AS update_name
               FROM core_dashboard cd
                        LEFT JOIN sys_user creator ON cd.create_by = creator.id::varchar
        LEFT JOIN sys_user updater
               ON cd.update_by = updater.id:: varchar
               WHERE cd.id = :dashboard_id
               """)
    result = session.execute(sql, {"dashboard_id": dashboard.id}).mappings().first()

    if (not result or str(result["create_by"]) != str(current_user.id)
            or str(result["workspace_id"]) != str(current_user.oid if current_user.oid is not None else 1)):
        raise HTTPException(404, "dashboard_not_found")

    result_dict = dict(result)
    canvas_view_obj = orjson.loads(result_dict['canvas_view_info'])
    for item in canvas_view_obj.values():
        # An adapted result pairs persisted rows with the scope, derived outputs,
        # and evidence of that run. Its primary query is only one of those inputs.
        if item.get("analysis_state"):
            record = _saved_record(session, item)
            check_saved_access(session, current_user, record)
            item["record_id"] = str(record.id)
            item["chat_id"] = str(record.chat_id)
            if item.get("snapshot_frozen") is not True:
                item["analysis_state"] = record.analysis_state
                item["sql"] = record.sql
                item["data"] = {"data": orjson.loads(record.data)["data"]} if record.data else {"data": []}
            item["method_result"] = saved_funnel_result(session, record)
            continue
        if all(key in item for key in ['datasource', 'sql']) and item['datasource'] is not None and item['sql'] is not None:
            data_result = get_chart_data_ds(session, item['datasource'], item['sql'])
            item['data']['data'] = data_result['data']
            item['status'] = data_result['status']
            item['message'] = data_result['message']
    result_dict['canvas_view_info'] = orjson.dumps(canvas_view_obj)
    return result_dict


def get_create_base_info(user: CurrentUser, dashboard: CreateDashboard):
    new_id = uuid.uuid4().hex
    record = CoreDashboard(**dashboard.model_dump())
    record.workspace_id = user.oid
    record.id = new_id
    record.create_by = user.id
    record.create_time = int(time.time())
    return record


def create_resource(session: SessionDep, user: CurrentUser, dashboard: CreateDashboard):
    record = get_create_base_info(user, dashboard)
    session.add(record)
    session.flush()
    session.refresh(record)
    session.commit()
    return record


def update_resource(session: SessionDep, user: CurrentUser, dashboard: QueryDashboard):
    record = session.query(CoreDashboard).filter(CoreDashboard.id == dashboard.id).first()
    if not record:
        raise ValueError(f"Resource with id {dashboard.id} does not exist")
    if str(record.create_by) != str(user.id):
        raise ValueError(f"Resource with id {dashboard.id} not owned by the current user")
    record.name = dashboard.name
    record.update_by = user.id
    record.update_time = int(time.time())
    session.add(record)
    session.commit()
    return record


def create_canvas(session: SessionDep, user: CurrentUser, dashboard: CreateDashboard):
    record = get_create_base_info(user, dashboard)
    record.node_type = dashboard.node_type
    record.component_data = dashboard.component_data
    record.canvas_style_data = dashboard.canvas_style_data
    record.canvas_view_info = _freeze_adapted_views(session, user, dashboard.canvas_view_info)
    session.add(record)
    session.flush()
    session.refresh(record)
    session.commit()
    return record


def update_canvas(session: SessionDep, user: CurrentUser, dashboard: CreateDashboard):
    record = session.query(CoreDashboard).filter(CoreDashboard.id == dashboard.id).first()
    if not record:
        raise ValueError(f"Resource with id {dashboard.id} does not exist")
    if str(record.create_by) != str(user.id):
        raise ValueError(f"Resource with id {dashboard.id} not owned by the current user")
    record.name = dashboard.name
    record.update_by = user.id
    record.update_time = int(time.time())
    record.component_data = dashboard.component_data
    record.canvas_style_data = dashboard.canvas_style_data
    record.canvas_view_info = _freeze_adapted_views(
        session, user, dashboard.canvas_view_info, record.canvas_view_info
    )
    session.add(record)
    session.commit()
    return record


def validate_name(session: SessionDep,user: CurrentUser,  dashboard: QueryDashboard) -> bool:
    if not dashboard.opt:
        raise ValueError("opt is required")
    oid = str(user.oid if user.oid is not None else 1)
    uid = str(user.id)


    if dashboard.opt in ('newLeaf', 'newFolder'):
        query = session.query(CoreDashboard).filter(
            and_(
                CoreDashboard.workspace_id == oid,
                CoreDashboard.create_by == uid,
                CoreDashboard.name == dashboard.name
            )
        )
    elif dashboard.opt in ('updateLeaf', 'updateFolder', 'rename'):
        if not dashboard.id:
            raise ValueError("id is required for update operation")
        query = session.query(CoreDashboard).filter(
            and_(
                CoreDashboard.workspace_id == oid,
                CoreDashboard.create_by == uid,
                CoreDashboard.name == dashboard.name,
                CoreDashboard.id != dashboard.id
            )
        )
    else:
        raise ValueError(f"Invalid opt value: {dashboard.opt}")
    return not session.query(query.exists()).scalar()


def delete_resource(session: SessionDep, current_user: CurrentUser, resource_id: str):
    coreDashboard = session.get(CoreDashboard, resource_id)
    if not coreDashboard:
        raise ValueError(f"Resource with id {resource_id} does not exist")
    if str(coreDashboard.create_by) != str(current_user.id):
        raise ValueError(f"Resource with id {resource_id} not owned by the current user")
    sql = text("DELETE FROM core_dashboard WHERE id = :resource_id")
    result = session.execute(sql, {"resource_id": resource_id})
    session.commit()
    return result.rowcount > 0
