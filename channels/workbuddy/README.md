# WorkBuddy 渠道 · 配置获取指南

本指南教你在本机一步步拿到「新建任务」表单里需要填写的信息。
(技术原理与更多排错见 `docs/WorkBuddy自动签到配置说明.md`)

## 需要填什么

| 配置项 | 是否必填 | 说明 |
|---|---|---|
| 账号列表 | 可选 | 每行一个: `ACCESS_TOKEN#UID#备注`。**留空则自动读本机登录文件** |
| 仅测试(不实际请求) | 否 | 勾选后只走流程不发请求, 用于验证配置 |
| WxPusher AppToken / UID | 否 | 想把签到结果推送到微信才需要填 |

## 方式一(推荐): 让平台自动读取本机登录文件

**前提**: 在这台电脑上登录过 WorkBuddy 桌面客户端。

1. 什么都不用填, 「账号列表」留空即可。
2. 创建任务后先手动运行一次, 看日志确认加载了账号(日志会显示"从本机登录文件加载账号")。
3. 令牌过期(约 60 天)时, 只需重新打开 WorkBuddy 客户端登录一次, 脚本下次运行自动用新令牌, **无需改任何配置**。

## 方式二: 手动填写「账号列表」(适合部署在别的机器/多账号)

### 第 1 步: 在你登录过 WorkBuddy 的电脑上打开认证文件

按 `Win + R` 输入下面路径回车(直接粘到文件管理器地址栏也行):

```
%LOCALAPPDATA%\CodeBuddyExtension\Data\Public\auth\
```

找到文件 `workbuddy-desktop.info`, 用记事本或 VS Code 打开。
(注意: 目录里可能还有带时间戳的备份文件, 别拿错, 认准不带时间戳的这个)

macOS: `~/Library/Application Support/CodeBuddyExtension/Data/Public/auth/workbuddy-desktop.info`
Linux: `~/.config/CodeBuddyExtension/Data/Public/auth/workbuddy-desktop.info`

### 第 2 步: 复制 ACCESS_TOKEN

在文件里搜索 `"accessToken"`, 复制冒号后面那串 `eyJ` 开头的长文本(约 1293 个字符, 引号不要带上)。

### 第 3 步: 复制 UID

在文件里搜索 `"uid"`, 复制那个 36 位的 UUID(形如 `f2b233c8-3263-xxxx-xxxx-xxxx-xxxxc654`)。
多账号时取 `"lastLogin": true` 那一条的 uid。

### 第 4 步: 按格式填入

每行一个账号, 三段用 `#` 分隔, 备注可省略:

```
ACCESS_TOKEN#UID#主号
ACCESS_TOKEN#UID#小号A
```

⚠ 常见错误: 粘贴时 token 被编辑器折行/带空格。确认每个账号是完整一行。

## WxPusher 微信推送(可选)

1. 打开 `https://wxpusher.zjiecode.com`, 注册并登录。
2. 进入「开发者中心」→ 创建应用, 得到 **APP_TOKEN**(填入 WxPusher AppToken)。
3. 用微信扫码关注你的应用的二维码后, 在应用的「用户管理」里找到你的 **UID**(填入 WxPusher UID)。

## 常见问题

- **日志报"登录文件令牌已被客户端加密"**: 新版客户端会把令牌加密存储(此时无法自动读取)。解决办法: 在任务配置「账号列表」中手动填写 `ACCESS_TOKEN#UID#备注`。取 token 的时机: 客户端刚登录完、或重启客户端后尽快取(文件可能处于明文状态); 若文件一直是密文, 可在客户端登录其他账号前先取。
- **日志报 401/令牌失效**: token 约 60 天过期, 或该账号在其他设备登录被顶下线。重新登录 WorkBuddy 客户端再取一次(方式一无需操作, 重登即可; 方式二需替换 token)。
- **切换/退出账号后旧 token 还能用吗**: 实测切换登录后旧账号 token 短期内仍有效(直到 JWT 到期或被服务端吊销), 但不能保证长期有效; 若任务报 401, 重新登录该账号取新 token 更新配置。
- **提示未找到任何账号**: 方式一依赖本机登录文件, 确认这台电脑登录过客户端; 或改用方式二手动填写。
- **签到接口幂等**: 一天运行多次不会重复发积分, 早晚各跑一次更保险。
