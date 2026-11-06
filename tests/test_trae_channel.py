# -*- coding: utf-8 -*-
"""Trae CN 渠道的离线单元测试(不发起任何网络请求)。

覆盖:
  1. 缺少 Cookie -> success=False, 不发请求
  2. Cookie 失效(CheckLogin IsLogin=false) -> success=False, 提示重新抓取
  3. 正常签到(未签到 -> 领取成功 -> 回查确认): 结果/日志/expires_info/balance_info
  4. 今日已签到: 不调用领取接口, success=True
  5. 领取接口返回 HTTP 400 + code=10001 -> 按"今日已签到"处理(幂等)
  6. 领取被限流 code=9074 -> 退避重试后成功
  7. 仅查询(dry_run=True) -> 不调用领取接口
  8. 签到活动未开启(enable=false) -> success=False
  9. Cookie 规整(多行/带 Cookie: 前缀)
 10. 平台引导代码: stdin 输入 -> 最后一行输出 __CHECKIN_RESULT__

运行: python -X utf8 tests/test_trae_channel.py
"""
import importlib.util
import json
import os
import subprocess
import sys
import time

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CHANNEL = os.path.join(BASE, "channels", "trae", "channel.py")
TEST_DEVICE = "1234567890123456"   # 测试用"真实设备号"(服务端语义: 只有它才被认可)

PASSED, FAILED = [], []


def check(name, cond, detail=""):
    (PASSED if cond else FAILED).append(name)
    print(("  ✅ %s" if cond else "  ❌ %s") % name + (" | %s" % detail if detail and not cond else ""))


