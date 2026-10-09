"""任务执行器: 以子进程运行渠道脚本, 捕获输出并落库。

执行契约(渠道脚本视角, 详见 docs/渠道开发指南.md):
  - 配置 JSON 通过 stdin 传入, 同时注入环境变量 CHECKIN_CONFIG 与 CHECKIN_<KEY>
  - stdout/stderr 全部捕获作为执行日志
  - 脚本结束时输出一行 "__CHECKIN_RESULT__{...json...}" 作为结构化结果
"""
import json
import os
import subprocess
import sys
import threading
import traceback
from datetime import datetime

from . import channels, db

RESULT_PREFIX = "__CHECKIN_RESULT__"
MAX_OUTPUT_CHARS = 200_000

_running = set()
_lock = threading.Lock()


def is_running(task_id):
    return task_id in _running


def running_count():
    return len(_running)


def submit(task_id, trigger="manual"):
    """提交一次执行(异步)。返回 (是否受理, 说明)。同一任务不会并发执行。"""
    with _lock:
        if task_id in _running:
            return False, "任务正在运行中"
        _running.add(task_id)
    threading.Thread(
        target=_wrap, args=(task_id, trigger),
        name="checkin-run-%d" % task_id, daemon=True,
    ).start()
    return True, "已开始执行"


def _wrap(task_id, trigger):
    try:
        _run(task_id, trigger)
    except Exception:
        traceback.print_exc()
    finally:
        with _lock:
            _running.discard(task_id)


def _run(task_id, trigger):
    task = db.query_one("SELECT * FROM tasks WHERE id=?", (task_id,))
    if task is None:
        return
    try:
        config = json.loads(task["config"] or "{}")
    except ValueError:
        config = {}
    if not isinstance(config, dict):
        config = {}

    started = datetime.now()
    log_id = db.execute(
        "INSERT INTO logs (task_id, status, trigger, started_at) VALUES (?,?,?,?)",
        (task_id, "running", trigger, db.fmt_dt(started)),
    )
    db.execute(
        "UPDATE tasks SET last_status='running', last_run_at=?, last_message=NULL WHERE id=?",
        (db.fmt_dt(started), task_id),
    )

    timeout = max(5, int(task["timeout"] or 300))
    status, message, output, extras = _execute(task, config, timeout)

    finished = datetime.now()
    db.execute(
        "UPDATE logs SET status=?, finished_at=?, duration=?, message=?, output=? WHERE id=?",
        (status, db.fmt_dt(finished), (finished - started).total_seconds(), message, output, log_id),
    )
    db.execute(
        "UPDATE tasks SET last_status=?, last_message=? WHERE id=?",
        (status, message, task_id),
    )
    if extras:
        # 渠道上报的附加信息(凭证有效期/积分余额), 仅在有值时更新(失败运行不清空历史)
        for col, val in extras.items():
            db.execute("UPDATE tasks SET %s=? WHERE id=?" % col, (val, task_id))


def _execute(task, config, timeout):
    try:
        channel_path = channels.get_channel_path(task["channel"])
    except Exception as e:
        return "fail", "加载渠道失败: %s" % e, "", None

    env = os.environ.copy()
    env["CHECKIN_CONFIG"] = json.dumps(config, ensure_ascii=False)
    for k, v in config.items():
        if isinstance(v, bool):
            v = "1" if v else "0"
        env["CHECKIN_%s" % str(k).upper()] = str(v)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUNBUFFERED"] = "1"

    cmd = [sys.executable, "-X", "utf8", channel_path]
    try:
        proc = subprocess.Popen(
            cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, cwd=os.path.dirname(channel_path), env=env,
        )
    except Exception as e:
        return "fail", "启动脚本失败: %s" % e, "", None

    timed_out = False
    try:
        out, _ = proc.communicate(
            input=json.dumps(config, ensure_ascii=False).encode("utf-8"), timeout=timeout,
        )
        raw = out.decode("utf-8", "replace") if out else ""
    except subprocess.TimeoutExpired:
        timed_out = True
        proc.kill()
        try:
            out, _ = proc.communicate(timeout=10)
        except Exception:
            out = b""
        raw = (out.decode("utf-8", "replace") if out else "")
        raw += "\n[平台] 执行超过 %d 秒, 进程已被强制终止" % timeout

    output = _truncate(raw)
    result = _extract_result(raw)
    extras = _extras(result)

    if timed_out:
        return "fail", "执行超时(超过 %d 秒), 进程已被强制终止" % timeout, output, extras or None
    if isinstance(result, dict):
        success = result.get("success") is True
        message = str(result.get("message") or ("执行成功" if success else "执行失败"))
        return ("success" if success else "fail"), message[:500], output, extras or None
    if proc.returncode == 0:
        return "success", "执行完成(脚本未返回结构化结果)", output, extras or None
    return "fail", "脚本异常退出(退出码 %s)" % proc.returncode, output, extras or None


def _extras(result):
    """从渠道结果 data 中提取平台展示用的附加信息(凭证有效期/积分余额)。"""
    if not isinstance(result, dict) or not isinstance(result.get("data"), dict):
        return {}
    data = result["data"]
    extras = {}
    expires = data.get("expires_info")
    if expires:
        extras["expires_info"] = expires if isinstance(expires, str) else json.dumps(expires, ensure_ascii=False)
    balance = data.get("balance_info")
    if balance:
        extras["balance_info"] = balance if isinstance(balance, str) else json.dumps(balance, ensure_ascii=False)
    return extras


def _extract_result(output):
    """从输出中提取最后一行结构化结果。"""
    for line in reversed(output.splitlines()):
        if line.startswith(RESULT_PREFIX):
            try:
                obj = json.loads(line[len(RESULT_PREFIX):])
                if isinstance(obj, dict):
                    return obj
            except ValueError:
                pass
    return None


def _truncate(text, limit=MAX_OUTPUT_CHARS):
    if len(text) <= limit:
        return text
    return text[:limit // 4] + "\n...[输出过长, 中间部分已截断]...\n" + text[-limit // 2:]
