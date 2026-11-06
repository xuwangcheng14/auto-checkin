#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
商汤小浣熊(xiaohuanxiong.com) 每日积分渠道 (纯标准库, 无第三方依赖, 支持多账号)。

原理 (2026-10-27 实测):
  小浣熊没有独立的"签到按钮", 每日积分在**当日首次登录**时由服务端发放:
    触发: GET /api/web/office/v3/setting_info
    判定: 响应 data.point_grant_popups 非空 = 本次新发放; 为空 = 今日已领过
    明细: GET /api/web/points/v2/bills 里的 biz_type=daily_grant (每天 23:59:59 清零)
  本渠道流程: 查签到前积分 -> 调 setting_info 触发当日发放 -> 再查积分, 展示前后变化。

凭证:
  - 只需要一个访问令牌(JWT), 通过任务配置传入, 不依赖 9router / 客户端 / 任何本机环境
  - 令牌约 30 天过期且无 refresh; 到期后任务报"凭证无效或已过期"
  - 如令牌来自浏览器 Cookie(xhx_token), 脚本会自动调一次 iam/confirm 激活
  - 签到接口幂等, 同一天重复执行不会重复发积分

配置获取方法见本目录 README.md。
"""
import base64
import datetime
import json
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

BASE = "https://xiaohuanxiong.com"
PATH_BALANCE = "/api/web/points/v1/balance"
PATH_SETTING_INFO = "/api/web/office/v3/setting_info"
PATH_BILLS = "/api/web/points/v2/bills?paging.limit=50&type=all"
PATH_IAM_CONFIRM = "/api/web/auth/v1/iam/confirm"
PATH_ACCOUNT = "/api/iam/v1/account"

META = {
    "name": "商汤小浣熊 每日积分",
    "version": "1.1.0",
    "author": "xuwangcheng",
    "description": "商汤小浣熊(xiaohuanxiong.com)每日登录积分: 调 setting_info 触发当日发放, "
                   "返回签到前后可用积分。只需在任务里填访问令牌, 支持多账号。",
    "config_schema": [
        {
            "key": "token",
            "label": "访问令牌",
            "type": "password",
            "required": False,
            "placeholder": "粘贴 JWT 令牌(获取方法见「配置获取指南」)",
            "help": "单账号填这里; 约 30 天过期, 过期后任务报 401, 重新获取并更新即可",
        },
        {
            "key": "accounts",
            "label": "账号列表(多账号)",
            "type": "textarea",
            "required": False,
            "placeholder": "TOKEN#备注\nTOKEN#备注",
            "help": "每行一个账号, 格式 TOKEN#备注(备注可省略)。可与上方单账号令牌同时使用, 同一 token 自动去重",
        },
        {
            "key": "dry_run",
            "label": "仅测试(不实际请求)",
            "type": "bool",
            "default": False,
        },
    ],
}


def log(msg):
    print("[%s] %s" % (time.strftime("%H:%M:%S"), msg), flush=True)


def mask(token):
    """日志里只显示令牌前 6 位, 避免泄露。"""
    token = str(token or "")
    return ("%s...(%d 字符)" % (token[:6], len(token))) if token else "(空)"


def as_bool(value, default=False):
    """配置项可能是 bool 或 "true"/"false" 字符串, 统一归一化。"""
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return str(value).strip().lower() in ("1", "true", "yes", "on", "y")


# ---------------------------------------------------------------- 账号加载

def normalize_token(raw):
    """归一化用户粘贴的令牌, 兼容常见复制方式。

    支持: 纯 JWT / 带首尾引号 / "Bearer xxx" / 整段 Cookie 里的 xhx_token=xxx;
    返回纯令牌字符串。
    """
    token = str(raw or "").strip().strip('"').strip("'").strip()
    if not token:
        return ""
    # 整段 Cookie 或 "xhx_token=eyJ...; path=/" 片段
    if "xhx_token=" in token:
        token = token.split("xhx_token=", 1)[1].split(";", 1)[0].strip()
    # Authorization: Bearer xxx
    if token.lower().startswith("bearer "):
        token = token[7:].strip()
    return token.strip().strip('"').strip("'")


def parse_account_line(line, index):
    """解析单行: TOKEN#备注 -> dict。备注可省略, 可含 '#'。"""
    line = (line or "").strip()
    if not line:
        return None
    token, _, remark = line.partition("#")
    token = normalize_token(token)
    if not token:
        return None
    return {"token": token, "remark": remark.strip() or "账号%d" % index}


