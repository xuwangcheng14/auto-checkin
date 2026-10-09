#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
【渠道模板】—— 新增签到渠道从这里开始。

使用方法:
  1. 把整个 _template 目录复制为 channels/<你的渠道id>/ (目录名即渠道id,
     建议: 小写字母/数字/下划线, 例如 tieba, bilibili, game_sign)
  2. 修改 META(渠道信息与配置字段声明)和 run(config)(签到逻辑)
  3. 本地测试:  echo '{"字段key": "值"}' | python channels/<渠道id>/channel.py
     输出最后一行必须是 __CHECKIN_RESULT__{...} 格式
  4. 打开 Web 界面 -> 渠道管理, 确认渠道加载无错误 -> 新建任务即可调度

完整规范(字段类型表/返回值格式/注意事项/验收清单)见 docs/渠道开发指南.md
"""

META = {
    # 渠道信息(展示在 Web 界面"渠道管理"页)
    "name": "模板渠道",                      # 显示名称
    "version": "1.0.0",
    "author": "your-name",
    "description": "一句话说明这个渠道做什么(会展示在界面上)。",

    # 配置字段声明: Web 界面按此自动生成表单, 用户填写后作为 config 传给 run()
    "config_schema": [
        {
            "key": "username",               # 字段名(英文), 传给 run() 的 dict 键
            "label": "用户名/手机号",         # 界面显示名
            "type": "text",                  # text | textarea | password | number | bool | select
            "required": True,
            "placeholder": "请输入账号",
            "help": "登录账号, 支持手机号/邮箱",
        },
        {
            "key": "password",
            "label": "密码",
            "type": "password",              # password 在界面打码显示
            "required": True,
        },
        {
            "key": "extra_headers",
            "label": "附加请求头",
            "type": "textarea",              # 多行文本
            "required": False,
            "help": "每行一个, 格式: Key: Value",
        },
        {
            "key": "retries",
            "label": "失败重试次数",
            "type": "number",
            "default": 3,
        },
        {
            "key": "dry_run",
            "label": "仅测试(不实际请求)",
            "type": "bool",
            "default": False,
        },
        {
            "key": "period",
            "label": "签到时段",
            "type": "select",
            "options": [
                {"value": "morning", "label": "早上"},
                {"value": "evening", "label": "晚上"},
            ],
            "default": "morning",
        },
    ],
}


def run(config):
    """签到入口(必填)。

    参数:
        config: dict, 键为 META.config_schema 中声明的 key。
                所有值默认是字符串; number/bool 类型平台会做转换。

    返回(必填): dict
        {
            "success": True/False,        # 必填, 签到是否成功
            "message": "签到成功, +10积分", # 必填, 一句话结果(展示在列表)
            "data": {...}                  # 可选, 附加信息(如积分、连续天数)
        }

    注意:
        - 用 print() 打日志(平台会完整捕获), 不要打印 __CHECKIN_RESULT__ 前缀
        - 平台按任务的超时设置强制杀进程, 请让脚本在超时内跑完
        - 任何异常都应自行捕获并返回 success=False, 而不是让脚本崩溃
    """
    print("开始执行签到...")
    username = str(config.get("username") or "").strip()
    if not username:
        return {"success": False, "message": "缺少用户名"}

    if config.get("dry_run"):
        print("[dry-run] 跳过实际请求")
        return {"success": True, "message": "dry-run 测试通过"}

    # TODO: 在这里实现真正的签到逻辑(登录/请求接口/解析结果)
    print("正在为 %s 签到(时段: %s)..." % (username, config.get("period", "morning")))

    return {"success": True, "message": "%s 签到成功" % username, "data": {}}


# ==================== 以下为平台引导代码, 无需修改 ====================
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
