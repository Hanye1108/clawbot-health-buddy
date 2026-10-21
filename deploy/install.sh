#!/usr/bin/env bash
# 一键安装 clawbot-health-buddy
# 用法（在项目目录里）：sudo bash deploy/install.sh            # 默认 pip 源
#                      sudo bash deploy/install.sh --mirror   # 国内服务器用清华 pip 镜像，更快
# 可以重复运行：不会覆盖已填写的 .env 和已有数据。
set -euo pipefail

APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
SERVICE=clawbot-health-buddy
PIP_ARGS=()
if [[ "${1:-}" == "--mirror" ]]; then
  PIP_ARGS=(-i https://pypi.tuna.tsinghua.edu.cn/simple)
fi

if [[ $EUID -ne 0 ]]; then
  echo "请用 root 运行：sudo bash deploy/install.sh"
  exit 1
fi

echo "==> [1/5] 安装系统依赖（Python 3 和 venv）"
if command -v apt-get >/dev/null 2>&1; then
  apt-get update -y
  DEBIAN_FRONTEND=noninteractive apt-get install -y python3 python3-venv python3-pip
elif command -v dnf >/dev/null 2>&1; then
  dnf install -y python3 python3-pip
elif command -v yum >/dev/null 2>&1; then
  yum install -y python3 python3-pip
else
  echo "未识别的系统包管理器，请手动安装 Python 3.8+ 和 venv 后重试"
fi
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)' || {
  echo "需要 Python 3.8 或更高版本，当前是 $(python3 --version)"; exit 1; }

echo "==> [2/5] 创建虚拟环境并安装 Python 依赖"
python3 -m venv "$APP_DIR/.venv"
"$APP_DIR/.venv/bin/pip" install --upgrade pip ${PIP_ARGS[@]+"${PIP_ARGS[@]}"} -q
"$APP_DIR/.venv/bin/pip" install -r "$APP_DIR/requirements.txt" ${PIP_ARGS[@]+"${PIP_ARGS[@]}"}

echo "==> [3/5] 准备配置文件和数据目录"
mkdir -p "$APP_DIR/data/ilink" "$APP_DIR/data/backup"
chmod 700 "$APP_DIR/data" "$APP_DIR/data/ilink" "$APP_DIR/data/backup"
if [[ ! -f "$APP_DIR/.env" ]]; then
  cp "$APP_DIR/.env.example" "$APP_DIR/.env"
  echo "    已生成 $APP_DIR/.env（需要填写密钥）"
else
  echo "    .env 已存在，保留原内容"
fi
chmod 600 "$APP_DIR/.env"
if [[ ! -f "$APP_DIR/persona.yaml" ]]; then
  cp "$APP_DIR/deploy/persona.example.yaml" "$APP_DIR/persona.yaml"
  echo "    已生成 $APP_DIR/persona.yaml（助手的名字、性格、问候语，可按喜好修改）"
fi

echo "==> [4/5] 安装开机自启服务 $SERVICE"
sed "s#__APP_DIR__#$APP_DIR#g" "$APP_DIR/deploy/$SERVICE.service" > "/etc/systemd/system/$SERVICE.service"
systemctl daemon-reload
systemctl enable "$SERVICE" >/dev/null 2>&1
echo "    已设为开机自启（暂未启动，填好配置并绑定微信后再启动）"

echo "==> [5/5] 每天凌晨 4 点自动备份数据库"
chmod +x "$APP_DIR/deploy/backup.sh"
if command -v crontab >/dev/null 2>&1; then
  ( crontab -l 2>/dev/null | grep -v "$APP_DIR/deploy/backup.sh" || true
    echo "0 4 * * * bash $APP_DIR/deploy/backup.sh >/dev/null 2>&1" ) | crontab -
  echo "    已加入 crontab"
else
  echo "    没有 crontab 命令，跳过自动备份（可安装 cron 后重新运行本脚本）"
fi

cat <<EOF

✅ 安装完成。接下来：
  1. 填写配置：     nano $APP_DIR/.env
  2. 检查配置：     cd $APP_DIR && .venv/bin/python -m bot check
  3. 绑定微信：     cd $APP_DIR && .venv/bin/python -m bot login me
  4. 启动服务：     systemctl start $SERVICE
  5. 查看日志：     journalctl -u $SERVICE -f
EOF
