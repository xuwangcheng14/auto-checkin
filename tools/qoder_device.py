#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成 Qoder 渠道的设备标识文件 data/qoder_device.json。

Qoder 服务端自 2026-09 下旬起只给带设备标识头的请求下发每日领取活动。
标识来自本机 Qoder 客户端自带的官方组件 resources/umid/runtime-info.exe,
因此本工具必须在装有 Qoder 客户端的 Windows 机器上运行(即开发机);
服务器没有客户端, 把生成的 JSON 上传到服务器 /opt/auto-checkin/data/ 即可。

用法:
    python tools/qoder_device.py            # 生成并打印(脱敏), 写入 data/qoder_device.json
    python tools/qoder_device.py --stdout   # 只打印完整 JSON, 不写文件(供管道上传)
"""
import json
import os
import socket
import subprocess
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def find_umid_exe():
    env = os.environ.get("QODER_UMID_EXE")
    if env and os.path.exists(env):
        return env
    rel = os.path.join("resources", "umid", "runtime-info.exe")
    for envkey, sub in (("LOCALAPPDATA", os.path.join("Programs", "Qoder")),
                        ("ProgramFiles", "Qoder"), ("ProgramFiles(x86)", "Qoder"),
                        ("ProgramW6432", "Qoder")):
        base = os.environ.get(envkey)
        if base and os.path.exists(os.path.join(base, sub, rel)):
            return os.path.join(base, sub, rel)
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
                    if "qoder" not in name:
                        continue
                    try:
                        loc = str(winreg.QueryValueEx(sk, "InstallLocation")[0]).strip()
                    except OSError:
                        loc = ""
                    if not loc:
                        try:
                            us = str(winreg.QueryValueEx(sk, "UninstallString")[0]).strip()
                            exe = us[1:].split('"', 1)[0] if us.startswith('"') else us.split(" ", 1)[0]
                            loc = os.path.dirname(exe)
                        except OSError:
                            pass
                    if loc and os.path.exists(os.path.join(loc, rel)):
                        return os.path.join(loc, rel)
                except OSError:
                    continue
    except Exception:
        pass
    return None


def machine_id():
    appdata = os.environ.get("APPDATA")
    if not appdata:
        return ""
    import glob
    dirs = [d for d in glob.glob(os.path.join(appdata, "com.qoder.app.*")) if os.path.isdir(d)]
    for d in sorted(dirs, key=os.path.getmtime, reverse=True):
        try:
            with open(os.path.join(d, "auth.machine-id"), "r", encoding="utf-8-sig") as f:
                v = f.read().strip()
            if v:
                return v
        except OSError:
            continue
    return ""


def product_version(exe):
    try:
        with open(os.path.join(os.path.dirname(exe), "..", "build-manifest.json"),
                  "r", encoding="utf-8") as f:
            return str(json.load(f).get("productVersion") or "")
    except Exception:
        return ""


def main():
    exe = find_umid_exe()
    if not exe:
        sys.exit("[qoder_device] 未找到 Qoder 客户端的 runtime-info.exe, 请在本机安装客户端"
                 "或用环境变量 QODER_UMID_EXE 指定组件路径")
    p = subprocess.run([exe, "--account-stdin"], input=b"", capture_output=True,
                       timeout=30, cwd=os.path.dirname(exe))
    lines = [l for l in p.stdout.decode("utf-8", "replace").strip().splitlines() if l.strip()]
    ri = json.loads(lines[-1])
    obj = {
        "os": "x86_64_windows",
        "hostname": socket.gethostname(),
        "version": product_version(exe) or "0.3.4",
        "machineId": machine_id(),
        "machineToken": str(ri.get("machineToken") or ""),
        "machineCode": str(ri.get("machineCode") or ""),
        "machineType": str(ri.get("machineType") or ""),
    }
    missing = [k for k, v in obj.items() if not v]
    if missing:
        sys.exit("[qoder_device] 标识不完整, 缺少: %s" % ", ".join(missing))
    payload = json.dumps(obj, ensure_ascii=False, indent=2)
    if "--stdout" in sys.argv:
        print(payload)
        return
    out = os.path.join(BASE_DIR, "data", "qoder_device.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        f.write(payload + "\n")
    print("[qoder_device] 已生成 %s" % out)
    for k, v in obj.items():
        print("  %s: %s...(%d 字符)" % (k, v[:6], len(v)) if len(v) > 12 else "  %s: %s" % (k, v))


if __name__ == "__main__":
    main()
