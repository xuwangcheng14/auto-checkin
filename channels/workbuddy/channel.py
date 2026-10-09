#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
WorkBuddy 每日积分签到渠道 (纯标准库, 无第三方依赖, 支持多账号)。

账号来源(优先级):
  1. 任务配置 accounts: 每行一条 "ACCESS_TOKEN#UID#备注"
  2. 本机 WorkBuddy 客户端登录文件 workbuddy-desktop.info (自动探测)

说明:
  - accessToken 是 JWT, 约 60 天有效; 过期后对应账号报 401, 重新登录客户端取新值
  - 签到接口幂等, 重复执行不会重复发积分
  - 详细说明见 docs/WorkBuddy自动签到配置说明.md
"""
import base64
import datetime
import json
import os
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ENDPOINT_CHECKIN = "https://copilot.tencent.com/v2/billing/meter/daily-checkin"
ENDPOINT_RESOURCE = "https://copilot.tencent.com/v2/billing/meter/get-user-resource"
RESOURCE_BODY = {"PageNumber": 1, "PageSize": 100, "ProductCode": "p_tcaca",
                 "Status": [0, 3], "OnlyValidPeriod": True}
WXPUSHER_API = "https://wxpusher.zjiecode.com/api/send/message"

META = {
    "name": "WorkBuddy 每日积分签到",
    "version": "1.3.0",
    "author": "xuwangcheng",
    "description": "WorkBuddy(腾讯 CodeBuddy) 每日签到领积分。支持多账号; 留空账号配置时自动读取本机客户端登录文件。",
    "config_schema": [
        {
            "key": "accounts", "label": "账号列表", "type": "textarea", "required": False,
            "placeholder": "ACCESS_TOKEN#UID#备注\nACCESS_TOKEN#UID#备注",
            "help": "每行一个账号, 格式: ACCESS_TOKEN#UID#备注(备注可省略)。留空则自动读取本机 WorkBuddy 客户端登录文件。",
        },
        {"key": "dry_run", "label": "仅测试(不实际请求)", "type": "bool", "default": False},
        {"key": "wxpusher_app_token", "label": "WxPusher AppToken(可选)", "type": "password",
         "required": False, "help": "配置后签到结果推送到微信"},
        {"key": "wxpusher_uid", "label": "WxPusher UID(可选)", "type": "password", "required": False},
    ],
}


def log(msg):
    print("[%s] %s" % (datetime.datetime.now().strftime("%H:%M:%S"), msg), flush=True)


def token_file_candidates():
    env = os.environ
    return [c for c in [
        os.path.join(env.get("LOCALAPPDATA", ""), "CodeBuddyExtension", "Data", "Public", "auth",
                     "workbuddy-desktop.info"),
        os.path.join(env.get("APPDATA", ""), "CodeBuddyExtension", "Data", "Public", "auth",
                     "workbuddy-desktop.info"),
        os.path.expanduser("~/Library/Application Support/CodeBuddyExtension/Data/Public/auth/"
                           "workbuddy-desktop.info"),
        os.path.expanduser("~/.config/CodeBuddyExtension/Data/Public/auth/workbuddy-desktop.info"),
    ] if c]


def parse_account_line(line):
    """解析单行: ACCESS_TOKEN#UID#备注 -> dict。备注可省略, 可含 '#'。"""
    line = (line or "").strip()
    if not line:
        return None
    parts = line.split("#")
    token = parts[0].strip()
    uid = parts[1].strip() if len(parts) > 1 else ""
    remark = "#".join(parts[2:]).strip() if len(parts) > 2 else ""
    if not token or not uid:
        return None
    return {"token": token, "uid": uid, "remark": remark or "(未备注)"}


def load_accounts(config, notes=None):
    accounts = []
    for line in str(config.get("accounts") or "").splitlines():
        acc = parse_account_line(line)
        if acc:
            acc["src"] = "<任务配置>"
            accounts.append(acc)
    if accounts:
        return accounts

    for path in token_file_candidates():
        if not os.path.isfile(path):
            continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            log("读取登录文件失败 %s: %s" % (path, e))
            continue
        auth = data.get("auth", {}) or {}
        token = auth.get("accessToken")
        uid = (data.get("account", {}) or {}).get("uid")
        if isinstance(token, dict):
            log("登录文件令牌已被客户端加密(%s), 无法自动读取。" % path)
            log("请在任务配置「账号列表」中手动填写, 每行: ACCESS_TOKEN#UID#备注 (获取方法见渠道配置获取指南)")
            if notes is not None:
                notes.append("本机登录文件令牌已被客户端加密, 需手动填写账号")
            continue
        if token and uid:
            log("从本机登录文件加载账号: %s" % path)
            return [{"token": token, "uid": uid, "remark": "(本机文件)", "src": path}]
    return []


