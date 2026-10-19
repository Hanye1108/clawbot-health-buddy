# 部署指南：从一台干净的服务器到微信里能聊天

这份文档写给两类读者：

- **AI 部署助手**（Claude Code、Cursor 等能在服务器上执行命令的 AI）：把整个文件交给它，它可以照着一步一步做完。
- **新手**：不懂原理也没关系，按顺序复制命令即可。

全程大约 30 分钟，其中大部分时间花在注册账号上。

---

## 给 AI 部署助手的规则（请先读）

1. **按章节顺序执行**，每一节末尾都有「✅ 检查点」。检查点没通过就停下，先看本文末尾的「常见问题」，不要跳过。
2. **密钥绝不经过聊天。** 不要让用户把 API Key 发给你，也不要把 `.env` 的内容打印出来。让用户自己在服务器上编辑 `.env`（第 5 节），你只用 `python -m bot check` 验证结果。
3. **以下事情只能由人来做**，到了这一步就停下来告诉用户怎么做，等用户确认：
   - 注册 DeepSeek / 和风天气账号、充值、复制密钥（第 1、2 节）
   - 用手机微信扫码（第 7 节）
4. 默认安装目录是 `/opt/clawbot-health-buddy`，需要 root 权限。所有命令都假设在这个目录里执行，除非另有说明。
5. 每完成一节，用一句话告诉用户进展。

---

## 0. 准备清单

| 需要的东西 | 说明 |
|---|---|
| 一台 Linux 服务器 | Ubuntu 20.04+ / Debian 11+ / CentOS 8+ 均可，1 核 1G 足够。**不需要域名、不需要备案、不需要开放任何端口**（服务器主动连微信，而不是微信来连服务器） |
| 服务器能访问外网 | 需要访问 `ilinkai.weixin.qq.com`、`api.deepseek.com`、和风天气、GitHub（或镜像）、pip 源 |
| 一个开了 ClawBot 的微信 | 手机微信 → 我 → 设置 → 插件，能看到「微信ClawBot」。看不到说明你的微信还没开放这个功能（腾讯在灰度推送），可以先用公众号测试号通道，见 [docs/MP_TEST_ACCOUNT.md](docs/MP_TEST_ACCOUNT.md) |
| DeepSeek 账号 | 用于对话和看图，需少量充值 |
| 和风天气账号 | 用于天气，免费额度够个人使用 |

---

## 1. 申请 DeepSeek API Key（人来做）

1. 打开 https://platform.deepseek.com ，注册登录。
2. 充值少量金额（个人使用，几十元能用很久）。
3. 左侧「API keys」→ 创建，复制保存（只显示一次）。

✅ 检查点：手里有一串 `sk-` 开头的 Key。

---

## 2. 申请和风天气 Key 和 API Host（人来做）

1. 打开 https://console.qweather.com ，注册登录。
2. 「项目管理」→ 创建项目 → 添加凭据，**认证方式选 API KEY**，复制 Key。
   - 页面上还会出现「凭据 ID」，本项目用不到。
3. 打开控制台的「设置」页面，找到 **API Host**，形如 `xxxxxxxxxx.re.qweatherapi.com`，复制。

⚠️ 两个最常见的坑：
- 官方文档里的 `abcxyz.qweatherapi.com` 是**示例**，不能用，一定要用设置页里你自己的。
- 和风天气的旧版 v7 接口已对新账号停用，本项目用的是新版接口，不受影响。

✅ 检查点：手里有一个 API Key 和一个 API Host。

---

## 3. 下载代码到服务器

```bash
sudo -i
cd /opt
git clone https://github.com/<作者>/clawbot-health-buddy.git
cd /opt/clawbot-health-buddy
```

> 把 `<作者>` 换成仓库实际所在的 GitHub 用户名。
> 国内服务器 clone GitHub 很慢或失败时：在自己电脑上下载仓库 ZIP，用 SFTP、宝塔或 1Panel 的文件管理上传到 `/opt` 再解压，目录名改成 `clawbot-health-buddy`。