def load_channel():
    spec = importlib.util.spec_from_file_location("trae_channel_under_test", CHANNEL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def make_cookie(days=60):
    """构造一份合法的测试 Cookie: sid_guard 带有效期, 便于校验 expires_info 解析。"""
    issued = int(time.time())
    sid = "0123456789abcdef0123456789abcdef"
    return ("ttwid=1%%7Cabc%%7Cdef; sessionid=%s; sessionid_ss=%s; sid_tt=%s; "
            "sid_guard=%s%%7C%d%%7C%d%%7CWed%%2C+01+Jan+2026+00%%3A00%%3A00+GMT; "
            "has_biz_token=true; X-Cloudide-Session=ZmFrZS1zZXNzaW9uLXRva2VuLXZhbHVl"
            % (sid, sid, sid, sid, issued, days * 86400))


class FakeServer(object):
    """按路径回放预置响应, 记录请求路径, 校验请求头与请求体。"""

    def __init__(self, status_checked_in=False, claim_responses=None, enable=True,
                 is_login=True, usage=None, token_fallback=False,
                 reject_empty_body=False, usage_web_404=False, did_checked_in=False):
        self.calls = []
        self.status_checked_in = status_checked_in
        self.claim_responses = list(claim_responses or [])
        self.enable = enable
        self.is_login = is_login
        self.usage = usage
        self.token_fallback = token_fallback
        self.reject_empty_body = reject_empty_body
        self.usage_web_404 = usage_web_404
        self.did_checked_in = did_checked_in
        self.claim_count = 0
        self.bodies = []
        self.headers_seen = []
        self.used_devices = set()

    def __call__(self, client, url, headers, body, use_curl=False):
        path = url.split("/", 3)[-1]
        self.calls.append(url)
        self.bodies.append(body)
        self.headers_seen.append(dict(headers or {}))
        if path.endswith("/trae/CheckLogin"):
            result = {"IsLogin": self.is_login, "UserID": "12345678" if self.is_login else "",
                      "Region": "CN" if self.is_login else "",
                      "AIPayHost": "https://api.trae.cn" if self.is_login else ""}
            return 200, json.dumps({"ResponseMetadata": {}, "Result": result}), "application/json"
        if path.endswith("/common/GetUserToken"):
            if self.token_fallback and "api.trae.cn" in url:
                return 401, json.dumps({"ResponseMetadata": {
                    "Error": {"Code": "20310", "Message": "The user is not logged in,"}}}), \
                    "application/json"
            return 200, json.dumps({"ResponseMetadata": {},
                                    "Result": {"Token": "fake-jwt-token",
                                               "ExpiredAt": int((time.time() + 7200) * 1000)}}), \
                "application/json"
        if path.endswith("/checkin_credits/status"):
            if self.reject_empty_body and not body:
                return 200, json.dumps({"code": 9074, "message": "当前参与用户太多，请稍后再试"}), \
                    "application/json"
            payload = {"code": 0, "checked_in": self.status_checked_in, "enable": self.enable,
                       "did_checked_in": self.did_checked_in or self.status_checked_in,
                       "credits": 20 if self.status_checked_in else 100}
            return 200, json.dumps(payload), "application/json"
        if path.endswith("/checkin_credits/claim"):
            if self.reject_empty_body and not body:
                self.claim_count += 1
                return 200, json.dumps({"code": 9074, "message": "当前参与用户太多，请稍后再试"}), \
                    "application/json"
            if self.claim_responses:   # 预设响应(测重试/幂等路径)
                index = min(self.claim_count, len(self.claim_responses) - 1)
                status, payload = self.claim_responses[index]
                self.claim_count += 1
                if payload.get("code") == 0:
                    self.status_checked_in = True
                return status, json.dumps(payload), "application/json"
            # 否则按真实服务端语义校验设备号
            device = str((headers or {}).get("x-device-id") or "")
            if not device:
                self.claim_count += 1
                return 200, json.dumps({"code": 9004, "message":
                                        "The submitted order parameters are incorrect."}), \
                    "application/json"
            if device != TEST_DEVICE:
                self.claim_count += 1
                return 200, json.dumps({"code": 9074, "message": "当前参与用户太多，请稍后再试"}), \
                    "application/json"
            if device in self.used_devices:
                self.claim_count += 1
                return 200, json.dumps({"code": 9095, "message": "当前设备今日已经签到，请明日再来哦～"}), \
                    "application/json"
            self.used_devices.add(device)
            self.claim_count += 1
            self.status_checked_in = True
            return 200, json.dumps({"code": 0, "message": "success"}), "application/json"
        if path.endswith("/web_user_ent_usage") and self.usage_web_404:
            return 404, "not found", "text/plain"
        if path.endswith("/ide_user_ent_usage") or path.endswith("/web_user_ent_usage"):
            payload = self.usage if self.usage is not None else {
                "usage_summary": {"total_amount": 500, "consumed_amount": 100}}
            return 200, json.dumps(payload), "application/json"
        return 404, "not found", "text/plain"


class patched(object):
    """临时替换 TraeClient._post_raw / time.sleep, 退出时恢复。"""

    def __init__(self, server, trailing=(200, {"code": 0}, "application/json")):
        self.server = server
        self.trailing = trailing

    def __enter__(self):
        mod = self.mod = load_channel()
        self.orig = mod.TraeClient._post_raw
        self.orig_sleep = mod.time.sleep
        self.orig_sleep_value = time.sleep
        self.orig_device_file = mod.DEVICE_FILE
        # 隔离真实设备号文件, 避免测试依赖本机 data/trae_device.json
        mod.DEVICE_FILE = os.path.join(os.path.dirname(CHANNEL), "__no_such_device_file__.json")

        def fake_post_raw(client, url, headers, body, use_curl=False):
            status, text, ct = self.server(client, url, headers, body, use_curl)
            if isinstance(ct, str) and ct:
                return status, text, ct
            return status, text, ""

        mod.TraeClient._post_raw = fake_post_raw
        mod.time.sleep = lambda seconds: None
        return mod

    def __exit__(self, *exc):
        self.mod.TraeClient._post_raw = self.orig
        self.mod.time.sleep = self.orig_sleep
        self.mod.DEVICE_FILE = self.orig_device_file
        return False


def test_missing_cookie():
    print("[1] 缺少 Cookie")
    mod = load_channel()
    server = FakeServer()
    orig = mod.TraeClient._post_raw

    def boom(*a, **k):
        raise AssertionError("缺少 Cookie 时不应发起请求")

    mod.TraeClient._post_raw = boom
    try:
        result = mod.run({"cookie": "   "})
    finally:
        mod.TraeClient._post_raw = orig
    check("success=False", result.get("success") is False, repr(result))
    check("提示缺少 Cookie", "缺少 Cookie" in result.get("message", ""), result.get("message"))
    del server


def test_cookie_expired():
    print("[2] Cookie 失效(IsLogin=false)")
    with patched(FakeServer(is_login=False)) as mod:
        result = mod.run({"cookie": make_cookie()})
    check("success=False", result.get("success") is False, repr(result))
    check("提示 Cookie 已失效", "Cookie 已失效" in result.get("message", ""), result.get("message"))


def test_happy_path():
    print("[3] 正常签到(未签到 -> 领取 -> 回查确认)")
    server = FakeServer(status_checked_in=False)
    with patched(server) as mod:
        result = mod.run({"cookie": make_cookie(), "req_source": 3, "device_id": TEST_DEVICE})
    check("success=True", result.get("success") is True, repr(result))
    check("消息含「签到成功」", "签到成功" in result.get("message", ""), result.get("message"))
    data = result.get("data") or {}
    check("上报今日积分 20", data.get("today_credit") == 20, repr(data.get("today_credit")))
    check("上报积分余额 400(总数-已消耗)",
          data.get("balance_info") == [{"label": "积分余额", "value": 400}], repr(data.get("balance_info")))
    expires = data.get("expires_info") or []
    check("上报 Cookie 到期时间",
          len(expires) == 1 and len(expires[0].get("expires_at", "")) == 19
          and "名义" in expires[0].get("label", ""), repr(expires))
    check("checked_in=True", data.get("checked_in") is True, repr(data.get("checked_in")))
    order = ["CheckLogin" if "CheckLogin" in c else
             "GetUserToken" if "GetUserToken" in c else
             "claim" if c.endswith("/claim") else
             "status" if c.endswith("/status") else
             "usage" if "user_ent_usage" in c else "?"
             for c in server.calls]
    check("请求顺序: 登录 -> 换 token -> 查状态 -> 领取 -> 回查 -> 余额",
          order == ["CheckLogin", "GetUserToken", "status", "claim", "status", "usage"],
          repr(server.calls))
    claim_bodies = [b for c, b in zip(server.calls, server.bodies) if c.endswith("/claim")]
    check("领取请求体沿用 status 命中的 req_source=3",
          claim_bodies and claim_bodies[0] == {"req_source": 3}, repr(claim_bodies))
    claim_headers = [h for c, h in zip(server.calls, server.headers_seen) if c.endswith("/claim")]
    check("领取请求带真实设备号头 x-device-id",
          claim_headers and claim_headers[0].get("x-device-id") == TEST_DEVICE,
          repr(claim_headers[:1])[:200])


def test_no_device_id():
    print("[15] 缺少设备号 -> 领取前拦下并给出获取指引")
    server = FakeServer(status_checked_in=False)
    with patched(server) as mod:
        result = mod.run({"cookie": make_cookie()})
    check("success=False", result.get("success") is False, repr(result))
    check("提示缺少设备号", "缺少设备号" in result.get("message", ""), result.get("message"))
    check("提示 tools/trae_device.py",
          "trae_device.py" in result.get("message", ""), result.get("message"))
    check("未调用领取接口", not any(c.endswith("/claim") for c in server.calls), repr(server.calls))


def test_device_taken_today():
    print("[16] 设备当日已被别的账号签过(checked_in=false, did_checked_in=true) -> 不能误报成功")
    server = FakeServer(status_checked_in=False, did_checked_in=True)
    server.used_devices.add(TEST_DEVICE)
    with patched(server) as mod:
        result = mod.run({"cookie": make_cookie(), "device_id": TEST_DEVICE})
    check("success=False(账号维度未签到, 不能当成功)", result.get("success") is False, repr(result))
    check("仍然发起了 claim 请求", server.claim_count >= 1, str(server.claim_count))
    check("消息含 9095 且说明设备被其它账号占用",
          "9095" in result.get("message", "") and "其它账号" in result.get("message", ""),
          result.get("message"))


def test_account_checked_with_device_flag():
    print("[18] 账号维度已签到 -> 直接成功(即使设备维度也为 true)")
    server = FakeServer(status_checked_in=True, did_checked_in=True)
    with patched(server) as mod:
        result = mod.run({"cookie": make_cookie(), "device_id": TEST_DEVICE})
    check("success=True", result.get("success") is True, repr(result))
    check("未调用领取接口", not any(c.endswith("/claim") for c in server.calls), repr(server.calls))


def test_device_from_file():
    print("[17] 设备号从 data/trae_device.json 自动读取")
    import tempfile
    server = FakeServer(status_checked_in=False)
    with patched(server) as mod:
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "trae_device.json")
            with open(path, "w", encoding="utf-8") as handle:
                json.dump({"device_id": TEST_DEVICE}, handle)
            mod.DEVICE_FILE = path
            result = mod.run({"cookie": make_cookie()})
    check("success=True", result.get("success") is True, repr(result))
    claim_headers = [h for c, h in zip(server.calls, server.headers_seen) if c.endswith("/claim")]
    check("使用了文件里的设备号",
          claim_headers and claim_headers[0].get("x-device-id") == TEST_DEVICE,
          repr(claim_headers[:1])[:200])


