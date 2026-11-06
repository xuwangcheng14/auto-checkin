# Trae CN 渠道 · 配置获取指南

本渠道需要两样东西：**网页 Cookie**（登录凭证）和 **设备号**（领取积分时服务端校验的客户端设备标识）。
两者都拿到一次即可，之后每天自动签到。

## 需要填什么

| 配置项 | 是否必填 | 说明 |
|---|---|---|
| 网页 Cookie | 必填 | 浏览器里 trae.cn 的**完整** Cookie 字符串（约 2000 字符） |
| 设备号 | 建议填 / 可从文件读 | 16 位数字，来自装有 Trae 客户端的电脑；留空则自动读 `data/trae_device.json` |
| 仅查询(不领取) | 可选 | 打开后只验证 Cookie、看今日是否已签到，不调用领取接口，**不需要设备号** |
| 请求来源标识(高级) | 可选 | 默认 `0`＝自动探测（依次试三种请求体，命中即记住）；一般不用改 |
| API 域名(高级) | 可选 | 默认 `https://api.trae.cn`，一般不用改 |
| 网页域名(高级) | 可选 | 默认 `https://www.trae.cn`，仅作 Origin/Referer 与提示用 |

## 第一步：获取设备号（一次即可）

服务端在**领取积分**时会校验请求头 `x-device-id` 必须是客户端真实签发的设备号：

| 情况 | 服务端返回 |
|---|---|
| 不带设备号 | `code=9004`「订单参数错误」 |
| 随便编一个 16 位数字 | `code=9074`「当前参与用户太多，请稍后再试」（伪装成限流的**设备校验拒绝**） |
| 本机真实设备号 | `code=0`，领取成功 |

获取方法（在**装过 Trae 客户端的电脑**上执行，Windows / macOS / Linux 均可）：

1. 确认这台电脑装过 **Trae CN** 或 **TRAE SOLO CN** 客户端（登录与否都行，设备号在安装时就生成了）
2. 把本项目放到这台电脑上，在项目根目录执行：

```
python tools/trae_device.py
```

3. 屏幕会列出声明的客户端与 16 位设备号，并写入 **`data/trae_device.json`**（本平台会自动读取这个文件）
4. 如果平台跑在服务器上：把生成的 `data/trae_device.json` 上传到服务器的 `data/` 目录即可；
   或者直接复制里面 `device_id` 的值，粘贴到任务配置的「设备号」输入框

> 多账号注意：Trae 限制「**一台设备每天只能签到 1 个账号**」（第二个账号会收到 `code=9095`
> 「当前设备今日已经签到」）。同一台电脑装了多个 Trae 客户端时，本工具会导出多个设备号，
> 请给每个账号配**不同的设备号**，否则同一天只有一个账号能领到积分。
> 工具默认会优先选「该客户端还没登录任何账号」的设备号——这种设备号不会被客户端里的账号
> 抢先用掉当天名额，最适合给本平台的任务用。

## 第二步：抓取网页 Cookie（约 2 分钟）

1. 用 Chrome / Edge 打开 `https://www.trae.cn`，确认右上角是**已登录**状态（头像正常显示）
2. 按 **F12** 打开开发者工具 → 点顶部 **Network（网络）** 标签
3. 按 **F5** 刷新页面，等请求列表出现内容
4. 在筛选框里输入 `api.trae.cn`（或 `cloudide`），点任意一条请求
5. 右侧切到 **Headers（标头）** → 下拉到 **Request Headers（请求标头）** → 找到 **`Cookie`** 这一行
6. 右键该行的值 → **复制值**（整串，很长），粘贴到任务配置的「网页 Cookie」输入框

**必须复制完整的一整串**，不要只挑其中一两个字段。关键字段及作用：

| Cookie 字段 | 作用 | 缺失后果 |
|---|---|---|
| `X-Cloudide-Session` | 换取 JWT 的核心会话字段 | 报「换取 JWT 失败 / get session empty」 |
| `sessionid` / `sessionid_ss` / `sid_tt` | 网页登录态（passport） | 报「Cookie 已失效」 |
| `sid_guard` | 不参与登录，仅用于解析名义到期时间并展示 | 只是少了「令牌有效期」展示 |
| `ttwid` 等风控字段 | 只在访问 `www.trae.cn` 时需要 | 一般不影响（本渠道接口都走 `api.trae.cn`） |

备用办法：F12 → **Application（应用）** 标签 → 左侧 **Cookies** → `https://www.trae.cn`，
把列表里的条目按 `名称=值` 用 `; ` 拼成一行也可以，但直接复制请求头里的 Cookie 最不容易出错。

## 前提条件