✅ 检查点：`ls /opt/clawbot-health-buddy` 能看到 `bot/`、`deploy/`、`DEPLOY.md`。

---

## 4. 一键安装

```bash
cd /opt/clawbot-health-buddy
bash deploy/install.sh            # 海外服务器
bash deploy/install.sh --mirror   # 国内服务器（用清华 pip 镜像）
```

脚本会：安装 Python 和 venv → 安装依赖 → 生成 `.env` → 注册开机自启服务（先不启动）→ 设置每天凌晨 4 点自动备份数据库。可以重复运行，不会覆盖已有配置和数据。

✅ 检查点：最后输出「✅ 安装完成」。

---

## 5. 填写配置（人来做）

```bash
nano /opt/clawbot-health-buddy/.env
```

把第 1、2 节拿到的内容填进去（`Ctrl+O` 回车保存，`Ctrl+X` 退出）：

```ini
DEEPSEEK_KEY=sk-你的key
QWEATHER_KEY=你的和风天气key
QWEATHER_HOST=https://你自己的.re.qweatherapi.com
CHANNEL=ilink
```

其余项保持默认即可。等号两边不要有空格，值不用加引号。

> 给 AI 的提示：这一步请让用户自己操作，然后进入第 6 节验证。不要读取或打印 `.env`。

---

## 6. 自检

```bash
cd /opt/clawbot-health-buddy
.venv/bin/python -m bot check
```

✅ 检查点：除了「已绑定 ClawBot 账号」这一项（下一步才做），其他都是 ✅。

| 失败项 | 处理 |
|---|---|
| 配置项 XXX | `.env` 里没填或拼错了 |
| DeepSeek 对话模型 | Key 错误、余额不足，或模型名已变化（看 DeepSeek 官方文档，改 `.env` 里的 `DEEPSEEK_MODEL`） |
| 和风天气 | Key 错、Host 错（见第 2 节的坑）、Host 少了 `https://`（程序会自动补，但拼写错就不行） |
| 能访问微信 iLink 服务器 | 服务器出网受限，检查安全组/防火墙出方向规则 |

---

## 7. 绑定微信 ClawBot（人来扫码）

```bash
cd /opt/clawbot-health-buddy
.venv/bin/python -m bot login me
```

- 终端会打出一个二维码。**谁扫码，机器人就出现在谁的微信里。** 先用你自己的微信扫，测试没问题再给别人用（第 9 节）。
- 终端二维码扫不出来：把终端窗口拉大，或复制终端里打印的链接，用任意二维码生成工具转成二维码再扫。
- 扫码后在手机上确认。终端显示「✅ 登录成功」即可。
- `me` 是给这个账号起的名字，凭据保存在 `data/ilink/me.json`（权限 600，只有 root 可读）。

✅ 检查点：`.venv/bin/python -m bot accounts` 能列出 `me`。

---

## 8. 启动并验证

```bash
systemctl start clawbot-health-buddy
systemctl status clawbot-health-buddy --no-pager      # 应为 active (running)
journalctl -u clawbot-health-buddy -n 30 --no-pager    # 应看到「已启动，通道：ilink」和「开始接收消息」
```

然后在手机微信里找到 ClawBot 的聊天，依次测试：

| 发送 | 预期 |
|---|---|
| `你好` | 先收到一段自我介绍（说明是 AI、数据存在哪、怎么关提醒），再收到回复 |
| `我在西安长安区，今天要带伞吗` | 返回真实天气（说明天气接口通了） |
| `我是女生，28岁，165cm，60公斤，平时不怎么运动` | 保存资料并给出每日热量建议 |
| `午饭吃了一碗牛肉面` | 估算热量并记录，告诉你今天合计 |
| 发一张食物照片 | 识别食物、给出热量范围并追问一句 |
| `快走了40分钟` | 估算运动消耗 |
| `#test morning` | 立即触发一次早安天气推送（其他：`#test lunch`、`#test water`、`#test weigh`、`#test weekly`） |
| `清除数据` | 删除这个人的所有记录 |

✅ 检查点：前四条都正常。照片如果失败，看第 11 节「已知问题」。

---

## 9. 给家人/朋友用

