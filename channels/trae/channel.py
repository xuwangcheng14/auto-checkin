#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
Trae CN(trae.cn / work.trae.cn) 每日积分签到 —— 网页 Cookie 模式。

原理(逆向自官方网页端, 参考实现见本目录 README「参考实现」一节):
  1. 用浏览器登录后的完整 Cookie 调 CheckLogin, 确认会话有效(IsLogin);
  2. 调 GetUserToken 把网页会话换成短期 JWT(约 8 小时), 之后用 Authorization: Cloud-IDE-JWT <jwt>;
  3. 查 /trae/api/v2/ug/checkin_credits/status: 今日已签到则直接返回成功(幂等);
  4. 未签到才调 /trae/api/v2/ug/checkin_credits/claim 领取每日积分, 并回查状态确认;
  5. 顺带查实时积分余额与 Cookie 到期时间, 上报给平台(剩余积分 / 令牌有效期列)。

为什么用网页 Cookie 而不是客户端凭据:
  - 客户端模式要读取客户端本地加密存储 + 真实设备号(x-device-id), 只能在装了 Trae
    客户端并登录的那台电脑上跑; 网页 Cookie 模式在任意服务器上都能跑;
  - 服务端按设备号限制「一台设备每天只能签到一个账号」, 网页模式不受此限制。

所有接口都走 https://api.trae.cn(纯 requests 即可, 不会被字节 WAF 拦截);
只有误配成 www.trae.cn 时才会遇到验证页, 此时脚本会提示安装 curl_cffi 兜底重试。

配置获取方法: 见本目录 README.md(配置获取指南)。
注意事项:
  - Cookie 是登录凭证, 名义有效期约 60 天(写在 sid_guard 里), 但服务端可提前作废
    (重新登录/登出/改密都会立即失效), 失效后重新登录网页版复制一份即可;
  - 一个任务对应一个账号; 多账号请新建多个任务。
