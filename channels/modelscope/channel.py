#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
ModelScope(魔搭社区) 每日登录渠道 (纯标准库, 无第三方依赖)。

原理:
  魔搭每天首次"登录"会送魔方, 发奖由网页首页加载时调用的
  /api/v1/users/login/info 触发; 只查余额接口是不会发奖的。
  本渠道流程: 查签到前余额 -> 调 login/info 触发当日登录奖励(顺带校验登录态)
  -> 再查余额, 展示前后变化。

配置获取方法:
  见本目录 README.md(配置获取指南)。

注意:
  - Cookie 有效期有限(数天到数月不等, 以平台实际为准), 失效后任务会报
    "Cookie 已失效", 重新登录网站并按指南重新抓取即可。
  - 一个任务对应一个账号; 多账号请新建多个任务, 每个任务填各账号的 Cookie。
"""
import json
import time
import uuid
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

API_BALANCE = "https://modelscope.cn/openapi/v1/magicubes/balance"
API_LOGIN_INFO = "https://modelscope.cn/api/v1/users/login/info"

META = {
    "name": "ModelScope 每日登录",
    "version": "1.1.0",
    "author": "xuwangcheng",
    "description": "魔搭社区(modelscope.cn)每日登录领魔方: 调 login/info 触发当日登录奖励, 返回签到前后魔方余额变化。",
    "config_schema": [
        {
            "key": "cookie",
            "label": "站点 Cookie",
            "type": "password",
            "required": True,
            "placeholder": "粘贴完整 Cookie(获取方法见「配置获取指南」)",
            "help": "登录 modelscope.cn 后抓取; 过期后任务报「Cookie 已失效」, 重新抓取填入即可",
        },
    ],
}


def log(msg):
    print("[%s] %s" % (time.strftime("%H:%M:%S"), msg), flush=True)


def http_get(url, cookie):
    headers = {
        "Accept": "application/json, text/plain, */*",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/153.0.0.0 Safari/537.36",
        "Referer": "https://modelscope.cn/my/overview",
        "X-Modelscope-Trace-Id": str(uuid.uuid4()),
        "x-modelscope-accept-language": "zh_CN",
        "bx-v": "2.5.37",
        "Cookie": cookie,
    }
    req = Request(url, headers=headers)
    try:
        with urlopen(req, timeout=25) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except URLError as e:
        return 0, "URLError: %s" % e


def fetch_balance(cookie):
    """查询魔方余额, 返回 (data_dict 或 None, err_msg)。"""
    status, body = http_get(API_BALANCE, cookie)
    if status != 200:
        return None, "HTTP %s" % status
    try:
        obj = json.loads(body)
    except ValueError:
        return None, "响应不是合法 JSON"
    if obj.get("success") is not True:
        return None, str(obj.get("message") or obj.get("msg") or obj.get("code"))[:120]
    return obj.get("data") or {}, ""


def run(config):
    cookie = str(config.get("cookie") or "").strip().strip('"').strip("'")
    if not cookie:
        return {"success": False,
                "message": "缺少 Cookie: 请在任务配置中粘贴 modelscope.cn 的 Cookie(获取方法见配置获取指南)"}

    log("Cookie 已加载(%d 字符), 查询签到前魔方余额..." % len(cookie))
    before_data, before_err = fetch_balance(cookie)
    before = before_data.get("total_balance") if before_data is not None else None
    log("签到前魔方: %s" % ("未知(%s)" % before_err if before is None else before))

    log("调用 login/info 触发当日登录奖励...")
    status, body = http_get(API_LOGIN_INFO, cookie)
    log("login/info 返回: HTTP %s" % status)
    if status in (401, 403):
        return {"success": False,
                "message": "Cookie 已失效(HTTP %s): 请登录 modelscope.cn 后按配置获取指南重新抓取 Cookie 并更新任务" % status}
    if status == 0:
        return {"success": False, "message": "网络异常: %s" % body[:200]}
    if status != 200:
        return {"success": False, "message": "login/info 接口异常(HTTP %s), 详情见日志" % status}
    try:
        obj = json.loads(body)
    except ValueError:
        return {"success": False, "message": "login/info 响应不是合法 JSON, 详情见日志"}
    # 该接口返回 {"Code":200,"Success":true,...}, 与 openapi 风格不同
    if obj.get("Success") is not True and obj.get("Code") != 200:
        msg = obj.get("Message") or obj.get("message") or ("code=%s" % obj.get("Code"))
        return {"success": False, "message": "login/info 返回失败: %s" % str(msg)[:200]}
    nickname = (obj.get("Data") or {}).get("NickName") or (obj.get("Data") or {}).get("Name") or ""
    if nickname:
        log("登录态有效, 账号: %s" % nickname)

    log("查询签到后魔方余额...")
    after_data, after_err = fetch_balance(cookie)
    after = after_data.get("total_balance") if after_data is not None else None
    available = after_data.get("available_balance") if after_data is not None else None

    if after is None:
        message = "登录成功(登录态有效), 魔方余额查询失败(%s)" % after_err
    elif before is None:
        message = "登录成功, 当前魔方: 总计 %s / 可用 %s" % (after, available if available is not None else "?")
    elif after > before:
        message = "登录成功, 每日奖励已到账: 魔方 %s → %s (+%s)" % (before, after, after - before)
    else:
        message = "登录成功, 今日奖励已领取: 魔方总计 %s / 可用 %s (无增量)" % (after, available if available is not None else "?")
    log(message)

    payload = dict(after_data) if isinstance(after_data, dict) else {}
    if after is not None:
        payload["balance_info"] = [{"label": "魔方", "value": after}]
        payload["balance_before"] = before
    return {"success": True, "message": message, "data": payload}


# ==================== 平台引导代码, 无需修改 ====================
if __name__ == "__main__":
    import sys as _sys

    try:
        _config = json.loads(_sys.stdin.read() or "{}")
    except Exception:
        _config = {}
    try:
        _result = run(_config)
        if not isinstance(_result, dict):
            _result = {"success": False, "message": "run() 未返回字典"}
    except Exception as _exc:  # noqa: BLE001
        _result = {"success": False, "message": "脚本异常: %s" % _exc}
    print("__CHECKIN_RESULT__" + json.dumps(_result, ensure_ascii=False))