def test_already_checked_in():
    print("[4] 今日已签到(幂等)")
    server = FakeServer(status_checked_in=True)
    with patched(server) as mod:
        result = mod.run({"cookie": make_cookie()})
    check("success=True", result.get("success") is True, repr(result))
    check("消息含「今日已签到」", "今日已签到" in result.get("message", ""), result.get("message"))
    check("未调用领取接口", not any(c.endswith("/claim") for c in server.calls), repr(server.calls))


def test_claim_already_by_code():
    print("[5] 领取返回 HTTP 400 + code=10001(今日已签到)")
    server = FakeServer(status_checked_in=False,
                        claim_responses=[(400, {"code": 10001, "message": "already checked in"})])
    with patched(server) as mod:
        result = mod.run({"cookie": make_cookie(), "device_id": TEST_DEVICE})
    check("success=True(幂等命中)", result.get("success") is True, repr(result))
    check("消息含「今日已签到」", "今日已签到" in result.get("message", ""), result.get("message"))


def test_claim_rate_limited_then_ok():
    print("[6] 领取被限流 9074 -> 退避重试后成功")
    server = FakeServer(status_checked_in=False, claim_responses=[
        (200, {"code": 9074, "message": "当前参与用户太多"}),
        (200, {"code": 0, "data": {"points": 20}}),
    ])
    with patched(server) as mod:
        result = mod.run({"cookie": make_cookie(), "device_id": TEST_DEVICE})
    check("success=True", result.get("success") is True, repr(result))
    check("领取接口被调用 2 次", server.claim_count == 2, str(server.claim_count))