def decode_exp(token):
    """从 JWT 解析 exp 字段, 失败返回 None。"""
    try:
        part = token.split(".")[1]
        part += "=" * (-len(part) % 4)
        payload = json.loads(base64.urlsafe_b64decode(part).decode("utf-8", "replace"))
        return payload.get("exp")
    except Exception:
        return None


def post_json(url, token, uid, body_obj=None):
    data = b"{}" if body_obj is None else json.dumps(body_obj).encode("utf-8")
    headers = {
        "Authorization": "Bearer " + token,
        "Content-Type": "application/json",
        "X-User-Id": uid,
        "User-Agent": "WorkBuddyCheckin/1.4",
    }
    req = Request(url, data=data, headers=headers, method="POST")
    try:
        with urlopen(req, timeout=25) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except URLError as e:
        return 0, "URLError: %s" % e


def parse_result(status, body):
    code = None
    msg = ""
    awarded = None
    streak = None
    try:
        obj = json.loads(body)
        code = obj.get("code")
        msg = obj.get("msg") or obj.get("message") or ""
        data = obj.get("data") or {}
        if isinstance(data, dict):
            if data.get("credit") is not None:
                try:
                    awarded = int(data.get("credit"))
                except (TypeError, ValueError):
                    awarded = None
            if data.get("streak_days") is not None:
                try:
                    streak = int(data.get("streak_days"))
                except (TypeError, ValueError):
                    streak = None
    except ValueError:
        pass

    if status == 200 and code in (0, None):
        extra = (" (连续 %d 天)" % streak) if streak is not None else ""
        return True, "签到成功" + extra + ((" | " + msg) if msg else ""), awarded
    if code == 10001 or "已签到" in body or "already" in body.lower():
        return True, "今日已签到(幂等命中)", 0
    if status == 401:
        return False, "令牌失效(401), 请重新登录 WorkBuddy 客户端并更新账号配置", None
    return False, "签到失败 status=%s code=%s msg=%s" % (status, code, msg), None


def query_balance(token, uid):
    status, body = post_json(ENDPOINT_RESOURCE, token, uid, RESOURCE_BODY)
    if status != 200:
        return None
    try:
        obj = json.loads(body)
        accts = (obj.get("data", {}) or {}).get("Response", {}) or {}
        accts = (accts.get("Data", {}) or {}).get("Accounts", []) or []
        total = 0.0
        for a in accts:
            v = a.get("CycleCapacityRemainPrecise")
            for fallback in ("CycleCapacityRemain", "CapacityRemainPrecise", "CapacityRemain"):
                if v is None:
                    v = a.get(fallback)
            try:
                total += float(v)
            except (TypeError, ValueError):
                pass
        return int(round(total))
    except (ValueError, AttributeError):
        return None


def run_account(acc, dry):
    remark, token, uid = acc["remark"], acc["token"], acc["uid"]

    exp = decode_exp(token)
    expires_at = (datetime.datetime.fromtimestamp(exp).strftime("%Y-%m-%d %H:%M:%S")
                  if exp else None)
    if exp:
        remain = datetime.datetime.fromtimestamp(exp) - datetime.datetime.now()
        log("账号[%s] 已加载(uid=%s, 令牌剩余 ~%s)" % (remark, uid, str(remain).split(".")[0]))

    if dry:
        log("账号[%s] [dry-run] 跳过实际请求" % remark)
        return {"remark": remark, "ok": None, "msg": "[dry-run] 未执行",
                "before": None, "after": None, "gained": None,
                "expires_at": expires_at}

    before = query_balance(token, uid)
    log("账号[%s] 签到前总积分: %s" % (remark, "未知" if before is None else before))

    status, body = post_json(ENDPOINT_CHECKIN, token, uid)
    ok, msg, awarded = parse_result(status, body)
    log("账号[%s] %s %s" % (remark, "✅" if ok else "❌", msg))

    after = query_balance(token, uid)
    log("账号[%s] 签到后总积分: %s" % (remark, "未知" if after is None else after))

    if awarded is not None:
        gained = awarded
    elif before is not None and after is not None:
        gained = after - before
    else:
        gained = None
    return {"remark": remark, "ok": ok, "msg": msg, "before": before, "after": after,
            "gained": gained, "expires_at": expires_at}


