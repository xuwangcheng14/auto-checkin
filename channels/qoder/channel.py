#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
Qoder 每日积分签到渠道 (纯标准库, 无第三方依赖, 一个渠道覆盖 CN/国际版双区)。

原理 (协议参考开源项目 decolua/9router 与 mmqz/cpa-multi-plugins 的 qoder 插件):
  - Qoder 的每日权益统一走 campaigns 活动领取系统(旧版 daily-check-in 已被
    上游全局禁用: claim 恒 409 且不发积分, 不可再调):
      GET  {base}/sash/api/v1/me/campaigns              列出账号可参与的活动
      POST {base}/sash/api/v1/me/campaigns/{id}/claim   领取 CLAIM_BENEFIT 活动
  - billing 接口按桌面端身份头门控(User-Agent: Qoder / Cosy-ClientType: 10 /
    Cosy-Version: 0.3.4), 缺这些头活动列表可能为空, 导致当天权益静默漏领。
  - 2026-09 下旬起, 服务端只给带「设备标识头」(Cosy-MachineToken/MachineId 等,
    来自本机 Qoder 客户端自带组件 runtime-info.exe)的请求下发每日领取活动;
    缺失时国际版账号活动列表只剩 VIEW_DETAILS, 无可领积分活动。
    本脚本优先读取 data/qoder_device.json (服务器无客户端时用 tools/qoder_device.py
    在有客户端的机器上生成后上传), 否则在 Windows 上自动调用本机客户端组件获取;
    两者都不可得则按原行为继续(CN 区通常仍可领取)。
  - 积分余额: GET {base}/api/v2/quota/usage, 基础额度+赠送额度+各类资源包求和。

凭证(二选一, 都通过任务配置传入, 不要硬编码):
  - pt- PAT: 在 qoder.com.cn(或 qoder.com)设置页创建, 长期有效;
    本脚本每次运行自动调 /api/v1/jobToken/exchange 换成 jt- 使用。
  - dt-/jt- 令牌: 来自 Qoder 客户端/代理工具抓取, dt- 约 30 天有效。