def test_dry_run():
    print("[7] 仅查询(dry_run)")
    server = FakeServer(status_checked_in=False)
    with patched(server) as mod:
        result = mod.run({"cookie": make_cookie(), "dry_run": True})
    check("success=True", result.get("success") is True, repr(result))
    check("消息含「仅查询」", "仅查询" in result.get("message", ""), result.get("message"))
    check("未调用领取接口", not any(c.endswith("/claim") for c in server.calls), repr(server.calls))


def test_activity_disabled():
    print("[8] 签到活动未开启(enable=false)")
    server = FakeServer(status_checked_in=False, enable=False)
    with patched(server) as mod:
        result = mod.run({"cookie": make_cookie()})
    check("success=False", result.get("success") is False, repr(result))
    check("消息含「未开启」", "未开启" in result.get("message", ""), result.get("message"))


def test_cookie_normalize():
    print("[9] Cookie 规整(多行 / 带 Cookie: 前缀 / 引号)")
    mod = load_channel()
    multi = 'Cookie: ttwid=abc;\n sessionid=def;\n\n sid_guard=ghi;\n'
    check("去掉 Cookie: 前缀并合并为一行",
          mod._normalize_cookie(multi) == "ttwid=abc; sessionid=def; sid_guard=ghi",
          repr(mod._normalize_cookie(multi)))
    check("去掉两端引号与末尾分号",
          mod._normalize_cookie('  "a=1; b=2;"  ') == "a=1; b=2",
          repr(mod._normalize_cookie('  "a=1; b=2;"  ')))
    cookie = make_cookie(30)
    exp = mod._cookie_expires_at(cookie)
    check("从 sid_guard 解析出到期时间", isinstance(exp, int) and exp > time.time() + 25 * 86400,
          repr(exp))


