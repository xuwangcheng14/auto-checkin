# 自动签到管理平台 (Auto Checkin)

一个轻量的本地签到管理系统：**Flask + SQLite3 + Python 渠道脚本（插件式）**，替代青龙面板的定时签到场景。
Web 界面管理任务、cron 定时调度、查看执行日志；新渠道以"插件目录"方式接入，界面自动生成配置表单。

纯本地运行、无构建依赖、无需 Docker，一个 Python 环境即可跑起来。

## 功能特性

- **任务管理**：为每个签到渠道创建任务，配置 cron 定时规则（含常用预设）、超时时间、启用开关
- **自动调度**：后台线程按 cron 触发；应用重启后错过的任务补跑一次；同一任务不会并发执行
- **执行日志**：完整捕获脚本 stdout/stderr，状态（成功/失败/运行中）、耗时、结构化结果一目了然
- **渠道插件化**：一个渠道 = `channels/` 下一个 Python 脚本目录，复制模板改两个函数即可接入
- **配置表单自动生成**：渠道声明 `config_schema`，新建任务时界面自动渲染输入项（凭证打码显示）
- **配置获取指南**：每个渠道目录内置 `README.md` 人工教程（凭证怎么抓、一步步照做），界面内直接查看
- **令牌有效期 / 剩余积分展示**：渠道上报后，任务列表按剩余天数着色，积分变动一眼可见
- **手动运行**：随时点「▶」立即执行，不用等定时

## 快速开始

要求：Python 3.9+（仅依赖 `flask`、`requests`）。

**Windows**：双击 `startAutoChekin.bat`（首次会自动安装依赖）

**Linux / macOS**：

```bash
chmod +x start.sh   # 仅首次需要
./start.sh
```

或者手动执行：

```bash
pip install -r requirements.txt
python app.py
```

然后打开 <http://127.0.0.1:7777>。

> 界面无鉴权，默认只监听 127.0.0.1。如需局域网访问：`python app.py --host 0.0.0.0`（自行承担风险）。

## 使用流程

1. **渠道管理**页查看可用渠道与配置项说明（每个渠道都有「📖 配置获取指南」）；
2. **任务管理**页新建任务：选渠道 → 按指南填入凭证 → 设定 cron 规则（支持 `@daily`、`*/n` 等预设）；
3. 点「▶ 立即运行」验证一次，到**执行日志**页查看完整输出；
4. 之后平台按 cron 自动执行，「仪表盘」页汇总成功/失败情况。

## 内置渠道

| 渠道 | 说明 |
|---|---|
| `workbuddy` | WorkBuddy（腾讯 CodeBuddy）每日积分签到，支持多账号、WxPusher 微信推送，详见 `docs/WorkBuddy自动签到配置说明.md` |
| `modelscope` | ModelScope 每日登录奖励，填站点 Cookie 即可，支持查询魔方余额 |
| `qoder` | Qoder 每日积分签到，支持 PAT / 会话令牌、国内版与国际版 |
| `example_demo` | 本地模拟签到（不联网），用于测试平台与演示接入方式，可删除 |

各渠道的凭证获取方法见对应 `channels/<渠道id>/README.md`（界面「配置指南」页亦可查看）。

> Qoder 渠道在服务器上运行需要设备标识文件：在装有 Qoder 客户端的 Windows 机器上执行
> `python tools/qoder_device.py` 生成 `data/qoder_device.json` 后上传，详见渠道 README。

## 新增签到渠道

把 `channels/_template/` 复制为 `channels/<渠道id>/`，实现 `META`（渠道信息 + 配置表单声明）
与 `run(config)`（签到逻辑）两个部分即可，界面自动出现新渠道。

详细规范、开发步骤与验收清单见 **[docs/渠道开发指南.md](docs/渠道开发指南.md)**。

## 部署到服务器（Linux / NAS / 软路由）

**方式一：脚本后台运行**

```bash
./start.sh -d        # 后台启动, 日志写入 data/app.log
./start.sh status    # 查看状态
./start.sh stop      # 停止
```

注意：`-d` 方式不会在机器重启后自动拉起，重启后需重新执行 `./start.sh -d`。

**方式二：systemd 服务（开机自启、崩溃自动拉起）**

