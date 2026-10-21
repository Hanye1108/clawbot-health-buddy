#!/usr/bin/env bash
# =====================================================================
# 一键安装 clawbot-health-buddy（服务器和 ARM 开发板通用）
#
# 用法（在项目目录里，用 root）：
#   bash deploy/install.sh            # 自动选择 pip 源（官方源装失败会自动换清华镜像）
#   bash deploy/install.sh --mirror   # 直接用清华 pip 镜像（国内推荐）
#
# 会自动处理：
#   - 识别 x86_64 / aarch64 / armv7 架构；armv7 用系统自带的加密库，避免现场编译
#   - 内存小于 1GB 且没有交换分区时，创建 1GB 交换文件（开发板常见）
#   - 检查系统时间（开发板没有电池时钟，时间不对会导致 HTTPS 失败）
# 可以重复运行：不会覆盖已填写的 .env、persona.yaml 和已有数据。
# =====================================================================
set -euo pipefail

APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
SERVICE=clawbot-health-buddy
MIRROR=https://pypi.tuna.tsinghua.edu.cn/simple
USE_MIRROR=0
for arg in "$@"; do
  case "$arg" in
    --mirror) USE_MIRROR=1 ;;
    *) echo "未知参数：$arg"; exit 1 ;;
  esac
done

if [[ $EUID -ne 0 ]]; then
  echo "请用 root 运行：sudo bash deploy/install.sh"
  exit 1
fi

ARCH="$(uname -m)"
MEM_MB="$(awk '/MemTotal/ {print int($2/1024)}' /proc/meminfo 2>/dev/null || echo 0)"
case "$ARCH" in
  x86_64|amd64)  PLATFORM="x86_64（服务器/电脑）" ;;
  aarch64|arm64) PLATFORM="ARM 64 位" ;;
  armv7*|armhf)  PLATFORM="ARM 32 位（armv7）" ;;
  *)             PLATFORM="$ARCH（未测试过的架构，尽力安装）" ;;
esac
echo "==> 设备：$PLATFORM，内存 ${MEM_MB}MB，安装目录 $APP_DIR"
if (( MEM_MB > 0 && MEM_MB < 400 )); then
  echo "    ⚠️  内存不足 400MB，可能跑不动。建议使用 512MB 以上内存的设备。"
fi

echo "==> [1/7] 检查系统时间"
YEAR="$(date +%Y)"
if (( YEAR < 2025 )); then
  echo "    ⚠️  系统时间是 $(date)，明显不对（开发板没有电池时钟时很常见）。"
  echo "       时间不对会导致 HTTPS 连接失败。正在尝试开启网络对时……"
  timedatectl set-ntp true 2>/dev/null || true
  sleep 5
  echo "       现在时间：$(date)。如果还不对，请先修好时间再安装。"
else
  echo "    时间正常：$(date '+%Y-%m-%d %H:%M')"
fi

echo "==> [2/7] 安装系统依赖"
SYS_CRYPTO=0
if command -v apt-get >/dev/null 2>&1; then
  apt-get update -y
  PKGS=(python3 python3-venv python3-pip ca-certificates cron tzdata)
  # armv7 上 cryptography 往往没有现成的安装包，现场编译要装 Rust、非常慢，直接用系统的
  if [[ "$ARCH" == armv7* || "$ARCH" == armhf ]]; then
    PKGS+=(python3-cryptography)
    SYS_CRYPTO=1
  fi
  DEBIAN_FRONTEND=noninteractive apt-get install -y "${PKGS[@]}"
  systemctl enable --now cron >/dev/null 2>&1 || true
elif command -v dnf >/dev/null 2>&1; then
  dnf install -y python3 python3-pip cronie ca-certificates
  systemctl enable --now crond >/dev/null 2>&1 || true
elif command -v yum >/dev/null 2>&1; then
  yum install -y python3 python3-pip cronie ca-certificates
  systemctl enable --now crond >/dev/null 2>&1 || true
else
  echo "    未识别的系统包管理器，请手动安装 Python 3.8+ 和 venv 后重试"
fi
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)' || {
  echo "需要 Python 3.8 或更高版本，当前是 $(python3 --version)"; exit 1; }