def load_accounts(config):
    """读取任务配置中的单账号令牌与账号列表, 按 token 去重, 保持顺序。"""
    accounts = []
    token = normalize_token(config.get("token"))
    if token:
        accounts.append({"token": token, "remark": "默认账号"})

    for i, line in enumerate(str(config.get("accounts") or "").splitlines(), 1):
        acc = parse_account_line(line, i)
        if acc:
            accounts.append(acc)

    seen = set()
    unique = []
    for acc in accounts:
        if acc["token"] in seen:
            continue
        seen.add(acc["token"])
        unique.append(acc)
    return unique


# ---------------------------------------------------------------- HTTP

def http_json(method, path, token, body=None, timeout=25, retries=2):
    """请求小浣熊接口, 网络错误/5xx 最多重试 retries 次(退避 2s/4s)。"""
    url = BASE + path
    data = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {
        "Accept": "application/json",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/153.0.0.0 Safari/537.36",
        "Authorization": "Bearer " + token,
    }
    if data is not None:
        headers["Content-Type"] = "application/json"

    status, text = 0, ""
    for attempt in range(retries + 1):
        req = Request(url, data=data, headers=headers, method=method)
        try:
            with urlopen(req, timeout=timeout) as resp:
                return resp.status, resp.read().decode("utf-8", "replace")
        except HTTPError as e:
            status = e.code
            text = e.read().decode("utf-8", "replace")
        except URLError as e:
            status = 0
            text = "URLError: %s" % e
        except Exception as e:  # noqa: BLE001  (超时等)
            status = 0
            text = "%s: %s" % (type(e).__name__, e)

        if status != 0 and status < 500:
            return status, text
        if attempt < retries:
            wait = 2 ** (attempt + 1)
            log("请求异常(%s), %d 秒后重试..." % (text[:120], wait))
            time.sleep(wait)
    return status, text


def api_call(method, path, token, body=None):
    """带 iam/confirm 自动激活: 浏览器 Cookie 令牌首次调用会返回 iam_confirm_required。"""
    status, text = http_json(method, path, token, body)
    if status == 400 and "iam_confirm_required" in text:
        log("令牌需要先激活(iam_confirm_required), 正在调用 iam/confirm...")
        c_status, _ = http_json("POST", PATH_IAM_CONFIRM, token,
                                {"registration_attribution": {"registration_source": "web"}})
        if c_status == 200:
            status, text = http_json(method, path, token, body)
        else:
            log("iam/confirm 失败(HTTP %s)" % c_status)
    return status, text


def parse_body(text):
    try:
        obj = json.loads(text)
    except ValueError:
        return None
    return obj if isinstance(obj, dict) else None


def fetch_balance(token):
    """返回 (data, err)。err=UNAUTHORIZED 表示凭证失效。"""
    status, text = api_call("GET", PATH_BALANCE, token)
    if status in (401, 403):
        return None, "UNAUTHORIZED"
    if status != 200:
        return None, "HTTP %s: %s" % (status, text[:150])
    obj = parse_body(text)
    if obj is None:
        return None, "响应不是合法 JSON"
    if obj.get("code") not in (0, "OK", None):
        return None, str(obj.get("message") or obj.get("code"))[:150]
    data = obj.get("data")
    return (data if isinstance(data, dict) else {}), ""


def trigger_daily(token):
    """调 setting_info 触发当日登录积分。返回 (data, err)。"""
    status, text = api_call("GET", PATH_SETTING_INFO, token)
    if status in (401, 403):
        return None, "UNAUTHORIZED"
    if status != 200:
        return None, "HTTP %s: %s" % (status, text[:150])
    obj = parse_body(text)
    if obj is None:
        return None, "响应不是合法 JSON"
    if obj.get("code") not in (0, "OK", None):
        return None, str(obj.get("message") or obj.get("code"))[:150]
    data = obj.get("data")
    return (data if isinstance(data, dict) else {}), ""