- 浏览器必须处于**已登录**状态；建议用日常使用的浏览器窗口复制，不要用无痕窗口
- 复制的是**网页版**（`www.trae.cn`）的 Cookie，不是 Trae 客户端里的登录态
- Cookie **名义有效期约 60 天**（写在 `sid_guard` 里），但服务端可能提前作废：
  在别处重新登录、点过登出、改过密码都会立刻失效。平台「令牌有效期」列显示的是
  **名义到期时间**（所以剩很多天也可能已经失效，以任务执行结果为准）
- 每次执行都会用 Cookie 现换一个短期 JWT（约 8 小时），不需要你提供 token

## 这个渠道做什么

- 每天签到一次领取积分（国内版每日约 **100 分**，具体以官方页面为准；未见连签加成）
- 先查状态、**未签到才领取**，重复执行是安全的（幂等）；已签到时服务端返回「今日已签到」也算成功
- 每次执行后上报**积分余额**（任务列表「剩余积分」列）与 **Cookie 到期时间**（「令牌有效期」列）
- 签到成功 / 今日已签到 / Cookie 失效 / 活动未开启 / 设备号问题，都会在任务列表给出明确结论

## 常见问题

- **报「缺少设备号, 无法领取积分」**：按上面「第一步」生成 `data/trae_device.json`，或在任务配置里粘贴设备号。
- **报「领取参数不被接受(code=9004)」**：设备号缺失或格式不对（必须是客户端导出的 16 位数字，别手改）。
- **报「领取积分失败(code=9074)」**：设备号不被服务端认可（官方文案「当前参与用户太多」是误导）。用 `tools/trae_device.py` 重新导出，**不要手填随机数**。
- **报「本机今日已签到(code=9095)」**：这个设备号今天已经被另一个 Trae 账号用掉了。给本任务换另一个设备号，或明天再签（官方限制：一台设备每天只能签到一个账号）。
- **报「Cookie 已失效」**：网页版登录态过期了（名义未到期也可能失效）。重新登录 `www.trae.cn`，按第二步重新抓一次 Cookie 更新任务即可。
- **报「换取 JWT 失败 / get session empty」**：Cookie 复制不完整，重点检查有没有漏掉 `X-Cloudide-Session` 和 `sessionid`。请整串复制，不要手工挑字段。
- **报「签到活动未开启」**：官方当期活动关闭或该账号不在活动范围，不是配置问题。
- **想先验证 Cookie 有没有抓对**：把「仅查询(不领取)」打开，点「▶ 立即运行」，看日志里是否出现「登录态有效」。
- **多账号**：一个任务对应一个账号；每个账号再配**不同的设备号**（否则同一天只有一个能签到成功）。
- **国际版 trae.ai**：**不支持**。国际版是另一套产品线（不同域名与接口），且没有每日签到活动；本渠道只适配国内版 `trae.cn`。
- **安全提醒**：Cookie 等同于登录凭证，不要发给他人、不要贴到公开场合；Cookie 只保存在本平台的任务配置里，设备号文件在 `data/`（已 gitignore）。

## 实现原理与参考

签到接口是逆向自官方客户端/网页端的非公开接口，可能随官方改版失效。全部接口都在 `api.trae.cn`（纯 requests 即可，无 WAF 挑战）：

1. `POST /cloudide/api/v3/trae/CheckLogin` 校验网页会话（Cookie），返回登录态与 API 地址
2. `POST /cloudide/api/v3/common/GetUserToken` 用会话换短期 JWT（`Authorization: Cloud-IDE-JWT <jwt>`）
3. `POST /trae/api/v2/ug/checkin_credits/status` 查今日签到状态（`checked_in` 账号维度、`did_checked_in` 设备维度、`enable` 活动开关、`credits`/`extra_credits` 每日奖励额）
4. `POST /trae/api/v2/ug/checkin_credits/claim` 领取每日积分（**必须带 `x-device-id`**）：`code=0` 成功、`9004` 缺设备号、`9074` 设备号不被认可、`9095` 本机今日已签，随后回查状态确认
5. `POST /trae/api/v2/pay/web_user_ent_usage`（客户端谱系为 `ide_user_ent_usage`）查实时积分余额（失败不影响签到结论）

设备号来自客户端 `aha/TinyStorage` 里的 iCube 加密串（AES-128-CBC + SHA-512 派生密钥），
`tools/trae_device.py` 已把这套解密实现好了，只需在有客户端的机器上跑一次。

主要参考的社区实现与资料：

- `https://github.com/devilardis/auto-checkin`（Python，Trae 网页 Cookie 模式；CheckLogin/GetUserToken 流程与它一致）
- `https://github.com/3338320742zh-collab/wb-trae-checkin`（Node，客户端 token + 真实设备号模式）
- `https://github.com/Minatoxiaohu/agent-auto-signin`（Python，客户端本地存储解密模式）
- `https://github.com/mmqz/cpa-multi-plugins`（反编译官方包整理的接口协议文档）
- `https://github.com/dingminhua/dsh-connect-trae`（明确了 claim 需要真实设备号、以及「每台设备每天一次」的限制）