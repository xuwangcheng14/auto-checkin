# WorkBuddy 每日自动签到 · 配置说明文档


> 本文补充了原文缺失的 **macOS / Linux 路径**，并按「能直接照着做」的标准重写了步骤。

---

## 0. 先读这一段

| 项目 | 说明 |
|---|---|
| 做什么 | 让 WorkBuddy 每天自动签到领积分，不用手动点 |
| 需要什么 | 一台常年开机的机器（NAS / 软路由 / 云服务器 / Mac）跑定时任务 |
| 要不要抓包 | **不需要**，登录态直接从本地文件读 |
| 难度 | 低，全程复制粘贴，10 分钟能搞定 |
| 唯一麻烦 | Token **60 天**过期一次，得手动重登续上 |

> ⚠️ **安全提醒（务必看）**
> 你的 `ACCESS_TOKEN` 等价于「完整的账号登录凭证」，拿到它的人可以以你的身份调用接口。
> - 不要把它贴到群里、论坛、GitHub Issue
> - 第三方脚本跑之前，**先把代码通读一遍**，确认它只做签到、没有往别处上传你的 token
> - 最稳妥的做法是自己写脚本（见附录 A），token 只留在本机

---

## 1. 原理（一句话）

WorkBuddy 客户端登录后，会把登录态以**明文 JSON** 写在本地一个叫 `workbuddy-desktop.info` 的文件里。
签到脚本读出里面的 `accessToken` 和 `uid`，直接调签到接口 —— 所以完全不需要抓包。

---

## 2. 前置条件

- [ ] 已在 WorkBuddy 桌面端**登录**过（没登录过就没有那个文件）
- [ ] 有一台能跑定时任务的环境（本文以青龙面板为主，Mac 用户可直接看附录 A）

---

## 3. 第一步：拿到 ACCESS_TOKEN 和 UID

### 3.1 认证文件在哪

文件固定叫 `workbuddy-desktop.info`，路径按系统分：

| 系统 | 路径 |
|---|---|
| **Windows** | `%LOCALAPPDATA%\CodeBuddyExtension\Data\Public\auth\workbuddy-desktop.info` |
| **macOS** | `~/Library/Application Support/CodeBuddyExtension/Data/Public/auth/workbuddy-desktop.info` |
| **Linux** | `~/.config/CodeBuddyExtension/Data/Public/auth/workbuddy-desktop.info` |

> Windows 用户直接把 `%LOCALAPPDATA%\CodeBuddyExtension\Data\Public\auth\` 粘到资源管理器的地址栏里回车即可。

**macOS / Linux 用户注意命令行引号** —— 路径里有空格，下面两种写法才对：

```bash
# 写法一：推荐，$HOME + 双引号
open "$HOME/Library/Application Support/CodeBuddyExtension/Data/Public/auth/"

# 写法二：~ + 反斜杠转义空格
open ~/Library/Application\ Support/CodeBuddyExtension/Data/Public/auth/

