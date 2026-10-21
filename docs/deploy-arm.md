# 部署方式二：ARM 开发板（放在家里的小盒子）

机器人本身几乎不耗算力：聊天、看图、天气、搜索都在云端，板子只负责收发微信消息、存数据库、按时提醒。
而且它是**主动连微信**的，不需要公网 IP、不需要开端口，插上电、连上家里网络就能用。装好后接着看 [after-deploy.md](after-deploy.md)。

## 0. 选板子

| 项目 | 要求 | 说明 |
|---|---|---|
| 内存 | **512MB 以上**，1GB 更从容 | 运行时约 100～150MB；安装依赖时更多，小内存板子安装脚本会自动建交换文件 |
| 架构 | **推荐 64 位（aarch64）** | 32 位（armv7）也能装：脚本会改用系统自带的加密库，避开现场编译（否则要装 Rust、编译很久） |
| 系统 | Debian / Ubuntu / Armbian，带 systemd | 安装脚本按这些系统写的 |
| Python | 3.8 以上 | Debian 11 / Ubuntu 20.04 及以后的系统自带 |
| 存储 | 8GB 以上，质量好一点的 TF 卡或 eMMC | 数据库很小，但劣质 TF 卡容易坏 |
| 网络 | 网线或 Wi-Fi，能上外网 | |

百元左右常见的选择，比如香橙派 Zero 3（1GB 版）、树莓派 Zero 2 W，或者二手「玩客云」刷 Armbian（很便宜，但是 32 位）。手上已有的能跑 Linux 的板子（比如 RK 系列）也行。具体型号的价格和货源变化快，以购买时为准。

## 1. 准备系统（人来做）

1. 按板子厂家的说明，把 Debian / Ubuntu / Armbian 系统刷进 TF 卡或 eMMC。
2. 接上网线（或配好 Wi-Fi），开机。
3. 在路由器后台找到板子的 IP，从电脑 SSH 登录（用户名密码看系统说明，Armbian 首次登录会让你改 root 密码）：

```bash
ssh root@板子的IP
```

4. **确认时间是对的**：运行 `date`。开发板大多没有电池时钟，时间错了会导致 HTTPS 连接全部失败。不对的话：

```bash
timedatectl set-ntp true      # 开启网络对时，等几十秒再看 date
timedatectl set-timezone Asia/Shanghai
```

✅ 检查点：能 SSH 登录，`date` 显示的是现在的时间，`ping -c 3 api.deepseek.com` 能通。

## 2. 一键安装

和服务器一样，用 root 运行（国内保留 `--mirror`）：

```bash
curl -fsSL https://raw.githubusercontent.com/Hanye1108/clawbot-health-buddy/main/deploy/bootstrap.sh | bash -s -- --mirror
```

安装脚本会自动识别板子：显示架构和内存；32 位板子改用系统自带的加密库；内存不到 1GB 且没有交换分区时，创建 1GB 的 `/swapfile`；检查系统时间。**在开发板上安装依赖可能要 5～15 分钟**，耐心等。

连不上 GitHub 时（命令卡住或报错），在电脑上下载 ZIP（https://github.com/Hanye1108/clawbot-health-buddy → Code → Download ZIP），用 `scp` 传到板子上：

```bash
# 在电脑上运行
scp clawbot-health-buddy-main.zip root@板子的IP:/opt/
# 在板子上运行
cd /opt && apt-get install -y unzip && unzip clawbot-health-buddy-main.zip && mv clawbot-health-buddy-main clawbot-health-buddy
cd /opt/clawbot-health-buddy && bash deploy/install.sh --mirror
```

✅ 检查点：最后输出「✅ 安装完成」，并提示运行 `python -m bot setup`。

## 3. 下一步

接着看 👉 [after-deploy.md](after-deploy.md)：运行配置向导填密钥、扫码绑定微信、测试。

## 开发板特有的注意事项

- **断电、断网**：机器人会停，恢复后自动重连。重新上电时 5 分钟内到点的提醒会补发，更早错过的就跳过了。
- **TF 卡保护**：数据库和微信登录凭据都在 `data/` 里，每天凌晨 4 点自动备份到 `data/backup/`（同一张卡上）。建议隔一段时间把 `data/` 整个复制到电脑上：
  ```bash
  # 在电脑上运行
  scp -r root@板子的IP:/opt/clawbot-health-buddy/data ./clawbot-data-备份
  ```
- **趋势图网页**：家里没有公网 IP，手机连着家里 Wi-Fi 才能打开。`.env` 里 `PUBLIC_URL` 填 `http://板子的局域网IP:5000`。最好在路由器里给板子固定 IP。不需要就不用管，其他功能不受影响。
- **省电**：板子功耗通常只有几瓦，一直开着也没关系。

## 开发板上的常见问题

| 现象 | 处理 |
|---|---|
| 安装时卡在 `Building wheel for cryptography` 很久 | 32 位板子没识别出来。`apt install python3-cryptography`，删掉 `.venv` 后重新运行安装脚本 |
| 安装时报内存不足 / 被 Killed | 确认有交换分区：`free -m`。没有的话重新运行安装脚本（会自动创建），或手动建一个 |
| `check` 里所有联网项都失败，报证书错误 | 多半是系统时间不对，见第 1 节第 4 步 |
| 重启板子后机器人没起来 | `systemctl status clawbot-health-buddy` 看原因；常见是开机时网络还没就绪，服务会每 10 秒自动重试 |
| 想从服务器搬到板子上 | 见 after-deploy.md 的「搬家」一节 |
