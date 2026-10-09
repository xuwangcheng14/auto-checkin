# AGENTS.md — AI 助手工作约定

本仓库是「自动签到管理平台」：Flask + SQLite3 后端、原生 JS 前端、Python 渠道脚本（插件式）。
本文档供 AI 编程助手（Claude Code / Cursor / ZCode 等）在开发、运行、部署本项目时参考。
人读的使用文档见 [README.md](README.md)。

## 技术栈与运行环境

- Python **3.9+**，第三方依赖仅 `flask>=3.0`、`requests>=2.31`（见 `requirements.txt`）
- 数据库 SQLite（`data/checkin.db`，WAL 模式），无迁移框架（`core/db.py` 内置幂等补列逻辑）
- 前端原生 JS SPA（`web/`），无构建步骤、无 Node 依赖
- 平台与渠道脚本通过**子进程 + stdin/stdout** 解耦，渠道不 import 平台代码

## 常用命令

```bash
# 安装依赖
pip install -r requirements.txt

# 启动平台(默认 http://127.0.0.1:7777; 环境变量 HOST/PORT 亦可)
python app.py
python app.py --host 0.0.0.0 --port 7777

# Linux/macOS 后台运行
./start.sh -d && ./start.sh status && ./start.sh stop

# 单元测试(离线, 不发网络请求)
python -X utf8 tests/test_expires_info.py

# 手动执行某个渠道脚本(不走平台, 用于调试)
echo '{"cookie": "测试值"}' | python channels/<渠道id>/channel.py
# 期望: 输出日志 + 最后一行 __CHECKIN_RESULT__{"success": ..., "message": ...}
```

## 目录结构与模块职责

```
app.py            入口: 创建 Flask app、注册路由、启动调度线程
core/
  db.py           SQLite 访问层(建表/幂等迁移/WAL)
  cron.py         5 字段 cron 解析(*/n、区间、列表、@daily 等别名)
  channels.py     扫描 channels/ 目录, 加载各渠道 META
  runner.py       子进程执行渠道脚本、超时强杀、解析 __CHECKIN_RESULT__、日志落库
  scheduler.py    后台调度线程(15s 扫描到期任务; 重启后过期任务补跑一次)
api/routes.py     REST API(/api/stats|channels|tasks|logs|cron)
web/              前端 SPA(仪表盘/任务管理/执行日志/渠道管理/配置指南 五个页面)
channels/         渠道脚本目录: 每个子目录一个渠道, 入口必须叫 channel.py
  _template/      新渠道模板(目录名 _ 开头会被平台忽略)
docs/渠道开发指南.md  渠道接入权威规范(开发新渠道前必读)
tools/qoder_device.py Qoder 设备标识生成工具(需装有 Qoder 客户端的 Windows)
data/             运行数据(自动创建, 已 gitignore): checkin.db、app.log 等
```

## 任务一：新增签到渠道（最常见需求）

1. **必读** `docs/渠道开发指南.md` —— 里面有执行模型、META/config_schema 规范、
   `run(config)` 返回值契约（含 `data.expires_info` / `data.balance_info` 可选上报）、
   开发步骤与验收清单。
2. 复制 `channels/_template/` 为 `channels/<渠道id>/`，只改 `META` 与 `run(config)`，
   文件末尾的引导代码保持原样（它负责读 stdin、兜底异常、打印结果行）。
3. 渠道适配只发生在渠道目录内；**不要**为了迁就单个渠道修改 `core/`、`api/`、`web/`。
   平台能力确实不足时，先说明问题再动平台代码，并同步更新开发指南。
4. 渠道目录必须附带 `README.md` 人工配置获取指南（每个配置项去哪拿、长什么样），
   平台会读取并在界面展示。
5. 提交前按指南第 7 节验收清单逐项自检，本地必须通过
   `echo '{...}' | python channels/<渠道id>/channel.py` 测试并输出 `__CHECKIN_RESULT__` 结果行。

## 任务二：修改平台代码

- 改动 `core/`、`api/`、`web/` 后至少验证：渠道列表能加载、创建任务、手动运行、日志落库。
- 涉及数据库结构时走 `core/db.py` 的幂等迁移（`_migrate`），保证旧库无损升级。
- 改了 `channels/_template/`（平台契约的一部分）必须同步更新 `docs/渠道开发指南.md`。
- 跑一遍 `python -X utf8 tests/test_expires_info.py` 确认不回归。

## 部署参考（Linux 服务器）

```bash
# 1) 部署到任意目录(示例 /opt/auto-checkin), 建 venv 装依赖
cd /opt/auto-checkin && python3 -m venv venv
venv/bin/pip install -r requirements.txt

# 2a) 简单方式: 后台跑
./start.sh -d

# 2b) 常驻方式: systemd(开机自启/崩溃拉起)
#    ExecStart=/opt/auto-checkin/venv/bin/python app.py --host 0.0.0.0 --port 7777
#    WorkingDirectory=/opt/auto-checkin, Restart=on-failure

# 3) 验证
curl -s http://127.0.0.1:7777/api/stats
```

要点：

- 升级只覆盖代码目录，**绝不覆盖 `data/`**（数据库里有任务凭证）；
- 跨机器迁移 = 拷贝 `data/checkin.db`（Qoder 渠道另需 `data/qoder_device.json`，chmod 600）；
- 拷库迁移后 `next_run_at` 若已在过去，调度器会立即补跑一次（签到接口幂等，安全）；
- Web 界面**无鉴权**，只有监听 127.0.0.1 时才可视为安全；对外暴露必须加反代鉴权。

## 安全红线

- 凭证（Cookie/Token/密码）一律通过任务配置传入，**禁止硬编码或写入文档/示例**。
- `data/` 与 `_tmp_*` 文件已 gitignore，任何情况下不得提交或打包进发布物。
- 日志中不要完整打印凭证（最多前 8 位 + `...`）；异常信息带 URL 参数时注意脱敏。
- 尊重目标站点：请求设超时、失败重试 ≤3 次带退避、不做并发轰炸。

## 仓库发布说明

- 本仓库对外公开发布；`tools/` 中仅保留 `qoder_device.py`，其余为个人部署/调试工具不随仓库分发；
- `docs/部署与运维.md` 为内网运维手册，不入库；
- 新增文件前先想一下是否包含私有环境信息（内网 IP、账号、路径），有则加入 `.gitignore`。