1. 让对方用**她自己的微信**扫码，给账号起另一个名字：
   ```bash
   .venv/bin/python -m bot login her
   systemctl restart clawbot-health-buddy
   ```
2. 她在 ClawBot 里发一句话，就会收到自我介绍。
3. **请当面说清楚**：这是 AI，热量是估算不是医嘱；聊天和健康记录存在你的服务器上，管理员技术上能看到（请承诺并做到不去看）；发「关闭提醒」可停止推送，发「清除数据」可删除全部记录。

每个人的数据按微信身份分开存放，互不可见。

---

## 10. 日常运维

| 要做的事 | 命令 |
|---|---|
| 看实时日志 | `journalctl -u clawbot-health-buddy -f` |
| 重启（改了 `.env` 或代码后） | `systemctl restart clawbot-health-buddy` |
| 停止 | `systemctl stop clawbot-health-buddy` |
| 更新代码 | `cd /opt/clawbot-health-buddy && git pull && .venv/bin/pip install -r requirements.txt && systemctl restart clawbot-health-buddy` |
| 手动备份 | `bash deploy/backup.sh`（备份在 `data/backup/`，按星期保留 7 份） |
| 从备份恢复 | `systemctl stop clawbot-health-buddy && cp data/backup/bot-3.db data/bot.db && systemctl start clawbot-health-buddy` |
| ClawBot 登录过期（日志出现「需要重新扫码」） | `.venv/bin/python -m bot login me` 然后重启 |
| 改提醒时间 | 编辑 `bot/core.py` 末尾的 `SCHEDULE`，然后重启 |
| 改语气、提醒文案 | 编辑 `bot/prompts.py`，然后重启 |

默认提醒时间（北京时间）：07:00 早安天气、08:30 早餐、10:30 喝水、12:30 午餐、15:30 喝水、19:00 晚餐、周日 07:30 称重（5 天内记过就不提醒）、周日 20:30 周总结。已经记录过的那一餐不会再问。

---

## 11. 常见问题

| 现象 | 原因与处理 |
|---|---|
| 服务启动后马上退出，日志说「没有找到 ClawBot 账号」 | 还没做第 7 节 |
| 微信里发消息没反应，日志有「忽略非扫码人的消息」 | 发消息的人和扫码的人对不上。如果确认是本人，在 `.env` 设 `ILINK_ONLY_OWNER=0` 后重启 |
| 日志出现「登录已过期，需要重新扫码」 | 运行 `python -m bot login <名字>` 重新扫码，再重启服务 |
| 回复「我刚刚走神了」 | 看日志里的报错：多半是 DeepSeek 余额不足、Key 失效或模型名变了 |
| 天气查不到 | 跑一遍 `python -m bot check` 看和风天气那一项 |
| 主动提醒收不到，但聊天正常 | 主动推送依赖对方最近一条消息带来的会话令牌，对方很久没说话后可能失效（见下方已知问题）。日志会提示「主动推送失败」；对方下次说话时，没送达的早安天气、周总结会自动补发 |
| 照片识别失败 | 日志里看是「下载/解密失败」还是「图片识别失败」：前者是微信图片格式问题（见已知问题），后者检查 `VISION_MODEL` 是否是当前支持图片的模型名 |
| `pip install` 很慢或超时 | 重新运行 `bash deploy/install.sh --mirror` |

### 已知问题与限制

- **主动推送的时效**：iLink 协议规定发消息要带对方最近消息里的 `context_token`。这个令牌在对方多久不说话后失效，目前没有官方说明，项目作者正在实测，结论会更新到 [docs/PROGRESS.md](docs/PROGRESS.md)。
- **ClawBot 图片格式**：图片的下载和解密是按社区整理的协议文档实现的，还没有在真实环境里完整验证。如果失败，请在 Issue 里附上日志（日志不含密钥）。
- **模型名**：DeepSeek 的模型名会随版本变化，`check` 报模型错误时以官方文档为准修改 `.env`。
- **ClawBot 是腾讯的新功能**（2026 年 3 月上线），接口和规则以后可能调整，请遵守微信的使用规范。
