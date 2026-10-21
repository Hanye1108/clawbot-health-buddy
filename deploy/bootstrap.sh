#!/usr/bin/env bash
# =====================================================================
# 从零开始的一条命令安装：下载代码 → 运行 install.sh
#
#   curl -fsSL https://raw.githubusercontent.com/Hanye1108/clawbot-health-buddy/main/deploy/bootstrap.sh | sudo bash -s -- --mirror
#
# 参数会原样传给 install.sh（国内建议加 --mirror）。
# 环境变量：
#   APP_DIR   安装目录，默认 /opt/clawbot-health-buddy
#   REPO_URL  代码仓库地址，默认 GitHub；国内可换成自己的 Gitee 镜像
# 已经安装过的话，会先更新代码再重新运行安装（不会覆盖 .env、persona.yaml 和数据）。
# =====================================================================
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/clawbot-health-buddy}"
REPO_URL="${REPO_URL:-https://github.com/Hanye1108/clawbot-health-buddy.git}"

if [[ $EUID -ne 0 ]]; then
  echo "请用 root 运行（命令前加 sudo）"
  exit 1
fi

if ! command -v git >/dev/null 2>&1; then
  echo "==> 安装 git"
  if command -v apt-get >/dev/null 2>&1; then apt-get update -y && apt-get install -y git
  elif command -v dnf >/dev/null 2>&1; then dnf install -y git
  elif command -v yum >/dev/null 2>&1; then yum install -y git
  else echo "请先手动安装 git"; exit 1; fi
fi

retry() {
  local n
  for n in 1 2 3 4 5; do
    if timeout 120 "$@"; then return 0; fi
    echo "    第 ${n} 次失败，5 秒后重试……"
    sleep 5
  done
  return 1
}

if [[ -d "$APP_DIR/.git" ]]; then
  echo "==> 已存在 $APP_DIR，更新代码"
  git -C "$APP_DIR" checkout -- . 2>/dev/null || true
  retry git -C "$APP_DIR" pull --ff-only || echo "    更新失败（网络问题），继续使用现有代码安装"
else
  echo "==> 下载代码到 $APP_DIR"
  if ! retry git clone --depth 1 "$REPO_URL" "$APP_DIR"; then
    cat <<EOF

❌ 下载代码失败（国内连 GitHub 不稳定很常见）。可以：
   1. 过一会儿重试这条命令；或
   2. 在电脑上打开 ${REPO_URL%.git}，点 Code → Download ZIP，
      上传到这台机器的 /opt 并解压，文件夹改名为 clawbot-health-buddy，然后运行：
      cd $APP_DIR && sudo bash deploy/install.sh --mirror
EOF
    exit 1
  fi
fi

exec bash "$APP_DIR/deploy/install.sh" "$@"
