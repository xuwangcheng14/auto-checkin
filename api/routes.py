"""REST API 路由。所有响应统一为 {"ok": bool, "data"/"message": ...}。"""
import json
from datetime import datetime

from flask import Blueprint, jsonify, request

from core import channels as channels_mod
from core import cron, db, runner

bp = Blueprint("api", __name__)


def ok(data=None):
    return jsonify({"ok": True, "data": data})


def err(message, code=400):
    return jsonify({"ok": False, "message": message}), code


# ---------------------------------------------------------------- 渠道

@bp.get("/api/channels")
def list_channels():
    items = sorted(channels_mod.list_channels().values(), key=lambda c: c["id"])
    return ok(items)


# ---------------------------------------------------------------- 统计

@bp.get("/api/stats")
def stats():
    today = datetime.now().strftime("%Y-%m-%d") + "%"
    return ok({
        "tasks_total": db.query_one("SELECT COUNT(*) c FROM tasks")["c"],
        "tasks_enabled": db.query_one("SELECT COUNT(*) c FROM tasks WHERE enabled=1")["c"],
        "channels_total": sum(1 for c in channels_mod.list_channels().values() if c["valid"]),
        "today_success": db.query_one(
            "SELECT COUNT(*) c FROM logs WHERE status='success' AND started_at LIKE ?", (today,))["c"],
        "today_fail": db.query_one(
            "SELECT COUNT(*) c FROM logs WHERE status='fail' AND started_at LIKE ?", (today,))["c"],
        "running": runner.running_count(),
    })


# ---------------------------------------------------------------- 任务

def _task_json(row):
    d = dict(row)
    meta = channels_mod.list_channels().get(d["channel"]) or {}
    d["channel_name"] = meta.get("name", d["channel"])
    d["channel_valid"] = bool(meta.get("valid"))
    d["running"] = runner.is_running(d["id"])
    return d


def _validate_payload(data, partial=False):
    """校验任务字段, 返回 (clean_fields, error_message)。"""
    out = {}

    if "channel" in data or not partial:
        ch = str(data.get("channel") or "").strip()
        meta = channels_mod.list_channels().get(ch)
        if not meta:
            return None, "渠道不存在: %s" % ch
        if not meta.get("valid"):
            return None, "渠道 %s 加载失败: %s" % (ch, meta.get("error"))
        out["channel"] = ch

    if "name" in data or not partial:
        name = str(data.get("name") or "").strip()
        if not name:
            return None, "任务名称不能为空"
        out["name"] = name[:100]

    if "config" in data or not partial:
        cfg = data.get("config")
        if cfg is None:
            cfg = {}
        if not isinstance(cfg, dict):
            return None, "配置必须是 JSON 对象"
        out["config"] = json.dumps(cfg, ensure_ascii=False)

    if "cron" in data or not partial:
        expr = str(data.get("cron") or "").strip()
        try:
            nxt = cron.next_after(expr)
        except ValueError as e:
            return None, "定时规则无效: %s" % e
        if nxt is None:
            return None, "定时规则在未来一年内没有可执行的时间"
        out["cron"] = expr
        out["_next"] = db.fmt_dt(nxt)

    if "timeout" in data or not partial:
        try:
            to = int(data.get("timeout") or 300)
        except (TypeError, ValueError):
            return None, "超时时间必须是数字(秒)"
        if not 5 <= to <= 86400:
            return None, "超时时间需在 5~86400 秒之间"
        out["timeout"] = to

    if "enabled" in data or not partial:
        out["enabled"] = 1 if data.get("enabled", True) in (True, 1, "1") else 0

    return out, None


@bp.get("/api/tasks")
def list_tasks():
    rows = db.query("SELECT * FROM tasks ORDER BY id")
    return ok([_task_json(r) for r in rows])


@bp.post("/api/tasks")
def create_task():
    data = request.get_json(silent=True) or {}
    fields, error = _validate_payload(data)
    if error:
        return err(error)
    nxt = fields.pop("_next", None)
    now = db.now_str()
    task_id = db.execute(
        "INSERT INTO tasks (channel,name,config,cron,timeout,enabled,created_at,updated_at) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (fields["channel"], fields["name"], fields["config"], fields["cron"],
         fields["timeout"], fields["enabled"], now, now),
    )
    db.execute("UPDATE tasks SET next_run_at=? WHERE id=?", (nxt, task_id))
    return ok(_task_json(db.query_one("SELECT * FROM tasks WHERE id=?", (task_id,))))