区域: cn = qoder.com.cn(国内版, 默认), intl = qoder.com(国际版)。
一个任务对应一个账号; 多账号请新建多个任务。
配置获取方法见本目录 README.md。
"""
import glob
import json
import os
import socket
import subprocess
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

BASE_CN = "https://openapi.qoder.com.cn"
BASE_INTL = "https://openapi.qoder.sh"
PATH_USERINFO = "/api/v1/userinfo"
PATH_QUOTA = "/api/v2/quota/usage"
PATH_CAMPAIGNS = "/sash/api/v1/me/campaigns?forceRefresh=true"
PATH_PAT_EXCHANGE = "/api/v1/jobToken/exchange"

META = {
    "name": "Qoder 每日积分签到",
    "version": "1.1.0",
    "author": "xuwangcheng",
    "description": "Qoder(qoder.com.cn / qoder.com) 每日签到领积分: 通过 campaigns 活动接口领取当日权益, 返回签到前后积分变化。",
    "config_schema": [
        {
            "key": "token",
            "label": "访问凭证",
            "type": "password",
            "required": True,
            "placeholder": "粘贴 pt- PAT 或 dt-/jt- 令牌(获取方法见「配置获取指南」)",
            "help": "pt- 开头的 PAT 会自动换成会话令牌使用; dt-/jt- 令牌直接使用",
        },
        {
            "key": "region",
            "label": "站点区域",
            "type": "select",
            "options": [
                {"value": "cn", "label": "国内版 qoder.com.cn"},
                {"value": "intl", "label": "国际版 qoder.com"},
            ],
            "default": "cn",
            "help": "凭证从哪个站点创建就选哪个区域",
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


def base_for(region):
    return BASE_INTL if str(region or "").strip().lower() in ("intl", "global") else BASE_CN


# ---------------- 设备标识头(2026-09 起服务端风控要求) ----------------
# 服务端只给带设备标识的请求下发每日领取活动。标识来源(按优先级):
#   1. data/qoder_device.json (或环境变量 QODER_DEVICE_JSON 指向的文件),
#      供无客户端的服务器使用, 用 tools/qoder_device.py 在有客户端的机器上生成;
#   2. Windows 本机 Qoder 客户端自带组件 resources/umid/runtime-info.exe
#      (官方组件, 客户端主进程同样每小时调用一次), 配合 auth.machine-id。
# 都取不到则返回空, 按普通请求继续(CN 区通常仍可领取)。

_DEVICE_TTL_S = 1800
_device_cache = (0.0, {})
_device_logged = False


def _data_dir():
    return os.path.normpath(os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "..", "..", "data"))


def _load_device_file():
    path = os.environ.get("QODER_DEVICE_JSON") or os.path.join(_data_dir(), "qoder_device.json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            obj = json.load(f)
        return obj if isinstance(obj, dict) else {}
    except Exception:
        return {}


def _registry_client_roots():
    roots = []
    try:
        import winreg
        for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            try:
                key = winreg.OpenKey(hive, r"Software\Microsoft\Windows\CurrentVersion\Uninstall")
            except OSError:
                continue
            i = 0
            while True:
                try:
                    sub = winreg.EnumKey(key, i)
                    i += 1
                except OSError:
                    break
                try:
                    sk = winreg.OpenKey(key, sub)
                    name = str(winreg.QueryValueEx(sk, "DisplayName")[0]).lower()
                    if "qoder" in name:
                        loc = ""
                        try:
                            loc = str(winreg.QueryValueEx(sk, "InstallLocation")[0]).strip()
                        except OSError:
                            pass
                        if not loc:
                            try:
                                us = str(winreg.QueryValueEx(sk, "UninstallString")[0]).strip()
                                exe = us[1:].split('"', 1)[0] if us.startswith('"') else us.split(" ", 1)[0]
                                loc = os.path.dirname(exe)
                            except OSError:
                                pass
                        if loc:
                            roots.append(loc)
                except OSError:
                    continue
    except Exception:
        pass
    return roots


def _find_umid_exe():
    env = os.environ.get("QODER_UMID_EXE")
    if env:
        return env if os.path.exists(env) else None
    rel = os.path.join("resources", "umid", "runtime-info.exe")
    cands = []
    for envkey, sub in (("LOCALAPPDATA", os.path.join("Programs", "Qoder")),
                        ("ProgramFiles", "Qoder"), ("ProgramFiles(x86)", "Qoder"),
                        ("ProgramW6432", "Qoder")):
        base = os.environ.get(envkey)
        if base:
            cands.append(os.path.join(base, sub))
    cands += _registry_client_roots()
    for root in cands:
        exe = os.path.join(root, rel)
        if os.path.exists(exe):
            return exe
    return None


def _umid_identity(exe):
    try:
        p = subprocess.run([exe, "--account-stdin"], input=b"", capture_output=True,
                           timeout=15, cwd=os.path.dirname(exe))
        lines = [l for l in p.stdout.decode("utf-8", "replace").strip().splitlines() if l.strip()]
        obj = json.loads(lines[-1])
        return obj if isinstance(obj, dict) else {}
    except Exception:
        return {}


def _machine_id():
    appdata = os.environ.get("APPDATA")
    if not appdata:
        return ""
    try:
        dirs = [d for d in glob.glob(os.path.join(appdata, "com.qoder.app.*")) if os.path.isdir(d)]
    except Exception:
        return ""
    for d in sorted(dirs, key=lambda p: os.path.getmtime(p), reverse=True):
        try:
            with open(os.path.join(d, "auth.machine-id"), "r", encoding="utf-8-sig") as f:
                v = f.read().strip()
            if v:
                return v
        except OSError:
            continue
    return ""


def _product_version(exe):
    try:
        manifest = os.path.join(os.path.dirname(exe), "..", "build-manifest.json")
        with open(manifest, "r", encoding="utf-8") as f:
            return str(json.load(f).get("productVersion") or "")
    except Exception:
        return ""


def _hdr_latin1(v):
    """urllib 头要求 latin-1 可编码; 先转成 UTF-8 字节再按 latin-1 映射,
    使中文主机名等非 ASCII 值以与官方客户端相同的 UTF-8 字节上线。"""
    return str(v).encode("utf-8").decode("latin-1")


def risk_headers():
    global _device_cache, _device_logged
    now = time.time()
    ttl = _DEVICE_TTL_S if _device_cache[1] else 60.0
    if now - _device_cache[0] < ttl:
        return _device_cache[1]
    obj = _load_device_file()
    exe = None
    if not obj and os.name == "nt":
        exe = _find_umid_exe()
        if exe:
            obj = _umid_identity(exe)
    h = {}
    if obj:
        if exe:
            os_name = "windows"
            try:
                import platform
                arch = {"amd64": "x86_64", "x86_64": "x86_64", "arm64": "aarch64"}.get(
                    platform.machine().lower(), "x86_64")
            except Exception:
                arch = "x86_64"
            h["Cosy-MachineOS"] = "%s_%s" % (arch, os_name)
            h["Cosy-MachineHostname"] = socket.gethostname()
            h["Cosy-Version"] = _product_version(exe) or "0.3.4"
            h["Cosy-MachineId"] = _machine_id()
        else:
            h["Cosy-MachineOS"] = str(obj.get("os") or "x86_64_windows")
            h["Cosy-MachineHostname"] = str(obj.get("hostname") or "")
            h["Cosy-Version"] = str(obj.get("version") or "0.3.4")
            h["Cosy-MachineId"] = str(obj.get("machineId") or "")
        h["Cosy-MachineToken"] = str(obj.get("machineToken") or "")
        h["Cosy-MachineCode"] = str(obj.get("machineCode") or "")
        h["Cosy-MachineType"] = str(obj.get("machineType") or "")
    h = {k: _hdr_latin1(v) for k, v in h.items() if v}
    _device_cache = (now, h)
    if h and not _device_logged:
        _device_logged = True
        log("已附加设备标识头(%d 项, 来源: %s)" % (len(h), "配置文件" if _load_device_file() else "本机客户端"))
    return h


def http_req(url, token=None, method="GET", body=None, timeout=20):
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": "Qoder",
        "Cosy-ClientType": "10",
        "Cosy-Version": "0.3.4",
    }
    headers.update(risk_headers())
    if token:
        headers["Authorization"] = "Bearer " + token
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = Request(url, data=data, headers=headers, method=method)
    try:
        with urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except URLError as e:
        return 0, "URLError: %s" % e


def exchange_pat(pat, base):
    """pt- PAT 换 jt-/jrt- 会话令牌, 返回 (token, err)。"""
    status, body = http_req(base + PATH_PAT_EXCHANGE, method="POST", body={"personal_token": pat})
    if status != 200:
        return None, "PAT 换取会话令牌失败(HTTP %s): %s" % (status, body[:150])
    try:
        obj = json.loads(body)
    except ValueError:
        return None, "PAT 换取会话令牌: 响应不是合法 JSON"
    token = obj.get("token") or obj.get("access_token")
    if not token:
        return None, "PAT 换取会话令牌: 响应中没有 token 字段"
    return token, ""


def fetch_userinfo(base, token):
    """查询账号昵称(尽力而为), 返回 (nickname, err)。"""
    status, body = http_req(base + PATH_USERINFO, token=token)
    if status != 200:
        return "", "HTTP %s" % status
    try:
        obj = json.loads(body)
    except ValueError:
        return "", "响应不是合法 JSON"
    name = obj.get("name") or obj.get("username") or obj.get("id") or ""
    return str(name), ""


def fetch_quota(base, token):
    """查询积分余额: 各资源池 remaining 求和。返回 (int 或 None, err)。"""
    status, body = http_req(base + PATH_QUOTA, token=token)
    if status in (401, 403):
        return None, "UNAUTHORIZED"
    if status != 200:
        return None, "HTTP %s" % status
    try:
        obj = json.loads(body)
    except ValueError:
        return None, "响应不是合法 JSON"
    total = 0.0
    pools = [obj.get("userQuota") or {}, obj.get("addOnQuota") or {}]
    pools += obj.get("dedicatedResourcePackages") or []
    if isinstance(obj.get("orgResourcePackage"), dict):
        pools.append(obj["orgResourcePackage"])
    for p in pools:
        try:
            total += float(p.get("remaining") or 0)
        except (TypeError, ValueError):
            pass
    return int(round(total)), ""


def fetch_campaigns(base, token):
    """拉取活动列表, 返回 (campaigns 数组, err)。"""
    status, body = http_req(base + PATH_CAMPAIGNS, token=token)
    if status in (401, 403):
        return None, "UNAUTHORIZED"
    if status != 200:
        return None, "HTTP %s: %s" % (status, body[:150])
    try:
        obj = json.loads(body)
    except ValueError:
        return None, "响应不是合法 JSON"
    campaigns = obj.get("campaigns")
    if not isinstance(campaigns, list):
        campaigns = ((obj.get("data") or {}).get("campaigns")) if isinstance(obj.get("data"), dict) else None
    return (campaigns or []), ""


def campaign_credit(c):
    if not isinstance(c, dict):
        return 0
    ben = c.get("benefit") or {}
    if str(ben.get("kind") or "").lower() != "credits":
        return 0
    return _to_int(ben.get("amount"))


def _to_int(v):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return 0


def pick_campaign(campaigns, now_ts, want_claimable):
    """找第一个 CLAIM_BENEFIT 且领取状态匹配的活动。"""
    for c in campaigns:
        if not isinstance(c, dict):
            continue
        if str(c.get("actionType") or "").lower() != "claim_benefit":
            continue
        is_claimable = str(c.get("claimStatus") or "").upper() == "CLAIMABLE"
        if want_claimable != is_claimable:
            continue
        if want_claimable:
            start = _to_int(c.get("startAt"))
            end = _to_int(c.get("endAt"))
            if start > 0 and now_ts < start:
                continue
            if end > 0 and now_ts > end:
                continue
        return c
    return None


def claim_campaign(base, token, campaign_id):
    """领取活动, 返回 (result_dict): success/result/replayed/reward。"""
    url = "%s/sash/api/v1/me/campaigns/%s/claim" % (base, campaign_id)
    status, body = http_req(url, token=token, method="POST", body={})
    if status == 409:
        return {"success": False, "result": "ALREADY_CLAIMED"}
    if status in (401, 403):
        return {"success": False, "error": "UNAUTHORIZED"}
    if status >= 400:
        return {"success": False, "error": "HTTP %s: %s" % (status, body[:150])}
    try:
        obj = json.loads(body)
    except ValueError:
        return {"success": False, "error": "响应不是合法 JSON: %s" % body[:150]}
    inner = obj.get("data") if isinstance(obj.get("data"), dict) else obj
    if str(inner.get("status") or "").upper() == "CLAIMED":
        return {"success": True, "replayed": bool(inner.get("replayed"))}
    msg = inner.get("message") or inner.get("msg") or obj.get("message") or ""
    result = inner.get("result") or obj.get("result") or ""
    return {"success": False, "result": str(result), "message": str(msg)[:150], "upstream": body[:200]}


def run(config):
    token = str(config.get("token") or "").strip().strip('"').strip("'")
    if not token:
        return {"success": False,
                "message": "缺少凭证: 请在任务配置中粘贴 Qoder 的 pt- PAT 或 dt-/jt- 令牌(获取方法见配置获取指南)"}
    region = str(config.get("region") or "cn")
    base = base_for(region)
    site = "qoder.com.cn" if base == BASE_CN else "qoder.com"

    if config.get("dry_run"):
        log("[dry-run] 区域=%s 站点=%s 凭证=%s...(%d 字符), 跳过实际请求"
            % (region, site, token[:6], len(token)))
        return {"success": True, "message": "[dry-run] 配置校验通过(未实际请求)"}

    # 1) pt- PAT 先换会话令牌
    if token.startswith("pt-"):
        log("检测到 PAT(%s...), 换取会话令牌..." % token[:6])
        token, err = exchange_pat(token, base)
        if not token:
            return {"success": False, "message": err}

    # 2) 账号昵称(尽力而为)
    nickname, _ = fetch_userinfo(base, token)
    if nickname:
        log("账号: %s (%s)" % (nickname, site))

    # 3) 签到前余额
    before, err = fetch_quota(base, token)
    if err == "UNAUTHORIZED":
        return {"success": False,
                "message": "凭证无效或已过期(HTTP 401): 请按配置获取指南重新获取并更新任务"}
    log("签到前积分: %s" % ("未知(%s)" % err if before is None else before))

    # 4) 拉活动列表
    campaigns, err = fetch_campaigns(base, token)
    if err == "UNAUTHORIZED":
        return {"success": False,
                "message": "凭证无效或已过期(HTTP 401): 请按配置获取指南重新获取并更新任务"}
    if campaigns is None:
        return {"success": False, "message": "活动列表获取失败(%s)" % err}
    log("活动列表: %d 条" % len(campaigns))

    # 5) 判定并领取
    now_ts = time.time()
    claimable = pick_campaign(campaigns, now_ts, want_claimable=True)
    claimed_before = pick_campaign(campaigns, now_ts, want_claimable=False)
    reward = campaign_credit(claimable if claimable else claimed_before)

    if claimable:
        cid = str(claimable.get("campaignId") or "")
        log("发现可领取活动 %s (奖励 %s 积分), 领取中..." % (cid or "?", reward))
        res = claim_campaign(base, token, cid)
        if res.get("error") == "UNAUTHORIZED":
            return {"success": False,
                    "message": "凭证无效或已过期(HTTP 401): 请按配置获取指南重新获取并更新任务"}
        if res.get("success") and res.get("replayed"):
            outcome = "already"          # 上游幂等重放: 今天其实已领过
        elif res.get("success"):
            outcome = "ok"
        elif res.get("result", "").upper() == "ALREADY_CLAIMED":
            outcome = "already"
        else:
            detail = res.get("message") or res.get("result") or res.get("error") or "未知原因"
            return {"success": False,
                    "message": "领取失败: %s" % str(detail)[:160]}
    elif claimed_before:
        outcome = "already"              # 列表里已有 CLAIMED 记录
        log("今日活动已领取过(幂等命中)")
    else:
        outcome = "none"                 # 没有可领取活动, 正常空状态
        log("今日暂无可领取的权益活动")

    # 6) 签到后余额
    after, err2 = fetch_quota(base, token)
    if after is None:
        after = None
    if outcome == "ok" and before is not None and after is not None and after > before:
        gained = after - before
    elif outcome == "ok" and reward > 0:
        gained = reward
    else:
        gained = 0 if outcome in ("already", "none") else None

    if outcome == "ok":
        if after is not None:
            message = "签到成功, 本次获得 +%s 积分 | 当前积分 %s" % (gained if gained is not None else "?", after)
        else:
            message = "签到成功, 本次获得 +%s 积分" % (gained if gained is not None else "?")
    elif outcome == "already":
        message = "今日已签到(幂等命中)"
        if after is not None:
            message += " | 当前积分 %s" % after
    else:
        message = "今日暂无可领取的权益活动"
        if after is not None:
            message += " | 当前积分 %s" % after
    log(message)

    payload = {}
    if after is not None:
        payload = {"balance_info": [{"label": "积分", "value": after}], "balance_before": before}
    return {"success": True, "message": message, "data": payload}


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
