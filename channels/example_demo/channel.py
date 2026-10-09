#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""示例渠道: 本地模拟签到, 不访问网络, 用于测试平台与演示接入规范。

行为:
  - username = "fail"  -> 固定返回失败(用于测试失败路径)
  - 其他               -> 模拟耗时 2 秒后返回成功
"""
import time

META = {
    "name": "示例渠道(本地模拟)",
    "version": "1.0.0",
    "author": "platform",
    "description": "本地模拟签到, 不访问网络。username 填 fail 可测试失败路径; 此渠道仅用于演示, 可随时删除。",
    "config_schema": [
        {"key": "username", "label": "模拟账号", "type": "text", "required": True,
         "placeholder": "随便填; 填 fail 则模拟失败", "help": "用于测试平台的成功/失败展示"},
        {"key": "points", "label": "模拟奖励积分", "type": "number", "default": 10},
        {"key": "sleep_seconds", "label": "模拟耗时(秒)", "type": "number", "default": 2},
        {"key": "notify", "label": "模拟开关", "type": "bool", "default": True},
        {"key": "scene", "label": "模拟场景", "type": "select",
         "options": [{"value": "app", "label": "APP 签到"}, {"value": "web", "label": "网页签到"}],
         "default": "app"},
    ],
}


def run(config):
    username = str(config.get("username") or "").strip()
    if not username:
        return {"success": False, "message": "缺少模拟账号"}

    try:
        sleep_seconds = float(config.get("sleep_seconds") or 2)
    except ValueError:
        sleep_seconds = 2

    print("[%s] 开始模拟签到, 场景: %s" % (username, config.get("scene", "app")))
    print("[%s] 通知开关: %s" % (username, "开" if config.get("notify") else "关"))
    time.sleep(max(0, sleep_seconds))

    if username.lower() == "fail":
        print("[%s] 模拟签到失败" % username)
        return {"success": False, "message": "模拟签到失败: 账号状态异常"}

    points = int(config.get("points") or 10)
    print("[%s] 模拟签到成功, 获得 %d 积分" % (username, points))
    return {
        "success": True,
        "message": "签到成功, 获得 %d 积分" % points,
        "data": {"points": points, "account": username},
    }


# ==================== 平台引导代码, 无需修改 ====================
if __name__ == "__main__":
    import json as _json
    import sys as _sys

    try:
        _config = _json.loads(_sys.stdin.read() or "{}")
    except Exception:
        _config = {}
    try:
        _result = run(_config)
        if not isinstance(_result, dict):
            _result = {"success": False, "message": "run() 未返回字典"}
    except Exception as _exc:  # noqa: BLE001
        _result = {"success": False, "message": "脚本异常: %s" % _exc}
    print("__CHECKIN_RESULT__" + _json.dumps(_result, ensure_ascii=False))
