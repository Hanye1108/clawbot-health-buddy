# 部署方式一：云服务器

适合已经有一台 Linux 云服务器的情况。装好后接着看 [after-deploy.md](after-deploy.md)。

## 0. 服务器要求

| 项目 | 要求 |
|---|---|
| 系统 | Ubuntu 20.04+ / Debian 11+ / CentOS 8+ 等常见 Linux |
| 配置 | 1 核 1GB 足够（机器人本身只占 100～150MB 内存） |
| 网络 | 能访问外网：`ilinkai.weixin.qq.com`、`api.deepseek.com`、和风天气、GitHub、pip 源 |
| 端口 / 域名 / 备案 | **都不需要**。机器人是主动去连微信，不是微信来连它 |
| 权限 | root |

服务器上已经跑着别的东西（网站、1Panel、宝塔、Docker 等）也没关系，互不影响。只有可选的「趋势图网页」会用到一个端口（默认 5000），见 after-deploy.md。

## 1. 一键安装

用 root 登录服务器，复制下面这条命令（**国内服务器**保留 `--mirror`，海外服务器可以去掉）：

```bash
curl -fsSL https://raw.githubusercontent.com/Hanye1108/clawbot-health-buddy/main/deploy/bootstrap.sh | bash -s -- --mirror
```

它会：安装 git → 下载代码到 `/opt/clawbot-health-buddy` → 安装 Python 和依赖 → 生成配置文件 → 注册开机自启服务 → 设置每天凌晨 4 点自动备份数据库。

**国内服务器连不上 GitHub 时**（命令卡住或报错，很常见），换成手动两步：

1. 在自己电脑上打开 https://github.com/Hanye1108/clawbot-health-buddy ，点 Code → Download ZIP。
2. 用 SFTP、1Panel 或宝塔的文件管理，上传到服务器的 `/opt` 并解压，把文件夹改名为 `clawbot-health-buddy`，然后：

```bash
cd /opt/clawbot-health-buddy
bash deploy/install.sh --mirror
```

或者网络时好时坏的话，直接用 git 重试几次：

```bash
cd /opt && for i in 1 2 3 4 5; do timeout 120 git clone --depth 1 https://github.com/Hanye1108/clawbot-health-buddy.git && break; sleep 5; done
cd /opt/clawbot-health-buddy && bash deploy/install.sh --mirror
```

✅ 检查点：最后输出「✅ 安装完成」，并提示运行 `python -m bot setup`。

## 2. 下一步

接着看 👉 [after-deploy.md](after-deploy.md)：运行配置向导填密钥、扫码绑定微信、测试。

## 服务器上的常见问题

| 现象 | 处理 |
|---|---|
| `pip install` 很慢或超时 | 重新运行 `bash deploy/install.sh --mirror` |
| `git clone` / `git pull` 报 `GnuTLS recv error` 或一直卡住 | 国内连 GitHub 不稳定，用上面的 ZIP 方式，或者多重试几次 |
| 想换到另一台服务器或开发板 | 见 after-deploy.md 的「搬家」一节 |
| 服务器 80 端口已被 Nginx / OpenResty 占用 | 不影响机器人。只有用趋势图网页时才需要配反向代理，见 after-deploy.md |
