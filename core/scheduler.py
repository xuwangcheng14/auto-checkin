"""后台调度线程: 每 15 秒扫描一次到期任务并提交执行。

- next_run_at 由本模块维护: 派发前先推进, 避免同一时刻重复派发
- 应用重启后, 到期未跑的任务会补跑一次(签到类脚本天然幂等)
"""
import threading
import traceback

from . import cron, db, runner

TICK_SECONDS = 15

_stop = threading.Event()
_thread = None


def start():
    global _thread
    if _thread and _thread.is_alive():
        return
    _stop.clear()
    _thread = threading.Thread(target=_loop, name="checkin-scheduler", daemon=True)
    _thread.start()


def stop():
    _stop.set()


def _loop():
    try:
        _bootstrap()
    except Exception:
        traceback.print_exc()
    while not _stop.wait(TICK_SECONDS):
        try:
            _tick()
        except Exception:
            traceback.print_exc()


def _bootstrap():
    """启动时为没有 next_run_at 的启用任务计算下次运行时间。"""
    for t in db.query(
        "SELECT id, cron FROM tasks WHERE enabled=1 AND (next_run_at IS NULL OR next_run_at='')"
    ):
        _set_next(t["id"], t["cron"])


def _set_next(task_id, cron_expr):
    try:
        nxt = cron.next_after(cron_expr)
    except ValueError:
        nxt = None
    db.execute(
        "UPDATE tasks SET next_run_at=? WHERE id=?",
        (db.fmt_dt(nxt) if nxt else None, task_id),
    )


def _tick():
    now = db.now_str()
    for t in db.query("SELECT id, cron, next_run_at FROM tasks WHERE enabled=1"):
        if not t["next_run_at"]:
            _set_next(t["id"], t["cron"])
            continue
        if t["next_run_at"] <= now:
            accepted, _ = runner.submit(t["id"], trigger="auto")
            if accepted:
                _set_next(t["id"], t["cron"])