def today_daily_granted(token):
    """从积分明细查今天(按 UTC+8)的 daily_grant 合计; 查不到返回 None。"""
    status, text = api_call("GET", PATH_BILLS, token)
    if status != 200:
        return None
    obj = parse_body(text)
    if obj is None:
        return None
    items = ((obj.get("data") or {}).get("items")) or []
    today = (datetime.datetime.now(datetime.timezone.utc)
             + datetime.timedelta(hours=8)).strftime("%Y-%m-%d")
    total = 0
    for it in items:
        if not isinstance(it, dict):
            continue
        if str(it.get("created_at") or "").startswith(today) and it.get("biz_type") == "daily_grant":
            try:
                total += int(it.get("points") or 0)
            except (TypeError, ValueError):
                pass
    return total


def fetch_account_name(token):
    """取昵称 + 脱敏手机号(尽力而为), 失败返回空串。"""
    status, text = api_call("GET", PATH_ACCOUNT, token)
    if status != 200:
        return ""
    obj = parse_body(text) or {}
    user = (obj.get("data") or {}).get("user") or {}
    parts = [str(user.get("name") or "").strip(), str(user.get("phone") or "").strip()]
    return " · ".join(p for p in parts if p)


def parse_jwt_exp(token):
    """解析 JWT 的 exp, 失败返回 None。"""
    try:
        part = str(token).split(".")[1]
        part += "=" * (-len(part) % 4)
        payload = json.loads(base64.urlsafe_b64decode(part).decode("utf-8", "replace"))
        exp = payload.get("exp")
        return int(exp) if exp else None
    except Exception:  # noqa: BLE001
        return None


def format_exp(exp):
    if not exp:
        return None
    try:
        return datetime.datetime.fromtimestamp(exp).strftime("%Y-%m-%d %H:%M:%S")
    except (OSError, OverflowError, ValueError):
        return None


# ---------------------------------------------------------------- 单个账号

def run_account(acc, dry):
    token, remark = acc["token"], acc["remark"]
    exp = parse_jwt_exp(token)
    expires_at = format_exp(exp)
    if exp:
        remain = datetime.datetime.fromtimestamp(exp) - datetime.datetime.now()
        log("账号[%s] 令牌 %s, 剩余 ~%s" % (remark, mask(token), str(remain).split(".")[0]))

    result = {
        "remark": remark, "ok": None, "msg": "", "before": None, "after": None,
        "gained": None, "expires_at": expires_at, "dry": bool(dry),
    }
    if dry:
        result["msg"] = "[dry-run] 已加载, 未实际请求"
        return result

    before, err = fetch_balance(token)
    if err == "UNAUTHORIZED":
        result["ok"] = False
        result["msg"] = "凭证无效或已过期(HTTP 401), 请按配置获取指南重新获取并更新任务"
        return result
    if before is None:
        log("账号[%s] 签到前积分查询失败(%s), 继续触发签到" % (remark, err))
    else:
        log("账号[%s] 签到前可用积分: %s (每日 %s / 奖励 %s)" % (
            remark, before.get("available_points"), before.get("daily_points"),
            before.get("reward_points")))

    trig, err = trigger_daily(token)
    if err == "UNAUTHORIZED":
        result["ok"] = False
        result["msg"] = "凭证无效或已过期(HTTP 401), 请按配置获取指南重新获取并更新任务"
        return result
    if trig is None:
        result["ok"] = False
        result["msg"] = "触发签到失败(%s)" % err
        return result

    popups = trig.get("point_grant_popups")
    popups = popups if isinstance(popups, list) else []
    granted = []
    for p in popups:
        if not isinstance(p, dict):
            continue
        points = p.get("points") or p.get("amount") or 0
        try:
            points = int(points)
        except (TypeError, ValueError):
            points = 0
        if points:
            granted.append((str(p.get("source") or p.get("type") or "积分"), points))

    after, err2 = fetch_balance(token)
    if after is None:
        log("账号[%s] 签到后积分查询失败(%s)" % (remark, err2 or "未知"))
    else:
        log("账号[%s] 签到后可用积分: %s" % (remark, after.get("available_points")))

    before_av = before.get("available_points") if before else None
    after_av = after.get("available_points") if after else None
    try:
        delta = int(after_av) - int(before_av)
    except (TypeError, ValueError):
        delta = None

    if delta is not None and delta > 0:
        names = ", ".join("%s+%d" % (src, pts) for src, pts in granted)
        msg = "签到成功, 本次获得 +%d 积分%s, 当前可用 %s" % (
            delta, (" (%s)" % names) if names else "", after_av)
        result["gained"] = delta
    elif granted:
        gained = sum(pts for _, pts in granted)
        msg = "签到成功, 本次获得 +%d 积分, 当前可用 %s" % (
            gained, after_av if after_av is not None else "未知")
        result["gained"] = gained
    elif popups:
        msg = "已触发积分发放(弹窗 %d 条), 当前可用 %s" % (
            len(popups), after_av if after_av is not None else "未知")
        result["gained"] = 0
    else:
        today_sum = today_daily_granted(token)
        if today_sum:
            msg = "今日已签到(今日每日积分已发 %s), 当前可用 %s" % (
                today_sum, after_av if after_av is not None else "未知")
        else:
            msg = "签到完成, 今日暂无新发放(可能已领过), 当前可用 %s" % (
                after_av if after_av is not None else "未知")
        result["gained"] = 0

    if after_av is not None:
        result["after"] = after_av
    if before_av is not None:
        result["before"] = before_av
    result["ok"] = True
    result["msg"] = msg
    who = fetch_account_name(token)
    if who:
        log("账号[%s] 账号信息: %s" % (remark, who))
    log("账号[%s] %s" % (remark, msg))
    return result