"""
import json
import os
import re
import time
import urllib.parse
from datetime import datetime

import requests

try:  # 可选依赖: 仅当误把接口域名配成 www.trae.cn、被 WAF 挑战页拦截时才需要
    from curl_cffi import requests as _curl_requests
except Exception:  # noqa: BLE001
    _curl_requests = None

# ==================== 常量 ====================
DEFAULT_WEB_BASE = "https://www.trae.cn"      # 网页版域名(Origin/Referer 用, 同时也是提示里的登录地址)
DEFAULT_API_BASE = "https://api.trae.cn"      # API 域名: 全部接口都可以直连, 无 WAF 挑战

CHECK_LOGIN_PATH = "/cloudide/api/v3/trae/CheckLogin"
GET_USER_TOKEN_PATH = "/cloudide/api/v3/common/GetUserToken"
STATUS_PATH = "/trae/api/v2/ug/checkin_credits/status"
CLAIM_PATH = "/trae/api/v2/ug/checkin_credits/claim"
# 余额接口: 网页谱系是 web_user_ent_usage, 客户端谱系是 ide_user_ent_usage, 依次尝试
USAGE_PATHS = ("/trae/api/v2/pay/web_user_ent_usage",
               "/trae/api/v2/pay/ide_user_ent_usage")

# 签到/领取接口的请求体: 官方没有公开文档, 各产品谱系用的 req_source 不同
#   {}              早期官方包的原样契约(网页版脚本 ScriptCat#8790 用它)
#   {"req_source":3} 网页谱系(devilardis/auto-checkin 用它)
#   {"req_source":1} 客户端 TRAE_CN 谱系
# 默认(配置 req_source=0)按这个顺序探测一次状态接口, 命中后领取接口沿用同一份请求体。
REQ_BODY_CANDIDATES = ({}, {"req_source": 3}, {"req_source": 1})

USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/153.0.0.0 Safari/537.36")

AUTH_FAIL_CODES = (1001, 1002, 401, 403)      # 登录态失效的业务码
CODE_ALREADY = (10001,)                       # 今日已签到(会伴随 HTTP 400 返回)
CODE_DEVICE_TAKEN = 9095                       # 该设备号今天已被别的账号用掉(账号维度还没签)
CODE_DEVICE_BAD = 9074                         # 设备号未被服务端认可(文案伪装成「当前参与用户太多」)
CODE_PARAM_MISSING = 9004                      # 领取需要 x-device-id, 没带就是参数错误
CLAIM_RETRY_DELAYS = (5, 15)                  # 领取被拒时的退避秒数(有界, 最多再试 2 次)
CLAIM_MAX_ATTEMPTS = 4                        # 领取接口最多请求次数(含换请求体变体)
REQUEST_TIMEOUT = 25

# 设备号文件: 由 tools/trae_device.py 在装有 Trae 客户端的机器上生成(平台会自动读取)
DEVICE_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), "data", "trae_device.json")

META = {
    "name": "Trae CN 每日签到",
    "version": "1.2.1",
    "author": "xuwangcheng",
    "description": "Trae CN(trae.cn, 字节的 AI IDE)每日签到领积分: 网页 Cookie 换 JWT + 本机真实设备号, "
                   "先查状态未签到才领取(幂等安全), 并上报积分余额与 Cookie 到期时间。仅国内版 trae.cn, "
                   "国际版 trae.ai 无签到活动。",
    "config_schema": [
        {
            "key": "cookie",
            "label": "网页 Cookie",
            "type": "password",
            "required": True,
            "placeholder": "登录 www.trae.cn 后 F12 复制完整 Cookie 粘贴到这里",
            "help": "必须是从 trae.cn 页面请求里复制的完整 Cookie(含 X-Cloudide-Session、sessionid 等); "
                    "抓取步骤见「配置获取指南」。失效后任务会提示重新抓取",
        },
        {
            "key": "device_id",
            "label": "设备号",
            "type": "text",
            "default": "",
            "required": False,
            "placeholder": "留空则自动读 data/trae_device.json；也可直接粘贴 16 位设备号",
            "help": "领取积分必须带本机真实设备号: 在装有 Trae 客户端的电脑上执行 "
                    "python tools/trae_device.py 生成 data/trae_device.json(或把里面 device_id 粘到这里)。"
                    "同一设备号每天只能签到 1 个账号",
        },
        {
            "key": "dry_run",
            "label": "仅查询(不领取)",
            "type": "bool",
            "default": False,
            "help": "打开后只验证 Cookie 是否有效、今日是否已签到, 不调用领取接口(不需要设备号)",
        },
        {
            "key": "req_source",
            "label": "请求来源标识(高级)",
            "type": "number",
            "default": 0,
            "help": "0=自动探测(推荐), 3=网页谱系, 1=客户端谱系; 一般无需修改",
        },
        {
            "key": "api_base",
            "label": "API 域名(高级)",
            "type": "text",
            "default": DEFAULT_API_BASE,
            "help": "默认 https://api.trae.cn; 一般无需修改",
        },
        {
            "key": "web_base",
            "label": "网页域名(高级)",
            "type": "text",
            "default": DEFAULT_WEB_BASE,
            "help": "默认 https://www.trae.cn, 仅作 Origin/Referer 与提示用; 一般无需修改",
        },
    ],
}


class TraeError(Exception):
    """签到流程中的可预期错误(带业务码/HTTP 状态, 便于给出可读提示)。"""

    def __init__(self, message, code=None, http_status=None):
        Exception.__init__(self, message)
        self.message = message
        self.code = code
        self.http_status = http_status

    def __str__(self):
        return self.message


# ==================== 小工具 ====================
def log(msg):
    print("[%s] %s" % (time.strftime("%H:%M:%S"), msg), flush=True)


def mask(value, keep=6):
    """凭证/账号脱敏: 只保留前 keep 位。"""
    text = str(value or "")
    return text if len(text) <= keep else text[:keep] + "..." + "(共%d位)" % len(text)


def _maybe_json(text):
    text = (text or "").strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except ValueError:
        return None


def _is_html(text, content_type):
    ct = (content_type or "").lower()
    if "text/html" in ct:
        return True
    head = (text or "").lstrip()[:200].lower()
    return head.startswith("<!doctype") or head.startswith("<html")


def _clean_amount(value):
    """积分数值: 整数转 int, 否则保留两位小数(避免 1234.56789012 这种展示)。"""
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return value
    if abs(value - round(value)) < 1e-6:
        return int(value)
    return round(float(value), 2)


def _first_number(*values):
    for v in values:
        if isinstance(v, bool):
            continue
        if isinstance(v, (int, float)):
            return _clean_amount(v)
        if isinstance(v, str):
            try:
                return _clean_amount(float(v))
            except ValueError:
                continue
    return None


def _normalize_cookie(raw):
    """把用户粘贴的内容整理成合法的 Cookie 请求头值。

    兼容三种粘贴方式:
      1. 直接粘贴 Cookie 值(正常情况);
      2. 连同 "Cookie:" 前缀一起粘贴;
      3. 把整段 Request Headers 复制进来(从中挑出 cookie 行, 遇到下一个请求头就停止)。
    多行内容会被拼成一行(浏览器内换行会破坏 Cookie 语法)。
    """
    text = str(raw or "").strip().strip('"').strip("'").strip()
    if not text:
        return ""
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    start = None
    for index, line in enumerate(lines):
        if re.match(r"(?i)^cookie\s*:\s*", line):
            start = index
            break
    if start is not None:
        chunk = [re.sub(r"(?i)^cookie\s*:\s*", "", lines[start])]
        for line in lines[start + 1:]:
            if re.match(r"^[A-Za-z][A-Za-z0-9-]*\s*:", line):  # 下一行是别的请求头, 结束
                break
            chunk.append(line)
        text = " ".join(part for part in chunk if part)
    else:
        text = "; ".join(lines)
    text = re.sub(r";\s*;+", ";", text)
    return text.strip().strip(";").strip()


def _cookie_dict(cookie):
    data = {}
    for part in str(cookie or "").split(";"):
        key, sep, value = part.strip().partition("=")
        if sep and key:
            data[key.strip()] = value.strip()
    return data


def _cookie_expires_at(cookie):
    """从 sid_guard 解析会话「名义」到期时间(epoch 秒)。

    sid_guard 形如: <sessionid>|<签发时间戳>|<有效期秒数>|<GMT 日期字符串>
    (值经过 URL 编码, 例如 15-Nov-2026+13%3A40%3A00+GMT)。
    注意这是 Cookie 的名义有效期, 服务端可能因为重新登录/登出/改密提前作废。
    """
    raw = _cookie_dict(cookie).get("sid_guard")
    if not raw:
        return None
    text = urllib.parse.unquote_plus(raw)
    parts = [p for p in text.split("|") if p]
    if len(parts) >= 3:
        try:
            return int(parts[1]) + int(parts[2])
        except ValueError:
            pass
    for part in parts:
        try:
            return int(datetime.strptime(part.replace("+", " ").strip(),
                                         "%a, %d %b %Y %H:%M:%S %Z").timestamp())
        except ValueError:
            continue
    return None


def _format_ts(ts):
    try:
        return datetime.fromtimestamp(int(ts)).strftime("%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError, OSError):
        return None


def _mask_device(device):
    """设备号脱敏展示: 1335****0736。"""
    text = str(device or "")
    return text if len(text) <= 8 else text[:4] + "****" + text[-4:]


def _load_device_id(config_value):
    """取设备号: 任务配置优先, 其次读 tools/trae_device.py 生成的 data/trae_device.json。"""
    value = str(config_value or "").strip().strip('"').strip("'")
    if value and value.lower() not in ("auto", "file"):
        return value, "任务配置"
    try:
        with open(DEVICE_FILE, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return "", ""
    device = str(data.get("device_id") or "").strip()
    if device:
        return device, "data/trae_device.json"
    for item in data.get("candidates") or []:
        if isinstance(item, dict):
            device = str(item.get("device_id") or "").strip()
            if device:
                return device, "data/trae_device.json"
    return "", ""


# ==================== Trae 网页会话客户端 ====================
class TraeClient(object):
    def __init__(self, cookie, web_base=DEFAULT_WEB_BASE, api_base=DEFAULT_API_BASE,
                 device_id="", timeout=REQUEST_TIMEOUT):
        self.cookie = cookie
        self.web_base = (web_base or DEFAULT_WEB_BASE).rstrip("/")
        self.api_base = (api_base or DEFAULT_API_BASE).rstrip("/")
        self.device_id = str(device_id or "").strip()
        self.timeout = timeout
        self.region = ""
        self.jwt = ""
        self.jwt_expires_at = None
        self.pay_base = self.api_base   # CheckLogin 返回的 AIPayHost 优先

    # ---- 原始请求 ----
    def _post_raw(self, url, headers, body, use_curl=False):
        """返回 (status, text, content_type); 网络异常时 status=-1, text 为错误原因。"""
        try:
            if use_curl:
                resp = _curl_requests.post(url, json=body, headers=headers,
                                           impersonate="chrome", timeout=self.timeout)
            else:
                resp = requests.post(url, json=body, headers=headers, timeout=self.timeout)
        except Exception as exc:  # noqa: BLE001  网络层错误
            return -1, "%s: %s" % (type(exc).__name__, exc), ""
        return resp.status_code, resp.text or "", resp.headers.get("content-type", "")

    def _request(self, base, path, body, headers, retries=2):
        """带重试的 POST: 网络错误/5xx 退避重试, WAF 挑战页换 curl_cffi 再试。

        返回 (status, payload_or_None, text, content_type)
        """
        url = base.rstrip("/") + path
        text, ct, status = "", "", -1
        for attempt in range(retries + 1):
            status, text, ct = self._post_raw(url, headers, body)
            if status < 0:
                if attempt < retries:
                    log("  ! 请求 %s 失败(%s), 第 %d 次重试" % (path, text[:120], attempt + 1))
                    time.sleep(2 * (attempt + 1))
                    continue
                log("  ! 请求 %s 失败: %s" % (path, text[:160]))
                return status, None, text, ct
            if _is_html(text, ct):
                if _curl_requests is None:
                    log("  ! %s 返回了验证页(字节 WAF 挑战), 未安装 curl_cffi 无法自动绕过" % path)
                    return status, None, text, ct
                log("  * %s 被 WAF 挑战页拦截, 用 curl_cffi 模拟浏览器指纹重试" % path)
                status2, text2, ct2 = self._post_raw(url, headers, body, use_curl=True)
                if status2 >= 0 and not _is_html(text2, ct2):
                    status, text, ct = status2, text2, ct2
                else:
                    return status2, None, text2, ct2
            if 500 <= status < 600 and attempt < retries:
                log("  ! %s 返回 HTTP %s, 第 %d 次重试" % (path, status, attempt + 1))
                time.sleep(2 * (attempt + 1))
                continue
            return status, _maybe_json(text), text, ct
        return status, None, text, ct

    # ---- 请求头 ----
    def _web_headers(self, base):
        return {
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Content-Type": "application/json",
            "Origin": base,
            "Referer": base + "/",
            "User-Agent": USER_AGENT,
            "Cookie": self.cookie,
        }

    def _api_headers(self):
        headers = {
            "Authorization": "Cloud-IDE-JWT " + self.jwt,
            "Accept": "application/json, text/plain, */*",
            "Content-Type": "application/json",
            "Origin": self.web_base,
            "Referer": self.web_base + "/",
            "User-Agent": USER_AGENT,
        }
        if self.region:
            headers["X-User-Region"] = self.region
        if self.device_id:  # 网页模式默认不发; 仅当用户手工指定时带上(排查 9074 用)
            headers["x-device-id"] = self.device_id
        return headers

    # ---- cloudide 接口(cookie 鉴权): 优先 api 域名, 失败时才回退网页域名 ----
    def _cloudide(self, path, body, fallback_on_error=False):
        """调用 cloudide 接口并取出 Result。

        fallback_on_error=False: api 域名返回 JSON 错误时直接报错(CheckLogin 用它,
            IsLogin=false 就代表 Cookie 失效, 换域名也没用);
        fallback_on_error=True : api 域名返回错误时再试网页域名(GetUserToken 用它,
            某些网络环境下 api 域名可能被挡)。
        """
        last_error = None
        text, ct, status = "", "", -1
        for base in (self.api_base, self.web_base):
            status, payload, text, ct = self._request(base, path, body, self._web_headers(base))
            if payload is None:
                if _is_html(text, ct):
                    last_error = TraeError(
                        "被字节 WAF 拦截(返回验证页): 请把「API 域名」保持为 %s, "
                        "或安装 curl_cffi 后重试(pip install curl_cffi)" % DEFAULT_API_BASE,
                        http_status=status)
                else:
                    last_error = TraeError("接口无有效响应(HTTP %s): %s"
                                           % (status, (text or "")[:160]), http_status=status)
                log("  * %s 在 %s 上请求失败: %s" % (path, base, last_error.message[:120]))
                if base == self.api_base:
                    continue
                raise last_error
            err = payload.get("ResponseMetadata") if isinstance(payload, dict) else None
            err = (err or {}).get("Error") or {}
            if err:
                last_error = TraeError("接口返回错误 %s: %s"
                                       % (err.get("Code"), err.get("Message")),
                                       code=err.get("Code"), http_status=status)
            else:
                result = payload.get("Result") if isinstance(payload, dict) else None
                if isinstance(result, dict):
                    return result, status
                last_error = TraeError("响应缺少 Result 字段(HTTP %s): %s"
                                       % (status, (text or "")[:160]), http_status=status)
            if base == self.web_base or not fallback_on_error:
                raise last_error
            log("  * %s 在 %s 上返回错误(%s), 尝试备用域名..."
                % (path, base, last_error.message[:100]))
        raise last_error or TraeError("接口无有效响应(HTTP %s): %s" % (status, (text or "")[:160]))

    def check_login(self):
        """校验网页会话; 返回 Result 字典(含 IsLogin/UserID/Region/AIPayHost)。"""
        result, status = self._cloudide(CHECK_LOGIN_PATH,
                                        {"GetAIPayHost": True, "ReqSource": "Web"})
        if result.get("IsLogin") is not True:
            raise TraeError("Cookie 已失效(IsLogin=false, HTTP %s): 请重新登录 %s 后复制完整 Cookie"
                            % (status, self.web_base), http_status=status)
        self.region = str(result.get("Region") or "")
        pay_host = str(result.get("AIPayHost") or result.get("AIHost") or "").strip()
        if pay_host.startswith("http"):
            self.pay_base = pay_host.rstrip("/")
        return result

    def get_user_token(self):
        """用网页会话换 JWT; 返回 {token, expires_at(epoch 秒或 None)}。"""
        result, status = self._cloudide(GET_USER_TOKEN_PATH, {}, fallback_on_error=True)
        token = result.get("Token")
        if not token:
            raise TraeError("换取 JWT 失败(HTTP %s): 响应中没有 Token" % status,
                            http_status=status)
        expires_at = None
        raw_exp = result.get("ExpiredAt")
        if isinstance(raw_exp, (int, float)) and raw_exp > 0:
            expires_at = raw_exp / 1000.0 if raw_exp > 1e11 else float(raw_exp)
        elif isinstance(raw_exp, str) and raw_exp.strip():
            try:
                expires_at = datetime.fromisoformat(raw_exp.replace("Z", "+00:00")).timestamp()
            except ValueError:
                expires_at = None
        self.jwt = str(token)
        self.jwt_expires_at = expires_at
        return {"token": self.jwt, "expires_at": expires_at}

    # ---- 积分接口(JWT 鉴权) ----
    def api_call(self, path, body):
        """返回 (http_status, payload_dict); 网络/非 JSON 抛 TraeError。"""
        status, payload, text, ct = self._request(self.pay_base, path, body, self._api_headers())
        if payload is None:
            if status < 0:
                raise TraeError("网络请求失败: %s" % (text or "")[:160])
            if status in (401, 403):
                raise TraeError("登录态已失效(HTTP %s)" % status, http_status=status)
            if _is_html(text, ct):
                raise TraeError("接口返回验证页(HTTP %s), 请稍后重试" % status, http_status=status)
            raise TraeError("接口返回非 JSON(HTTP %s): %s" % (status, (text or "")[:160]),
                            http_status=status)
        return status, payload

    def checkin_status(self, body=None):
        status, payload = self.api_call(STATUS_PATH, {} if body is None else body)
        return _unwrap(payload), status, payload

    def claim(self, body=None):
        status, payload = self.api_call(CLAIM_PATH, {} if body is None else body)
        return _unwrap(payload), status, payload

    def usage(self):
        """实时积分余额: usage_summary 的「总额 - 已消耗」; 两个谱系的接口都试一次。"""
        last_error = None
        for path in USAGE_PATHS:
            try:
                _, payload = self.api_call(path, {"require_usage": True})
            except TraeError as exc:
                last_error = exc
                continue
            body = _unwrap(payload, keys=("usage_summary",))
            summary = body.get("usage_summary") or {}
            total = _clean_amount(summary.get("total_amount"))
            used = _clean_amount(summary.get("consumed_amount"))
            left = None
            if isinstance(total, (int, float)) and isinstance(used, (int, float)):
                left = _clean_amount(total - used)
            return {"credits": left, "credits_total": total, "credits_used": used}
        if last_error is not None:
            raise last_error
        return {}


def _unwrap(payload, keys=("checked_in", "did_checked_in", "enable", "credits")):
    """兼容两种返回结构: 扁平 {checked_in:...} 与包装 {code:0, data:{...}}。"""
    if not isinstance(payload, dict):
        return {}
    data = payload.get("data")
    if isinstance(data, dict) and any(k in data for k in keys):
        return data
    return payload


def _claimed_credits(raw_payload):
    """从领取响应里尽力提取「本次获得的积分」。

    优先取领取接口自己返回的数值(credits > extra_credits > add_credits > reward_credits
    > credited > points), 都没有时由调用方回退到 status.credits(每日奖励额)。
    注意: 实际到账是「取其一」而不是相加, 所以这里按优先级取第一个有效值。
    """
    if not isinstance(raw_payload, dict):
        return None
    body = _unwrap(raw_payload, keys=("credits", "extra_credits", "add_credits",
                                      "reward_credits", "credited", "points"))
    return _first_number(
        raw_payload.get("credits"), raw_payload.get("extra_credits"),
        raw_payload.get("add_credits"), raw_payload.get("reward_credits"),
        raw_payload.get("credited"), raw_payload.get("points"),
        body.get("credits"), body.get("extra_credits"), body.get("add_credits"),
        body.get("reward_credits"), body.get("credited"), body.get("points"))


# ==================== 签到主流程 ====================
def _session_invalid(_exc=None):
    return {"success": False,
            "message": "Cookie 已失效: 请重新登录 www.trae.cn 后按「配置获取指南」重新抓取 Cookie 并更新任务"}


def _status_error(body, raw):
    """status 接口业务码非 0 时返回可读错误; 正常返回 None。"""
    if not isinstance(raw, dict):
        return None
    code = raw.get("code")
    if code in (None, 0):
        return None
    message = raw.get("message") or raw.get("msg") or json.dumps(raw, ensure_ascii=False)[:160]
    if code in AUTH_FAIL_CODES:
        return "Cookie 已失效(code=%s): 请重新登录 www.trae.cn 后重新抓取 Cookie" % code
    if code == CODE_DEVICE_BAD:
        return "活动校验被拒(code=9074: %s): 官方文案是「当前参与用户太多」, 稍后重试" % message
    return "接口返回失败(code=%s): %s" % (code, message)


def _query_balance(client):
    """实时积分余额(查不到就返回 {})。"""
    try:
        return client.usage()
    except TraeError as exc:
        log("  * 积分余额查询失败(不影响签到结论): %s" % exc)
        return {}


def run(config):
    print("开始执行 Trae CN 每日签到...")
    cookie = _normalize_cookie(config.get("cookie"))
    if not cookie:
        return {"success": False,
                "message": "缺少 Cookie: 请在任务配置中粘贴 trae.cn 网页版的完整 Cookie"
                           "(获取方法见「配置获取指南」)"}
    dry_run = bool(config.get("dry_run"))
    web_base = str(config.get("web_base") or DEFAULT_WEB_BASE).strip() or DEFAULT_WEB_BASE
    api_base = str(config.get("api_base") or DEFAULT_API_BASE).strip() or DEFAULT_API_BASE
    device_id, device_source = _load_device_id(config.get("device_id"))
    try:
        req_source = int(config.get("req_source") or 0)
    except (TypeError, ValueError):
        req_source = 0
    req_bodies = [{"req_source": req_source}] if req_source > 0 else list(REQ_BODY_CANDIDATES)

    client = TraeClient(cookie, web_base, api_base, device_id)
    log("Cookie 已加载(%d 字符), 校验登录态..." % len(cookie))
    if device_id:
        log("设备号已加载(%s, 来源: %s)" % (_mask_device(device_id), device_source))
    else:
        log("设备号未加载: 领取积分需要一个真实设备号(见渠道配置指南)")

    # 1) 登录态
    try:
        login = client.check_login()
    except TraeError as exc:
        if "Cookie 已失效" in exc.message:
            return _session_invalid(exc)
        return {"success": False, "message": "登录态校验失败: %s" % exc}
    user_id = str(login.get("UserID") or "")
    log("登录态有效: UserID=%s Region=%s API=%s"
        % (mask(user_id) if user_id else "未知", client.region or "CN", client.pay_base))

    expires_at = _cookie_expires_at(cookie)
    expires_info = []
    if expires_at:
        text_exp = _format_ts(expires_at)
        if text_exp:
            # 名义有效期: 服务端可能因重新登录/登出/改密提前作废
            expires_info = [{"label": "网页 Cookie(名义)", "expires_at": text_exp}]
            log("Cookie 名义到期时间: %s" % text_exp)

    # 2) 换 JWT(有效期约 8 小时, 每次运行都要重新换)
    try:
        token_info = client.get_user_token()
    except TraeError as exc:
        return {"success": False, "message": "换取 JWT 失败: %s" % exc}
    log("JWT 获取成功(%s 字符)%s"
        % (len(client.jwt),
           ", 有效期至 %s" % _format_ts(token_info["expires_at"])
           if token_info.get("expires_at") else ""))

    # 3) 查签到状态(自动探测请求体: 不同产品谱系的 req_source 不同)
    status = None
    raw_status = None
    chosen_body = None
    last_error = None
    for body in req_bodies:
        try:
            body_status, http_status, raw = client.checkin_status(body)
        except TraeError as exc:
            if exc.http_status in (401, 403):
                return _session_invalid(exc)
            last_error = exc
            log("  * 状态查询失败(请求体 %s): %s" % (json.dumps(body, ensure_ascii=False), exc))
            continue
        code = raw.get("code") if isinstance(raw, dict) else None
        looks_like_status = any(k in body_status for k in ("checked_in", "did_checked_in",
                                                          "enable", "credits"))
        if code in (None, 0) and looks_like_status:
            status, raw_status, chosen_body = body_status, raw, body
            break
        error = _status_error(body_status, raw) or "响应结构不认识: %s" % (
            json.dumps(raw, ensure_ascii=False)[:160] if isinstance(raw, dict) else str(raw)[:160])
        last_error = TraeError(error)
        log("  * 请求体 %s 不被接受: %s" % (json.dumps(body, ensure_ascii=False), error))
    if status is None:
        return {"success": False,
                "message": "查询签到状态失败: %s" % (last_error or "未知原因, 详情见日志")}
    log("签到接口请求体: %s" % json.dumps(chosen_body, ensure_ascii=False))

    # 注意: checked_in=账号维度(本账号今天是否已签), did_checked_in=设备维度(这台设备今天是否已签)。
    # 判定"今日已签到"只能看账号维度; 设备维度为 true 只说明该设备当天名额已被用掉
    # (可能是别的账号签的), 此时仍要继续领取流程, 由服务端返回 9095 再如实报告失败。
    checked = bool(status.get("checked_in"))
    if status.get("did_checked_in") and not checked:
        log("提示: 该设备今天已被其它账号签到过(did_checked_in=true), 领取大概率返回 9095")
    today_credits = _first_number(status.get("credits"), status.get("extra_credits"))

    def _result(ok, message, claimed=False):
        data = {}
        if expires_info:
            data["expires_info"] = expires_info
        if user_id:
            data["user_id"] = user_id
        if client.region:
            data["region"] = client.region
        if today_credits is not None:
            data["today_credit"] = today_credits
        data["checked_in"] = bool(checked or claimed)
        log("签到结论上报: success=%s, 今日积分=%s" % (ok, today_credits))
        balance = _query_balance(client)
        if balance and balance.get("credits") is not None:
            data["balance_info"] = [{"label": "积分余额", "value": balance["credits"]}]
            data["credits_total"] = balance.get("credits_total")
        return {"success": ok, "message": message, "data": data}

    def _suffix():
        if today_credits is None:
            return ""
        return "(今日获得 %s 积分)" % today_credits

    if checked:
        log("今日已签到, 无需重复领取(幂等)。")
        return _result(True, "今日已签到" + _suffix())

    if status.get("enable") is False:
        log("签到活动未开启: %s" % json.dumps(status, ensure_ascii=False)[:300])
        return _result(False, "签到活动未开启(当前账号/时段不可签到)")

    if dry_run:
        log("已开启「仅查询(不领取)」, 不调用领取接口。")
        return _result(True, "仅查询: 登录态有效, 今日尚未签到(未领取)")

    # 4) 领取积分(幂等: 已签到时服务端返回 HTTP 400+10001)
    if not client.device_id:
        return _result(False, "缺少设备号, 无法领取积分: 请在装有 Trae 客户端的电脑上运行 "
                              "python tools/trae_device.py 生成 data/trae_device.json "
                              "(或在任务配置的「设备号」里粘贴 16 位设备号)。"
                              "如果只是想先验证 Cookie, 可打开「仅查询(不领取)」")
    log("今日未签到, 调用领取接口(设备号 %s)..."
        % _mask_device(client.device_id))
    claim_candidates = [chosen_body]
    for body in req_bodies:
        if body not in claim_candidates:
            claim_candidates.append(body)
    success = False
    claim_payload = {}
    index = 0
    attempts = 0
    delays = list(CLAIM_RETRY_DELAYS)
    while attempts < CLAIM_MAX_ATTEMPTS:
        body = claim_candidates[min(index, len(claim_candidates) - 1)]
        attempts += 1
        try:
            claim_body, http_status, raw_claim = client.claim(body)
        except TraeError as exc:
            if exc.http_status in (401, 403):
                return _session_invalid(exc)
            return _result(False, "领取积分失败: %s" % exc)
        code = raw_claim.get("code") if isinstance(raw_claim, dict) else None
        if code in AUTH_FAIL_CODES:
            return _session_invalid(TraeError("登录态已失效(code=%s)" % code))
        if code in (None, 0):
            if code is None and not isinstance(raw_claim, dict):
                return _result(False, "领取积分失败: 响应不是 JSON 对象")
            success = True
            claim_payload = raw_claim if isinstance(raw_claim, dict) else {}
            break
        if code in CODE_ALREADY:
            log("服务端提示今日已签到(code=%s), 按成功处理。" % code)
            checked = True
            today_credits = _first_number(_claimed_credits(raw_claim), today_credits)
            return _result(True, "今日已签到" + _suffix())
        message = (raw_claim.get("message") or raw_claim.get("msg")
                   or json.dumps(raw_claim, ensure_ascii=False)[:160]) \
            if isinstance(raw_claim, dict) else ""
        log("领取失败: 请求体 %s -> code=%s %s"
            % (json.dumps(body, ensure_ascii=False), code, message))
        if code == CODE_PARAM_MISSING:
            return _result(False, "领取参数不被接受(code=9004: %s): 通常是设备号缺失或格式不对, "
                                  "请用 tools/trae_device.py 重新生成 data/trae_device.json 后重试"
                                  % message)
        if code == CODE_DEVICE_TAKEN:
            return _result(False, "本设备今日已被其它账号签到(code=9095: %s): 该设备号今天的名额已被"
                                  "另一个 Trae 账号用掉, 官方限制「一台设备每天只能签到一个账号」。"
                                  "本账号今天没领到。解决办法: 给这个任务换一个设备号, 或明天再签"
                                  % message)
        if attempts >= CLAIM_MAX_ATTEMPTS:
            break
        # 9074: 设备号未被认可 / 活动校验拒绝, 换请求体 + 有界退避再试
        if index < len(claim_candidates) - 1:
            index += 1
        if delays:
            delay = delays.pop(0)
            log("约 %d 秒后用备用请求体重试(%d/%d)..."
                % (delay, attempts + 1, CLAIM_MAX_ATTEMPTS))
            time.sleep(delay)

    if not success:
        return _result(False, "领取积分失败(code=9074): 服务端不认可这个设备号(官方文案为"
                              "「当前参与用户太多」)。请确认设备号来自真实客户端: 在装了 Trae 客户端的"
                              "电脑上运行 python tools/trae_device.py 重新生成, 不要手填随机数")

    points = _claimed_credits(claim_payload)
    log("领取接口返回成功%s, 回查签到状态确认..."
        % ("(+%s 积分)" % points if points is not None else ""))

    # 5) 回查确认
    verified = False
    for _ in range(2):
        time.sleep(2)
        try:
            status2, _, _ = client.checkin_status(chosen_body)
        except TraeError:
            continue
        if bool(status2.get("checked_in")):   # 只看账号维度, 设备维度可能早被别人占用
            verified = True
            today_credits = _first_number(points, status2.get("credits"),
                                          status2.get("extra_credits"), today_credits)
            break
    if not verified and today_credits is None:
        today_credits = points

    if verified:
        log("签到成功, 状态回查确认已签到。")
        return _result(True, "签到成功" + _suffix(), claimed=True)
    log("领取接口返回成功, 但状态回查未确认(可稍后再次运行核对)。")
    return _result(True, "领取接口返回成功%s, 状态回查未确认"
                   % (" +%s 积分" % points if points is not None else ""), claimed=True)


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