@bp.put("/api/tasks/<int:task_id>")
def update_task(task_id):
    row = db.query_one("SELECT * FROM tasks WHERE id=?", (task_id,))
    if not row:
        return err("任务不存在", 404)
    data = request.get_json(silent=True) or {}
    fields, error = _validate_payload(data, partial=True)
    if error:
        return err(error)
    fields.pop("_next", None)
    if fields:
        cols = ", ".join("%s=?" % k for k in fields)
        db.execute("UPDATE tasks SET %s, updated_at=? WHERE id=?" % cols,
                   (*fields.values(), db.now_str(), task_id))
    cur = db.query_one("SELECT enabled, cron FROM tasks WHERE id=?", (task_id,))
    if cur["enabled"]:
        try:
            nxt = db.fmt_dt(cron.next_after(cur["cron"]))
        except ValueError:
            nxt = None
    else:
        nxt = None
    db.execute("UPDATE tasks SET next_run_at=? WHERE id=?", (nxt, task_id))
    return ok(_task_json(db.query_one("SELECT * FROM tasks WHERE id=?", (task_id,))))


@bp.delete("/api/tasks/<int:task_id>")
def delete_task(task_id):
    if runner.is_running(task_id):
        return err("任务正在运行中, 请稍后再删除", 409)
    if not db.query_one("SELECT id FROM tasks WHERE id=?", (task_id,)):
        return err("任务不存在", 404)
    db.execute("DELETE FROM logs WHERE task_id=?", (task_id,))
    db.execute("DELETE FROM tasks WHERE id=?", (task_id,))
    return ok()


@bp.post("/api/tasks/<int:task_id>/run")
def run_task(task_id):
    if not db.query_one("SELECT id FROM tasks WHERE id=?", (task_id,)):
        return err("任务不存在", 404)
    accepted, message = runner.submit(task_id, trigger="manual")
    if not accepted:
        return err(message, 409)
    return ok({"message": message})


@bp.post("/api/tasks/run-all")
def run_all_tasks():
    """一键手动执行: 触发全部启用中的任务, 正在运行中的自动跳过。"""
    rows = db.query("SELECT id, name FROM tasks WHERE enabled=1 ORDER BY id")
    accepted, skipped = [], []
    for r in rows:
        submitted, _ = runner.submit(r["id"], trigger="manual")
        (accepted if submitted else skipped).append(r["name"])
    message = "已触发 %d 个任务" % len(accepted)
    if skipped:
        message += ", %d 个正在运行中已跳过" % len(skipped)
    return ok({"total": len(rows), "accepted": len(accepted), "skipped": len(skipped),
               "accepted_names": accepted, "skipped_names": skipped, "message": message})


# ---------------------------------------------------------------- 日志

@bp.get("/api/logs")
def list_logs():
    try:
        page = max(1, int(request.args.get("page", 1)))
        page_size = min(100, max(1, int(request.args.get("page_size", 20))))
    except ValueError:
        return err("分页参数无效")

    conds, args = [], []
    task_id = request.args.get("task_id", type=int)
    status = request.args.get("status")
    if task_id:
        conds.append("l.task_id=?")
        args.append(task_id)
    if status in ("success", "fail", "running"):
        conds.append("l.status=?")
        args.append(status)
    where = ("WHERE " + " AND ".join(conds)) if conds else ""

    total = db.query_one("SELECT COUNT(*) c FROM logs l %s" % where, args)["c"]
    rows = db.query(
        "SELECT l.id, l.task_id, t.name AS task_name, l.status, l.trigger, l.started_at, "
        "l.finished_at, l.duration, l.message FROM logs l "
        "LEFT JOIN tasks t ON t.id=l.task_id %s ORDER BY l.id DESC LIMIT ? OFFSET ?" % where,
        (*args, page_size, (page - 1) * page_size),
    )
    return ok({"items": [dict(r) for r in rows], "total": total,
               "page": page, "page_size": page_size})


@bp.get("/api/logs/<int:log_id>")
def log_detail(log_id):
    row = db.query_one(
        "SELECT l.*, t.name AS task_name FROM logs l "
        "LEFT JOIN tasks t ON t.id=l.task_id WHERE l.id=?", (log_id,))
    if not row:
        return err("日志不存在", 404)
    return ok(dict(row))


# ---------------------------------------------------------------- cron 预览

@bp.get("/api/cron/next")
def cron_next():
    expr = request.args.get("expr", "").strip()
    try:
        count = min(5, max(1, int(request.args.get("count", 3))))
    except ValueError:
        count = 3
    times, after = [], datetime.now()
    try:
        for _ in range(count):
            t = cron.next_after(expr, after=after)
            if not t:
                break
            times.append(db.fmt_dt(t))
            after = t
    except ValueError as e:
        return err("定时规则无效: %s" % e)
    return ok({"times": times})