```bash
sudo tee /etc/systemd/system/auto-checkin.service <<'EOF'
[Unit]
Description=Auto Checkin Platform (Flask)
After=network.target

[Service]
WorkingDirectory=/opt/auto-checkin
ExecStart=/opt/auto-checkin/venv/bin/python app.py
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now auto-checkin
```

把 `/opt/auto-checkin` 换成实际部署目录即可。长期跑建议用 venv 隔离依赖
（先 `python3 -m venv venv && venv/bin/pip install -r requirements.txt`，再把
`ExecStart` 指向 `venv/bin/python app.py`）。

升级时**只需覆盖代码目录**，`data/`（数据库与任务配置）不要覆盖。

## 渠道脚本执行模型

平台以**子进程**运行每个渠道脚本（互不干扰、超时可强杀）：

- 配置 JSON 从 **stdin** 传入，同时注入环境变量 `CHECKIN_CONFIG` 与 `CHECKIN_<KEY>`；
- 脚本 `print()` 的内容全部进入执行日志；
- 脚本结束前输出一行 `__CHECKIN_RESULT__{"success": true, "message": "..."}` 作为结构化结果。

## API 一览（供二次开发）

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | /api/stats | 仪表盘统计 |
| GET | /api/channels | 渠道列表(含 config_schema) |
| GET/POST | /api/tasks | 任务列表 / 新建 |
| PUT/DELETE | /api/tasks/<id> | 修改 / 删除 |
| POST | /api/tasks/<id>/run | 立即执行 |
| GET | /api/logs?task_id=&status=&page= | 日志分页 |
| GET | /api/logs/<id> | 日志详情(含完整输出) |
| GET | /api/cron/next?expr= | cron 下次执行时间预览 |

## 安全须知

- **凭证安全**：Cookie/Token 等同于账号密码，只保存在本地 `data/checkin.db`，不要把 `data/` 目录发给他人或提交到 git；
- **界面无鉴权**：默认仅监听 127.0.0.1，暴露到公网前请自行加反向代理鉴权；
- **合规提醒**：本项目仅用于个人账号的每日签到自动化，请遵守目标站点的服务条款与频率限制，不要用于刷量等滥用行为。

## 目录结构

```
app.py                    入口: python app.py [--host H] [--port P]
startAutoChekin.bat       Windows 一键启动
start.sh                  Linux/macOS 启动脚本(前台/-d 后台/stop/status)
requirements.txt          依赖(仅 flask/requests)
core/
  db.py                   SQLite 访问层(WAL, data/checkin.db)
  cron.py                 极简 5 字段 cron 解析器(支持 */n、区间、@daily 等)
  channels.py             渠道扫描与 META 加载
  runner.py               子进程执行渠道脚本、超时强杀、结果解析、日志落库
  scheduler.py            后台调度线程(15s 扫描)
api/routes.py             REST API (/api/...)
web/                      前端(原生 JS SPA, 无构建依赖)
channels/
  _template/              新渠道模板(平台忽略 _ 开头目录)
  example_demo/           本地模拟示例
  workbuddy/ modelscope/ qoder/   内置渠道
docs/
  渠道开发指南.md          渠道接入权威规范(面向开发者/AI Agent)
  WorkBuddy自动签到配置说明.md     WorkBuddy 渠道详细教程与青龙独立脚本说明
tools/
  qoder_device.py         Qoder 设备标识文件生成工具
data/                     运行数据(自动创建, 勿提交): checkin.db、日志
```

## 常见问题

- **数据库在哪？** `data/checkin.db`（SQLite，可直接备份/迁移；跨机器迁移后重启即可）。
- **错过的时间点会补跑吗？** 会，应用重启后到期任务补跑一次（签到接口幂等，重复执行安全）。
- **怎么改端口？** `python app.py --port 8080`，或环境变量 `PORT`。
- **新增渠道后界面没出现？** 检查目录名是否以 `_` 开头、入口是否叫 `channel.py`、META 是否合法；
  「渠道管理」页会显示具体加载错误。
- **迁移任务到新机器？** 复制 `data/checkin.db` 即可（Qoder 渠道另需 `data/qoder_device.json`）。

## 许可证

[MIT](LICENSE)