# ❌ 错误示范：~ 放在双引号里不会展开，且空格会把路径劈成两半
open "~/Library/Application Support/..."   # 会报 "does not exist"
```

> 💡 **找不到文件？** 先确认客户端登录过；再确认你用的是**桌面端**而不是网页版。
> 另外该目录下可能还有带时间戳的备份文件（如 `workbuddy-desktop.2026-08-19T13-48-32-405Z.16533.dade6171-....info`），
> **里面是旧 token，别拿错**，认准 `workbuddy-desktop.info`。

### 3.2 要取哪两个值

用记事本 / VS Code 打开这个 JSON 文件，找这两个字段：

```json
{
  "auth": {
    "accessToken": "eyJhbGciOi...（很长一串，1293 字符）",   ← 这个就是 ACCESS_TOKEN
    "expiresIn": 5184000,
    "expiresAt": 1793202728467,
    ...
  },
  "accounts": [
    {
      "uid": "f2b233c8-3263-xxxx-xxxx-xxxx-xxxxc654",        ← 这个就是 UID（此处为占位，换成你自己的）
      "nickname": "（你的昵称）",
      "lastLogin": true,
      ...
    }
  ]
}
```

| 字段 | 取值位置 | 长什么样 |
|---|---|---|
| `ACCESS_TOKEN` | `auth.accessToken` | `eyJ` 开头的 JWT，约 1293 字符 |
| `UID` | `accounts[].uid` | 36 位 UUID，形如 `f2b233c8-3263-xxxx-xxxx-xxxx-xxxxc654`（以你本机实际为准） |

> 多账号时 `accounts` 是个数组，取 `lastLogin: true` 那条的 `uid`。

### 3.3 （可选）用脚本自动提取，省得手抄

Mac / Linux 用户可以直接用配套的 `workbuddy_token.py`：

```bash
python3 workbuddy_token.py           # 脱敏显示 + 到期剩余天数
python3 workbuddy_token.py --raw     # 输出完整值
python3 workbuddy_token.py --copy    # token 直接进剪贴板，不往终端打（最安全）
```

正常情况下会输出：

```
📄 文件: /Users/xxx/Library/Application Support/CodeBuddyExtension/Data/Public/auth/workbuddy-desktop.info
👤 账号: （你的昵称）
UID          = f2b233c8-3263-…-xxxxc654
ACCESS_TOKEN = eyJhbGci…e7W8WA  (共 1293 字符)
⏰ Token 到期: 2026-10-28 23:26:07  (还剩 60.0 天)
🔄 刷新截止: 2026-11-27 23:26:07
```

---

## 4. 第二步：准备签到脚本


> ⚠️ 用别人的脚本前，请先通读一遍代码，确认它只做签到、不往别处传你的 token。
> 如果你不想走这一步，附录 A 给了 Mac 免青龙的部署思路，可以照着自己写一个（Python 标准库就够）。

---

## 5. 第三步：青龙面板配置

### 5.1 新建脚本文件

1. 左侧菜单 → **脚本管理** → 右上角 **+**
2. 文件名填：`workbuddy_checkin.py`
3. 把脚本内容整个粘进去 → 保存

### 5.2 新建环境变量

1. 左侧菜单 → **环境变量** → **新建变量**
2. 按下表填：

| 项 | 值 |
|---|---|
| 名称 | `WORKBUDDY` |
| 值 | `ACCESS_TOKEN#UID#备注`（见下方格式） |
| 备注 | 选填 |

**值的格式**：用 `#` 分隔三段

```
ACCESS_TOKEN#UID#备注
```

单账号示例：
```
eyJhbGciOiJSUzI1NiIsInR5cCI6...#f2b233c8-3263-xxxx-xxxx-xxxx-xxxxc654#主号
```

**多账号**：一行一个，用换行分隔（不是逗号、不是分号）：
```
eyJxxx.aaa.bbb#u_123456#主号
eyJyyy.ccc.ddd#u_654321#小号A
eyJzzz.eee.fff#u_111222#小号B
```

> ⚠️ 常见错误：token 里有换行被编辑器折行了。粘贴前确认每个账号是**完整一行**。

### 5.3 新建定时任务

1. 左侧菜单 → **定时任务** → **新建任务**
2. 名称：随便，如 `WorkBuddy 每日签到`
3. 命令：`task workbuddy_checkin.py`（或填脚本完整路径）
4. 定时规则：见下一节
5. 保存

### 5.4 测试运行

在定时任务列表右侧点 **运行** 按钮（三角形），然后看日志。

判断成功的标准：
- 日志里出现「签到成功」「已签到」之类字样
- 或第二天去客户端看积分是否 +了

---

## 6. 定时规则怎么填

建议**一天 1~2 次**就够，多了没意义（一天只能签一次）。

| 场景 | Cron 表达式 | 含义 |
|---|---|---|
| 只跑一次 | `30 9 * * *` | 每天 09:30 |
| 早晚各一次（推荐） | `30 9,20 * * *` | 每天 09:30 和 20:30 |
| 保守一点 | `0 10 * * *` | 每天 10:00 |

> 青龙面板默认时区可能是 UTC，比北京时间晚 8 小时。
> 如果发现任务实际在下午才跑，把小时数减 8，或在面板「配置文件」里加 `export TZ=Asia/Shanghai`。

---

## 7. 常见问题排查

| 现象 | 原因 | 怎么办 |
|---|---|---|
| 报「变量不存在 / 未配置」 | 环境变量名不是 `WORKBUDDY`，或没启用 | 检查名称拼写；确认变量是启用状态 |
| 报「401 / Unauthorized」 | token 过期或被顶下线 | 重新登录客户端，重新取一次 token |
| 报「参数错误」 | token 或 uid 复制不完整、多粘了空格 | 重新用 `--copy` 取值，粘贴后检查首尾无空格 |
| 路径不存在（macOS） | 命令行引号/转义问题 | 用 `"$HOME/Library/Application Support/..."` |
| 目录存在但找不到文件 | 客户端从未登录过 | 打开 WorkBuddy 桌面端登录一次 |
| 一直提示"已签到"但积分不涨 | 当天已签过 | 正常，不用管 |
| 积分在减少 | 积分 **30 天**有效期，旧的过期了 | 尽早用掉，别攒着 |
| 任务没按点跑 | 青龙时区是 UTC | 见第 6 节时区说明 |