def test_bootstrap_stdin():
    print("[10] 平台引导代码(stdin -> __CHECKIN_RESULT__ 行)")
    proc = subprocess.run([sys.executable, "-X", "utf8", CHANNEL],
                          input=json.dumps({"cookie": ""}), capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=60)
    lines = [ln for ln in (proc.stdout or "").splitlines() if ln.strip()]
    last = lines[-1] if lines else ""
    check("最后一行以 __CHECKIN_RESULT__ 开头", last.startswith("__CHECKIN_RESULT__"), last[:120])
    payload = json.loads(last[len("__CHECKIN_RESULT__"):]) if last.startswith("__CHECKIN_RESULT__") else {}
    check("结果是合法 JSON 且 success=false", payload.get("success") is False, repr(payload)[:200])


def test_token_host_fallback():
    print("[11] GetUserToken 在 api 域名失败 -> 回退网页域名成功")
    server = FakeServer(status_checked_in=True, token_fallback=True)
    with patched(server) as mod:
        result = mod.run({"cookie": make_cookie()})
    check("success=True", result.get("success") is True, repr(result))
    token_calls = [c for c in server.calls if "GetUserToken" in c]
    check("两个域名都试过", len(token_calls) == 2 and
          token_calls[0].startswith("https://api.trae.cn") and
          token_calls[1].startswith("https://www.trae.cn"), repr(token_calls))


def test_waf_blocked_message():
    print("[12] 网页域名返回 WAF 挑战页且无 curl_cffi -> 给出可读提示")
    class WafServer(FakeServer):
        def __call__(self, client, url, headers, body, use_curl=False):
            if "www.trae.cn" in url:
                return 200, "<!doctype html><html><head><script>waf challenge</script>", "text/html"
            return FakeServer.__call__(self, client, url, headers, body, use_curl)

    server = WafServer(token_fallback=True)
    with patched(server) as mod:
        had_curl = mod._curl_requests
        mod._curl_requests = None
        try:
            result = mod.run({"cookie": make_cookie()})
        finally:
            mod._curl_requests = had_curl
    check("success=False", result.get("success") is False, repr(result))
    check("提示含「WAF」或「验证页」",
          ("WAF" in result.get("message", "") or "验证页" in result.get("message", "")),
          result.get("message"))


def test_auto_probe_req_source():
    print("[13] 请求体自动探测: 空请求体被拒(9074) -> 自动改用 req_source=3")
    server = FakeServer(status_checked_in=False, reject_empty_body=True)
    with patched(server) as mod:
        result = mod.run({"cookie": make_cookie(), "device_id": TEST_DEVICE})  # req_source 自动探测
    check("success=True", result.get("success") is True, repr(result))
    status_bodies = [b for c, b in zip(server.calls, server.bodies) if c.endswith("/status")][:2]
    check("先试空请求体、再试 req_source=3",
          status_bodies == [{}, {"req_source": 3}], repr(status_bodies))
    claim_bodies = [b for c, b in zip(server.calls, server.bodies) if c.endswith("/claim")]
    check("领取接口沿用命中的请求体", claim_bodies[:1] == [{"req_source": 3}], repr(claim_bodies))


def test_balance_web_endpoint_fallback():
    print("[14] 余额接口: 网页谱系 404 -> 回退客户端谱系")
    server = FakeServer(status_checked_in=True, usage_web_404=True)
    with patched(server) as mod:
        result = mod.run({"cookie": make_cookie()})
    data = result.get("data") or {}
    check("余额仍取到 400", data.get("balance_info") == [{"label": "积分余额", "value": 400}],
          repr(data.get("balance_info")))
    usage_calls = [c for c in server.calls if "user_ent_usage" in c]
    check("两个余额接口都试过", len(usage_calls) == 2, repr(usage_calls))


def main():
    tests = [test_missing_cookie, test_cookie_expired, test_happy_path, test_already_checked_in,
             test_claim_already_by_code, test_claim_rate_limited_then_ok, test_dry_run,
             test_activity_disabled, test_cookie_normalize, test_bootstrap_stdin,
             test_token_host_fallback, test_waf_blocked_message, test_auto_probe_req_source,
             test_balance_web_endpoint_fallback, test_no_device_id, test_device_taken_today,
             test_device_from_file, test_account_checked_with_device_flag]
    for fn in tests:
        fn()
    print("\n通过 %d 项, 失败 %d 项" % (len(PASSED), len(FAILED)))
    if FAILED:
        print("失败项: %s" % ", ".join(FAILED))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())