# ---------------------------------------------------------------- 入口

def run(config):
    dry = as_bool(config.get("dry_run"))
    accounts = load_accounts(config)
    if not accounts:
        return {
            "success": False,
            "message": "未找到任何账号: 请在任务配置中填写「访问令牌」或「账号列表」"
                       "(获取方法见配置获取指南)",
        }

    log("共加载 %d 个账号%s" % (len(accounts), " [dry-run]" if dry else ""))
    for i, acc in enumerate(accounts, 1):
        log("  %d) %s | %s" % (i, acc["remark"], mask(acc["token"])))

    results = []
    for i, acc in enumerate(accounts):
        try:
            results.append(run_account(acc, dry))
        except Exception as e:  # noqa: BLE001  单账号异常不影响其他账号
            results.append({"remark": acc["remark"], "ok": False, "msg": "执行异常: %s" % e,
                            "before": None, "after": None, "gained": None,
                            "expires_at": None, "dry": dry})
        if i < len(accounts) - 1:
            time.sleep(0.8)  # 账号之间稍作间隔, 避免连续请求被风控

    ok_count = sum(1 for r in results if r.get("ok") is True)
    fail_count = sum(1 for r in results if r.get("ok") is False)
    gained_total = sum(r.get("gained") or 0 for r in results if r.get("ok") is True)

    if dry:
        message = "[dry-run] 已加载 %d 个账号, 未实际请求" % len(results)
    elif len(results) == 1:
        message = results[0]["msg"]
    elif fail_count == 0:
        brief = " | ".join("%s: %s" % (r["remark"], r["msg"].split(" | ")[0][:60]) for r in results)
        message = "全部签到成功 (%d/%d), 共获得 %d 积分 | %s" % (
            ok_count, len(results), gained_total, brief)
    else:
        brief = " | ".join("%s: %s" % (r["remark"], ("成功" if r.get("ok") else r["msg"])[:60])
                           for r in results)
        message = "签到 %d 成功 / %d 失败 | %s" % (ok_count, fail_count, brief)

    if dry:
        log(message)
    else:
        lines = ["📅 %s 小浣熊每日积分 (共 %d 个账号)" % (
            datetime.date.today().strftime("%Y-%m-%d"), len(results)), "─" * 28]
        for r in results:
            mark = "✅" if r.get("ok") is True else "➖" if r.get("ok") is None else "❌"
            lines.append("【%s】%s %s" % (r["remark"], mark, r["msg"]))
            if r.get("before") is not None or r.get("after") is not None:
                lines.append("  可用积分(前→后): %s → %s" % (
                    "未知" if r["before"] is None else r["before"],
                    "未知" if r["after"] is None else r["after"]))
        log("\n" + "\n".join(lines))

    expires_info = [{"label": r["remark"], "expires_at": r["expires_at"]}
                    for r in results if r.get("expires_at")]
    balance_info = [{"label": r["remark"], "value": r["after"]}
                    for r in results if not r.get("dry") and r.get("after") is not None]

    return {
        "success": fail_count == 0,
        "message": message[:500],
        "data": {
            "total": len(results), "ok": ok_count, "fail": fail_count,
            "gained": gained_total,
            "expires_info": expires_info,
            "balance_info": balance_info,
        },
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