---

## 8. 三个必须记住的坑

### 坑 1：Token 60 天过期

`expiresAt` 到期后脚本会开始报 401。
**处理方式**：重新登录一次 WorkBuddy 客户端 → 重新取 token → 更新环境变量。

> 缓解办法：在手机日历里设一个 **每 55 天** 的重复提醒，标题写「换 WorkBuddy token」，到点花 1 分钟重取。
> 刷新 token 的截止时间是 `refreshExpiresAt`（比 `expiresAt` 晚约 30 天），但脚本一般不实现自动刷新，
> 所以按 60 天（保险起见 55 天）手动换最稳。

### 坑 2：积分只有 30 天保质期

签到攒的积分**从获得日起 30 天有效**，过期清零。别想着攒一波大的，攒着攒着就没了。

### 坑 3：Token 是明文存的

macOS / Windows 上这个 `workbuddy-desktop.info` 都是**明文**，没有进钥匙串 / DPAPI 加密。
任何能读你用户目录的程序都能拿到它。所以：

- 别把整个 `auth` 目录打包发给别人
- 别在共享机器上登录个人账号
- 第三方脚本务必审代码

---

## 附录 A：Mac 免青龙方案（Python + crontab）

不想为了签到专门装个青龙面板？Mac 上用系统自带的 crontab 就够。

### A.1 思路

```
crontab 每天 09:30
   └─> python3 你的签到脚本
          ├─ 读认证文件拿 token / uid
          ├─ 调签到接口
          └─ 结果写日志 ~/Library/Logs/workbuddy-checkin.log
```

跟青龙方案最大的区别：**token 不落任何配置文件**，每次运行时现读现用。
客户端里刷新了 token，脚本自动跟着变，不用手动去改环境变量。

### A.2 配置步骤

```bash
# 1. 把脚本放到固定位置
mkdir -p ~/.local/bin
cp your_checkin_script.py ~/.local/bin/
chmod +x ~/.local/bin/your_checkin_script.py

# 2. 先手动跑一次，确认能通
python3 ~/.local/bin/your_checkin_script.py

# 3. 加定时任务（每天 09:30 和 20:30）
crontab -e
```

在 crontab 里加这几行：

```cron
SHELL=/bin/zsh
PATH=/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin
TZ=Asia/Shanghai

30 9,20 * * * /usr/bin/python3 "$HOME/.local/bin/your_checkin_script.py" >> "$HOME/Library/Logs/workbuddy-checkin.log" 2>&1
```

```bash
# 4. 确认已生效
crontab -l

# 5. 想看历史结果
tail -20 ~/Library/Logs/workbuddy-checkin.log
```

### A.3 Mac 上 cron 的两个坑

1. **cron 环境里没有完整 PATH** —— 所以上面显式写了 `PATH=`，脚本里也用绝对路径。
2. **首次运行会弹「终端想访问你的文件」授权** —— 这是 macOS TCC 保护。
   如果日志里报权限错误，去
   `系统设置 → 隐私与安全性 → 完全磁盘访问权限`，
   把 `/usr/sbin/cron` 或你用的终端加进去。

> 顺带一提：macOS 上 cron 正被 launchd 取代，但目前仍完全可用。
> 若哪天 cron 不跑了，可以改用 launchd 的 `StartCalendarInterval`，效果一样。

---

## 附录 B：速查卡

```
┌─ 认证文件 ────────────────────────────────────────────────┐
│ macOS   ~/Library/Application Support/CodeBuddyExtension/ │
│                 Data/Public/auth/workbuddy-desktop.info   │
│ Win     %LOCALAPPDATA%\CodeBuddyExtension\Data\Public\    │
│                 auth\workbuddy-desktop.info               │
└───────────────────────────────────────────────────────────┘

ACCESS_TOKEN  = auth.accessToken        (eyJ 开头, ~1293 字符)
UID           = accounts[].uid          (36 位 UUID)

环境变量名: WORKBUDDY
变量值格式: ACCESS_TOKEN#UID#备注        (多账号换行分隔)

定时规则:   30 9,20 * * *               (每天 09:30 / 20:30)
Token 有效期: 60 天
积分有效期: 30 天
```

---

*文档整理时间：2026-08-29 · macOS 部分已在本机实测验证*
