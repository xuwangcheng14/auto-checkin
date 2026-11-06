#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""提取本机 Trae 客户端的真实设备号, 生成 data/trae_device.json（Trae 渠道用）。

为什么需要它:
    Trae CN 的签到领取接口(checkin_credits/claim)要求请求头 x-device-id 是**客户端真实
    签发**的设备号:
      - 不带           -> 业务码 9004「订单参数错误」
      - 随机/伪造       -> 业务码 9074「当前参与用户太多」(伪装成限流的设备校验拒绝)
      - 本机真实设备号   -> code 0, 领取成功
    网页 Cookie 模式同样要带(只是换 JWT 用 Cookie, 领取仍校验设备号)。

服务端还会按设备号限制「一台设备每天只能签到一个账号」(第二个账号报 9095),
所以本工具一次导出本机所有 Trae 客户端的设备号, 多账号可以各用一份。

用法(在装有 Trae 客户端的机器上运行, Windows/macOS/Linux 均可):
    python tools/trae_device.py             # 扫描并写入 data/trae_device.json(打印结果)
    python tools/trae_device.py --stdout    # 只打印 JSON, 不写文件(方便管道上传到服务器)
    python tools/trae_device.py --app "Trae CN"   # 只导出指定客户端目录

部署到服务器时: 把生成的 data/trae_device.json 放到服务器的 data/ 目录即可,
渠道会自动读取; 也可以在任务配置的「设备号」里直接粘贴 device_id。
"""
import base64
import glob
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import time

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_PATH = os.path.join(BASE_DIR, "data", "trae_device.json")

# Trae 客户端的数据目录名(不同版本/产品线拼写不同)
APP_DIR_NAMES = ("Trae CN", "TRAE SOLO CN", "TRAE CN", "trae-cn", "trae-solo-cn")

# iCube 加密用的两张硬编码表(与客户端实现一致, 公开 skill 已交叉验证)
_TABLE_A = bytes([
    82, 9, 106, 213, 48, 54, 165, 56, 191, 64, 163, 158, 129, 243, 215, 251,
    124, 227, 57, 130, 155, 47, 255, 135, 52, 142, 67, 68, 196, 222, 233, 203,
    84, 123, 148, 50, 166, 194, 35, 61, 238, 76, 149, 11, 66, 250, 195, 78,
    8, 46, 161, 102, 40, 217, 36, 178, 118, 91, 162, 73, 109, 139, 209, 37])
_TABLE_B = bytes([
    31, 221, 168, 51, 136, 7, 199, 49, 177, 18, 16, 89, 39, 128, 236, 95,
    96, 81, 127, 169, 25, 181, 74, 13, 45, 229, 122, 159, 147, 201, 156, 239,
    160, 224, 59, 77, 174, 42, 245, 176, 200, 235, 187, 60, 131, 83, 153, 97,
    23, 43, 4, 126, 186, 119, 214, 38, 225, 105, 20, 99, 85, 33, 12, 125])


def data_dirs():
    """返回本机所有 Trae 客户端数据目录(存在 aha 或 User 子目录的才算)。"""
    if platform.system() == "Windows":
        roots = [os.environ.get("APPDATA") or os.path.expanduser("~")]
    elif platform.system() == "Darwin":
        roots = [os.path.join(os.path.expanduser("~"), "Library", "Application Support")]
    else:
        roots = [os.path.join(os.path.expanduser("~"), ".config"),
                 os.path.join(os.path.expanduser("~"), ".trae")]
    found = []
    for root in roots:
        if not root:
            continue
        for name in APP_DIR_NAMES:
            path = os.path.join(root, name)
            if os.path.isdir(os.path.join(path, "aha")) or os.path.isdir(os.path.join(path, "User")):
                if path not in found:
                    found.append(path)
    return found


def _aes_cbc_decrypt(key, iv, data):
    """AES-128-CBC 解密(去掉 PKCS7 填充); 依次尝试 cryptography / pycryptodome / openssl。"""
    try:
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
        decryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).decryptor()
        plain = decryptor.update(data) + decryptor.finalize()
    except ImportError:
        try:
            from Crypto.Cipher import AES  # type: ignore
            plain = AES.new(key, AES.MODE_CBC, iv).decrypt(data)
        except ImportError:
            openssl = shutil.which("openssl")
            if not openssl:
                raise RuntimeError("需要 AES 库: 请先执行 pip install cryptography")
            proc = subprocess.run(
                [openssl, "enc", "-d", "-aes-128-cbc", "-nopad",
                 "-K", key.hex(), "-iv", iv.hex()],
                input=data, capture_output=True, timeout=15)
            if proc.returncode != 0:
                raise RuntimeError("openssl 解密失败: %s"
                                   % proc.stderr.decode("utf-8", "replace")[:120])
            plain = proc.stdout
    if plain and plain[-1]:
        plain = plain[:-plain[-1]]
    return plain


def decrypt_icube(value):
    """解密 iCube 加密串, 返回明文 bytes。

    结构: base64 -> [0:6] 头 | [6:38] 密钥盐 | [38:] AES-128-CBC 密文;
    明文前 64 字节是客户端自己的头部, 之后才是 JSON。
    """
    blob = base64.b64decode(value)
    if len(blob) < 70:
        raise RuntimeError("加密串长度异常")
    sha = hashlib.sha512(blob[6:38]).digest()
    xor = bytes(a ^ b for a, b in zip(_TABLE_A, _TABLE_B))
    digest = hashlib.sha512(sha + xor).digest()
    plain = _aes_cbc_decrypt(digest[:16], digest[16:32], blob[38:])
    return plain[64:]


def _json_from_icube(value):
    text = decrypt_icube(value).decode("utf-8", "replace").strip()
    start = text.find("{")
    if start < 0:
        raise RuntimeError("解密结果不是 JSON")
    return json.loads(text[start:])


def read_device_from_tiny_storage(app_dir):
    path = os.path.join(app_dir, "aha", "TinyStorage")
    if not os.path.isfile(path):
        return None, ""
    try:
        with open(path, "rb") as handle:
            data = json.loads(handle.read().decode("utf-8", "replace"))
    except (OSError, ValueError) as exc:
        return None, "读取失败: %s" % exc
    encrypted = (data.get("tiny_storage_data") or {}).get("aha.device.device_id")
    if not encrypted:
        return None, "TinyStorage 里没有 aha.device.device_id"
    try:
        obj = _json_from_icube(encrypted)
    except Exception as exc:  # noqa: BLE001
        return None, "解密失败: %s" % exc
    device = str(obj.get("device_id_str") or "").strip()
    if not device:
        return None, "解密结果里没有 device_id_str"
    return device, ""


def read_accounts(app_dir):
    """从 globalStorage/storage.json 读该客户端登录过的账号(仅 userId, 用于区分)。"""
    path = os.path.join(app_dir, "User", "globalStorage", "storage.json")
    if not os.path.isfile(path):
        return []
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return []
    users = []
    for key, value in data.items():
        if key.startswith("iCubeAuthInfo://icube-dc:"):        # 设备注册键里直接带设备号
            device = key.split(":", 2)[-1].strip()
            if device.isdigit() and ("dc:" + device) not in users:
                users.append("dc:" + device)
        if key == "iCubeAuthInfo://icube.cloudide":
            try:
                obj = _json_from_icube(value)
            except Exception:  # noqa: BLE001
                continue
            uid = str(obj.get("userId") or obj.get("user_id") or "").strip()
            if uid:
                users.append(uid)
    return users


def collect(app_filter=None):
    candidates = []
    for app_dir in data_dirs():
        app_name = os.path.basename(app_dir)
        if app_filter and app_filter.lower() not in app_name.lower():
            continue
        device, error = read_device_from_tiny_storage(app_dir)
        users = read_accounts(app_dir)
        if not device:
            # 兜底: 设备注册键里的数字后缀就是设备号(不需要解密)
            for user in users:
                if user.startswith("dc:"):
                    device = user[3:]
                    error = ""
                    break
        candidates.append({
            "app": app_name,
            "app_dir": app_dir,
            "device_id": device or "",
            "user_ids": [u for u in users if not u.startswith("dc:")],
            "error": error,
        })
    return candidates


def main(argv):
    app_filter = None
    if "--app" in argv:
        index = argv.index("--app")
        if index + 1 < len(argv):
            app_filter = argv[index + 1]
    candidates = collect(app_filter)
    usable = [c for c in candidates if c["device_id"]]

    # 默认设备号优先选「该客户端还没登录任何账号」的设备: 这种设备号不会被客户端里的账号
    # 抢先用掉当天的签到名额, 更适合给本平台的任务用。
    preferred = next((c for c in usable if not c["user_ids"]), usable[0] if usable else None)

    payload = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "host": platform.node(),
        "device_id": preferred["device_id"] if preferred else "",
        "device_from": preferred["app"] if preferred else "",
        "candidates": candidates,
        "note": "Trae 渠道用: 领取积分时作为 x-device-id 发送; 一个设备号每天只能签到一个账号, "
                "多账号请用不同机器的设备号。本文件含设备标识, 不要提交到公开仓库。",
    }

    if "--stdout" in argv:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0 if usable else 1

    print("扫描到的 Trae 客户端:")
    if not candidates:
        print("  (没找到) 请确认本机安装并登录过 Trae CN / TRAE SOLO CN 客户端")
    for item in candidates:
        if item["device_id"]:
            users = (" 账号: " + ", ".join(u[:6] + "..." for u in item["user_ids"])) \
                if item["user_ids"] else ""
            print("  %-14s device_id = %s%s" % (item["app"], item["device_id"], users))
        else:
            print("  %-14s 未取到设备号: %s" % (item["app"], item["error"] or "未知原因"))

    if not usable:
        print("\n没有可用的设备号, 未写入文件。")
        return 1

    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
    try:
        os.chmod(OUT_PATH, 0o600)
    except OSError:
        pass
    print("\n已写入 %s (渠道会自动读取)" % OUT_PATH)
    print("默认设备号: %s (来自 %s) 也可以在任务配置的「设备号」里直接粘贴"
          % (payload["device_id"], payload["device_from"] or "第一个可用客户端"))
    print("\n注意: 同一设备号每天只能签到 1 个账号; 多账号请换设备号(如另一台机器的客户端)。")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))