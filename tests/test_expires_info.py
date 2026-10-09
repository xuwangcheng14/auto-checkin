# -*- coding: utf-8 -*-
"""expires_info(令牌有效期)功能的单元测试。

覆盖:
  1. db._migrate 幂等为 tasks 表补 expires_info 列
  2. runner._execute 从渠道输出中提取 data.expires_info
  3. runner._execute 对无 expires_info 的结果返回 None
  4. workbuddy 渠道 dry-run 真实输出含 expires_info

运行: python -X utf8 tests/test_expires_info.py
(纯离线/幂等操作, 不发起任何网络请求)
"""
import json
import os
import subprocess
import sys
import tempfile

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

from core import db, runner  # noqa: E402

PASSED, FAILED = [], []


def check(name, cond, detail=""):
    (PASSED if cond else FAILED).append(name)
    print(("  ✅ %s" if cond else "  ❌ %s") % name + (" | %s" % detail if detail and not cond else ""))


def test_db_migrate():
    print("[1] db._migrate 为 tasks 表补列(幂等)")
    db.init_db()  # 第一次: 补列
    db.init_db()  # 第二次: 幂等, 不应报错
    cols = {r["name"] for r in db.query("PRAGMA table_info(tasks)")}
    check("tasks 表含 expires_info 列", "expires_info" in cols, str(cols))


def _write_fake_channel(directory, payload):
    """构造一个假渠道脚本, 输出固定结果行。返回 channel.py 路径。"""
    os.makedirs(directory, exist_ok=True)
    path = os.path.join(directory, "channel.py")
    with open(path, "w", encoding="utf-8") as f:
        f.write(
            "import json\n"
            "print('hello from fake channel')\n"
            "print('__CHECKIN_RESULT__' + json.dumps(%r, ensure_ascii=False))\n" % payload
        )
    return path


def test_runner_extract():
    print("[2] runner._execute 提取 data.expires_info")

    class FakeChannel:
        def __getitem__(self, key):
            return {"channel": "fake", "timeout": 30}.get(key)

    expires = [{"label": "主号", "expires_at": "2026-11-22 02:06:22"}]
    payload = {"success": True, "message": "签到成功", "data": {"expires_info": expires}}
    with tempfile.TemporaryDirectory() as td:
        path = _write_fake_channel(td, payload)
        orig = runner.channels.get_channel_path
        runner.channels.get_channel_path = lambda ch: path
        try:
            status, message, output, got = runner._execute(FakeChannel(), {}, 30)
        finally:
            runner.channels.get_channel_path = orig
    check("状态为 success", status == "success", status)
    check("expires_info 被提取(extras 字典, 值为 JSON 字符串)",
          got == {"expires_info": json.dumps(expires, ensure_ascii=False)}, repr(got))

    print("[3] 无 expires_info 的结果返回 None")
    payload2 = {"success": True, "message": "ok", "data": {"points": 1}}
    with tempfile.TemporaryDirectory() as td:
        path = _write_fake_channel(td, payload2)
        orig = runner.channels.get_channel_path
        runner.channels.get_channel_path = lambda ch: path
        try:
            _, _, _, got2 = runner._execute(FakeChannel(), {}, 30)
        finally:
            runner.channels.get_channel_path = orig
    check("expires_info 为 None", got2 is None, repr(got2))


def test_workbuddy_dryrun():
    print("[4] workbuddy 渠道 dry-run 输出含 expires_info")
    # 用一个本地构造的 JWT(明文可用, 仅本地解析不联网)
    import base64
    import datetime

    def b64(obj):
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).decode().rstrip("=")

    exp = int((datetime.datetime.now() + datetime.timedelta(days=29)).timestamp())
    jwt_token = "%s.%s.%s" % (b64({"alg": "RS256"}), b64({"exp": exp}), "sig")
    config = {"accounts": jwt_token + "#fake-uid-000#测试号", "dry_run": True}

    channel_path = os.path.join(BASE, "channels", "workbuddy", "channel.py")
    proc = subprocess.run(
        [sys.executable, "-X", "utf8", channel_path],
        input=json.dumps(config).encode("utf-8"),
        capture_output=True, timeout=60,
    )
    out = proc.stdout.decode("utf-8", "replace")
    result_line = [ln for ln in out.splitlines() if ln.startswith("__CHECKIN_RESULT__")]
    check("输出含 __CHECKIN_RESULT__ 行", bool(result_line))
    if not result_line:
        return
    result = json.loads(result_line[-1][len("__CHECKIN_RESULT__"):])
    info = ((result.get("data") or {}).get("expires_info")) or []
    check("success=true(dry-run 视为成功)", result.get("success") is True, repr(result))
    check("expires_info 含账号与过期时间",
          len(info) == 1 and info[0]["label"] == "测试号" and info[0]["expires_at"],
          repr(info))


if __name__ == "__main__":
    test_db_migrate()
    test_runner_extract()
    test_workbuddy_dryrun()
    print("\n结果: %d 通过, %d 失败" % (len(PASSED), len(FAILED)))
    sys.exit(1 if FAILED else 0)