echo "==> [3/7] 内存与交换分区"
SWAP_MB="$(awk '/SwapTotal/ {print int($2/1024)}' /proc/meminfo 2>/dev/null || echo 0)"
if (( MEM_MB > 0 && MEM_MB < 1000 && SWAP_MB < 256 )); then
  if [[ ! -f /swapfile ]]; then
    echo "    内存较小且没有交换分区，创建 1GB 交换文件 /swapfile（安装依赖时更稳）"
    (fallocate -l 1G /swapfile 2>/dev/null || dd if=/dev/zero of=/swapfile bs=1M count=1024 status=none)
    chmod 600 /swapfile
    mkswap /swapfile >/dev/null && swapon /swapfile
    grep -q '^/swapfile ' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
  fi
else
  echo "    内存 ${MEM_MB}MB，交换分区 ${SWAP_MB}MB，不需要处理"
fi

echo "==> [4/7] 创建虚拟环境并安装 Python 依赖（开发板上可能要几分钟）"
if [[ $SYS_CRYPTO == 1 ]]; then
  python3 -m venv --system-site-packages "$APP_DIR/.venv"
else
  python3 -m venv "$APP_DIR/.venv"
fi
PIP="$APP_DIR/.venv/bin/pip"
pip_install() {
  if [[ $USE_MIRROR == 1 ]]; then
    "$PIP" install --prefer-binary -i "$MIRROR" "$@"
  else
    "$PIP" install --prefer-binary "$@" || {
      echo "    官方源安装失败，换清华镜像重试……"
      "$PIP" install --prefer-binary -i "$MIRROR" "$@"
    }
  fi
}
pip_install --upgrade pip -q
pip_install -r "$APP_DIR/requirements.txt"

echo "==> [5/7] 准备配置文件和数据目录"
mkdir -p "$APP_DIR/data/ilink" "$APP_DIR/data/backup"
for mood in 开心 抱抱 夸夸 加油 早安 晚安 撒娇 喝水 吃饭; do mkdir -p "$APP_DIR/stickers/$mood"; done
chmod 700 "$APP_DIR/data" "$APP_DIR/data/ilink" "$APP_DIR/data/backup"
if [[ ! -f "$APP_DIR/.env" ]]; then
  cp "$APP_DIR/.env.example" "$APP_DIR/.env"
  echo "    已生成 .env（下一步用 setup 向导填写密钥）"
else
  echo "    .env 已存在，保留原内容"
fi
chmod 600 "$APP_DIR/.env"
if [[ ! -f "$APP_DIR/persona.yaml" ]]; then
  cp "$APP_DIR/deploy/persona.example.yaml" "$APP_DIR/persona.yaml"
  echo "    已生成 persona.yaml（助手的名字、性格，可按喜好修改）"
fi

echo "==> [6/7] 安装开机自启服务 $SERVICE"
sed "s#__APP_DIR__#$APP_DIR#g" "$APP_DIR/deploy/$SERVICE.service" > "/etc/systemd/system/$SERVICE.service"
systemctl daemon-reload
systemctl enable "$SERVICE" >/dev/null 2>&1
echo "    已设为开机自启（暂未启动，填好配置并绑定微信后再启动）"

echo "==> [7/7] 每天凌晨 4 点自动备份数据库"
chmod +x "$APP_DIR/deploy/backup.sh"
if command -v crontab >/dev/null 2>&1; then
  ( crontab -l 2>/dev/null | grep -v "$APP_DIR/deploy/backup.sh" || true
    echo "0 4 * * * bash $APP_DIR/deploy/backup.sh >/dev/null 2>&1" ) | crontab -
  echo "    已加入 crontab"
else
  echo "    没有 crontab 命令，跳过自动备份（可安装 cron 后重新运行本脚本）"
fi

cat <<EOF

✅ 安装完成。下一步只要运行一条命令，跟着提示走：

    cd $APP_DIR && .venv/bin/python -m bot setup

它会：逐项填写密钥 → 自动检查 → 扫码绑定微信 → 启动服务。
详细说明见 docs/after-deploy.md
EOF
