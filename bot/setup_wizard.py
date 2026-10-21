"""配置向导：python -m bot setup

一步步带着填好密钥（输入时不显示）→ 自动检查 → 扫码绑定微信 → 启动服务。
可以重复运行：已经填过的项直接回车保留。
"""
import getpass
import os
import re
import shutil
import subprocess
import sys

from . import config

SERVICE = "clawbot-health-buddy"

# (配置名, 说明, 是否隐藏输入, 是否必填)
ITEMS = [
    ("DEEPSEEK_KEY", "DeepSeek API Key（platform.deepseek.com → API keys，sk- 开头）", True, True),
    ("QWEATHER_KEY", "和风天气 API Key（console.qweather.com → 项目管理 → 凭据，选 API KEY）", True, True),
    ("QWEATHER_HOST", "和风天气 API Host（控制台「设置」页里，形如 xxxx.re.qweatherapi.com）", False, True),
    ("PUBLIC_URL", "趋势图网页的对外地址（可选，不需要就直接回车；见 docs/after-deploy.md）", False, False),
]
# 旧的 .env 里没有这些项时，用推荐值补上
DEFAULTS = {"DEEPSEEK_MODEL": "deepseek-flash", "VISION_MODEL": "deepseek-flash",
            "DEEPSEEK_THINKING": "disabled", "CHANNEL": "ilink"}


def env_path():
    return config.ROOT / ".env"


def read_env(path):
    values = {}
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            m = re.match(r"\s*(?:export\s+)?([A-Z_][A-Z0-9_]*)\s*=\s*(.*)$", line)
            if m:
                values[m.group(1)] = m.group(2).strip().strip("'\"")
    return values


def write_env(path, updates):
    """更新已有的行，没有的追加到末尾；保留注释和其他配置。"""
    if not path.is_file():
        example = config.ROOT / ".env.example"
        if example.is_file():
            shutil.copy(example, path)
        else:
            path.write_text("", encoding="utf-8")
    lines = path.read_text(encoding="utf-8").splitlines()
    done = set()
    for i, line in enumerate(lines):
        m = re.match(r"\s*(?:export\s+)?([A-Z_][A-Z0-9_]*)\s*=", line)
        if m and m.group(1) in updates and m.group(1) not in done:
            lines[i] = f"{m.group(1)}={updates[m.group(1)]}"
            done.add(m.group(1))
    for k, v in updates.items():
        if k not in done:
            lines.append(f"{k}={v}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.chmod(path, 0o600)


def mask(v):
    return v[:4] + "****" + v[-4:] if len(v) > 10 else "****"


def clean(name, value):
    """校验并整理输入，返回 (值, 错误)。"""
    value = value.strip().strip("'\"")
    if name == "DEEPSEEK_KEY" and value and not value.startswith("sk-"):
        return value, "DeepSeek 的 Key 一般以 sk- 开头，确认没复制错？（仍会保存）"
    if name == "QWEATHER_HOST" and value:
        if "abcxyz" in value:
            return None, "这是官方文档里的示例地址，不能用。请到控制台「设置」页复制你自己的 API Host"
        if not value.startswith(("http://", "https://")):
            value = "https://" + value
        value = value.rstrip("/")
    if name == "PUBLIC_URL" and value:
        if not value.startswith(("http://", "https://")):
            value = "http://" + value
        value = value.rstrip("/")
    if re.search(r"\s", value):
        return None, "中间不能有空格"
    return value, None


def ask(prompt, hidden=False):
    return (getpass.getpass(prompt) if hidden else input(prompt)).strip()


def yes(prompt, default=True):
    ans = input(prompt + (" [Y/n] " if default else " [y/N] ")).strip().lower()
    return default if not ans else ans in ("y", "yes", "是", "好")


def run_self(*args):
    return subprocess.call([sys.executable, "-m", "bot", *args], cwd=str(config.ROOT))


def main():
    if not sys.stdin.isatty():
        print("setup 需要在终端里交互运行。也可以直接编辑 .env，见 docs/after-deploy.md")
        return 1
    path = env_path()
    current = read_env(path)
    print("\n=== clawbot-health-buddy 配置向导 ===")
    print(f"配置文件：{path}\n已经填过的项，直接回车保留原值。密钥输入时屏幕上不会显示，粘贴后回车即可。\n")

    updates = {}
    for name, desc, hidden, required in ITEMS:
        old = current.get(name, "")
        while True:
            shown = f"（当前：{mask(old) if hidden else old}）" if old else ""
            print(f"· {desc}{shown}")
            value = ask("  请输入：", hidden)
            if not value:
                if old or not required:
                    break
                print("  这一项必填。")
                continue
            value, err = clean(name, value)
            if value is None:
                print(f"  ❌ {err}")
                continue
            if err:
                print(f"  ⚠️  {err}")
            updates[name] = value
            break
    for k, v in DEFAULTS.items():
        if not current.get(k):
            updates[k] = v
    if updates:
        write_env(path, updates)
        print(f"\n✅ 已保存 {len(updates)} 项到 .env（权限 600，只有 root 能读）")
    else:
        print("\n没有修改。")

    print("\n=== 检查配置 ===")
    run_self("check")

    from .channels.ilink import load_accounts
    if (current.get("CHANNEL") or updates.get("CHANNEL") or "ilink") == "ilink":
        accounts = [a["_name"] for a in load_accounts()]
        print("\n=== 绑定微信 ===")
        if accounts:
            print(f"已绑定：{', '.join(accounts)}")
        if not accounts and yes("现在扫码绑定你自己的微信吗？（谁扫码，ClawBot 就出现在谁的微信里）"):
            run_self("login", "me")
        elif accounts and yes("要再给另一个人绑定吗？（比如家人、朋友，让对方用自己的微信扫码）", default=False):
            name = ask("  给这个账号起个英文名字（比如 her、mom）：") or "friend"
            run_self("login", re.sub(r"[^A-Za-z0-9_-]", "", name) or "friend")

    if shutil.which("systemctl") and os.geteuid() == 0:
        print("\n=== 启动服务 ===")
        if yes("现在（重新）启动机器人吗？"):
            subprocess.call(["systemctl", "restart", SERVICE])
            subprocess.call(["systemctl", "--no-pager", "--lines=5", "status", SERVICE])
            print("\n🎉 好了！在微信里找到 ClawBot，发一句「你好」，它会做自我介绍。")
            print("   测试清单、修改人设、日常维护见 docs/after-deploy.md")
    else:
        print("\n用 root 运行 systemctl restart clawbot-health-buddy 启动服务。")
    return 0
