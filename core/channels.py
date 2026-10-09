"""渠道发现与元数据加载。

每个渠道是 channels/<channel_id>/ 目录, 必须含 channel.py, 其中定义:
  - META: dict, 渠道元信息与配置字段声明(config_schema)
  - run(config): 签到入口(由平台以子进程执行 channel.py 时调用)

本模块仅读取 META(进程内 import), 实际签到在 runner.py 中以子进程执行。
"""
import importlib.util
import os
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CHANNELS_DIR = os.path.join(BASE_DIR, "channels")

FIELD_TYPES = ("text", "textarea", "password", "number", "bool", "select")

# {channel_id: (mtime, meta)} 进程内缓存, channel.py 修改后自动重载
_cache = {}


def normalize(channel_id, meta):
    """把渠道作者声明的 META 规范化, 缺省字段自动补齐。"""
    schema = []
    for f in meta.get("config_schema") or []:
        if not isinstance(f, dict) or not f.get("key"):
            continue
        ftype = f.get("type", "text")
        if ftype not in FIELD_TYPES:
            ftype = "text"
        schema.append({
            "key": str(f["key"]),
            "label": str(f.get("label") or f["key"]),
            "type": ftype,
            "required": bool(f.get("required")),
            "default": f.get("default", ""),
            "placeholder": str(f.get("placeholder") or ""),
            "help": str(f.get("help") or ""),
            "options": [
                {"value": str(o.get("value", "")), "label": str(o.get("label") or o.get("value", ""))}
                for o in (f.get("options") or []) if isinstance(o, dict)
            ],
        })
    return {
        "id": channel_id,
        "name": str(meta.get("name") or channel_id),
        "version": str(meta.get("version") or "0.0.0"),
        "author": str(meta.get("author") or ""),
        "description": str(meta.get("description") or ""),
        "config_schema": schema,
        "valid": True,
        "error": None,
    }


def _invalid(channel_id, error):
    return {
        "id": channel_id, "name": channel_id, "version": "?", "author": "",
        "description": "", "config_schema": [], "valid": False, "error": str(error),
    }


def _read_guide(channel_dir):
    """读取渠道目录下的人工配置获取指南(README.md), 不存在返回空串。"""
    path = os.path.join(channel_dir, "README.md")
    if os.path.isfile(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return f.read()
        except OSError:
            return ""
    return ""


def _load_meta(channel_id, path):
    mod_name = "checkin_channel_%s" % channel_id
    spec = importlib.util.spec_from_file_location(mod_name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    try:
        spec.loader.exec_module(mod)
        meta = getattr(mod, "META", None)
        if not isinstance(meta, dict):
            raise ValueError("channel.py 缺少 META 定义")
        normalized = normalize(channel_id, meta)
        if not callable(getattr(mod, "run", None)):
            raise ValueError("channel.py 缺少 run(config) 函数")
        return normalized
    except Exception as e:
        return _invalid(channel_id, e)


def list_channels(force=False):
    """扫描 channels/ 目录, 返回 {channel_id: meta}。"""
    result = {}
    if not os.path.isdir(CHANNELS_DIR):
        return result
    for entry in sorted(os.listdir(CHANNELS_DIR)):
        channel_dir = os.path.join(CHANNELS_DIR, entry)
        if entry.startswith(("_", ".")) or not os.path.isdir(channel_dir):
            continue
        path = os.path.join(channel_dir, "channel.py")
        if not os.path.isfile(path):
            continue
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            mtime = 0
        cached = _cache.get(entry)
        if not force and cached and cached[0] == mtime:
            meta = dict(cached[1])
            meta["guide"] = _read_guide(channel_dir)
            result[entry] = meta
            continue
        meta = _load_meta(entry, path)
        _cache[entry] = (mtime, meta)
        meta = dict(meta)
        meta["guide"] = _read_guide(channel_dir)
        result[entry] = meta
    return result


def get_channel_meta(channel_id):
    meta = list_channels().get(channel_id)
    if meta is None:
        raise KeyError("渠道不存在: %s" % channel_id)
    return meta


def get_channel_path(channel_id):
    """返回渠道脚本绝对路径, 不存在时抛异常。"""
    path = os.path.join(CHANNELS_DIR, channel_id, "channel.py")
    if not os.path.isfile(path):
        raise KeyError("渠道脚本不存在: %s" % path)
    return path