def wxpusher_send(config, title, content):
    app_token = str(config.get("wxpusher_app_token") or "").strip()
    uid = str(config.get("wxpusher_uid") or "").strip()
    if not app_token or not uid:
        return
    try:
        payload = {"appToken": app_token, "uids": [uid], "topicIds": [],
                   "summary": title, "content": content.replace("\n", "<br>"),
                   "contentType": 1, "verifyPay": False}
        req = Request(WXPUSHER_API, data=json.dumps(payload).encode("utf-8"),
                      headers={"Content-Type": "application/json;charset=utf-8"}, method="POST")
        with urlopen(req, timeout=10) as resp:
            rj = json.loads(resp.read().decode("utf-8", "replace"))
        log("WxPusher 推送%s" % ("成功" if rj.get("code") == 1000 else "失败: %s" % rj.get("msg")))
    except Exception as e:
        log("WxPusher 推送异常: %s" % e)


def run(config):
    dry = bool(config.get("dry_run"))
    notes = []
    accounts = load_accounts(config, notes)
    if not accounts:
        message = "未找到任何账号"
        if notes:
            message += ": " + "; ".join(notes)
        else:
            message += ": 请在任务配置中填写账号列表, 或在本机登录 WorkBuddy 客户端"
        return {"success": False, "message": message}

    log("共加载 %d 个账号, 开始签到%s" % (len(accounts), " [dry-run]" if dry else ""))
    results = [run_account(acc, dry) for acc in accounts]

    ok_count = sum(1 for r in results if r.get("ok") is True)
    fail_count = sum(1 for r in results if r.get("ok") is False)

    date_str = datetime.date.today().strftime("%Y-%m-%d")
    if dry:
        title = "WorkBuddy 签到 [dry-run] (%d个账号)" % len(results)
    elif fail_count == 0:
        title = "WorkBuddy 签到全部成功 (%d/%d)" % (ok_count, len(results))
    else:
        title = "WorkBuddy 签到 %d成功 %d失败" % (ok_count, fail_count)

    # 状态消息附带各账号当前积分(签到后), 界面任务列表直接可见
    if not dry:
        if len(results) == 1:
            after = results[0]["after"]
            title += " | 当前积分 %s" % ("未知" if after is None else after)
        else:
            bal = " · ".join("%s: %s" % (r["remark"], "未知" if r["after"] is None else r["after"])
                             for r in results)
            title += " | " + bal

    lines = ["📅 %s 每日签到 (共 %d 个账号)" % (date_str, len(results)), "─" * 28]
    for r in results:
        mark = "✅" if r.get("ok") is True else "➖" if r.get("ok") is None else "❌"
        lines.append("【%s】%s %s" % (r["remark"], mark, r["msg"]))
        if r.get("dry"):
            continue
        lines.append("  总积分(前→后): %s → %s" % (
            "未知" if r["before"] is None else r["before"],
            "未知" if r["after"] is None else r["after"]))
        if r["gained"] is not None:
            lines.append("  本次获得: %s" % ("0 (已签到/无增量)" if r["gained"] == 0 else "+%d" % r["gained"]))
    content = "\n".join(lines)
    log("\n" + content)
    if not dry:
        wxpusher_send(config, title, content)

    # 上报各账号令牌有效期与积分余额, 平台在任务列表对应列展示
    expires_info = [{"label": r["remark"], "expires_at": r["expires_at"]}
                    for r in results if r.get("expires_at")]
    balance_info = [{"label": r["remark"], "value": r["after"]}
                    for r in results if not r.get("dry") and r.get("after") is not None]

    return {
        "success": fail_count == 0,
        "message": title,
        "data": {"total": len(results), "ok": ok_count, "fail": fail_count,
                 "expires_info": expires_info, "balance_info": balance_info},
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
