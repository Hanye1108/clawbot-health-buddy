"""配置：优先读取环境变量，其次读取项目根目录下的 .env 文件。"""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load_env_file():
    path = Path(os.environ.get("ENV_FILE") or ROOT / ".env")
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export "):]
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        os.environ.setdefault(key, value)


_load_env_file()

try:
    from zoneinfo import ZoneInfo
except ImportError:  # Python 3.8
    from backports.zoneinfo import ZoneInfo


def env(name, default=""):
    return os.environ.get(name, default).strip()


def _https(host):
    host = host.rstrip("/")
    if host and not host.startswith(("http://", "https://")):
        host = "https://" + host
    return host


# 通道：ilink = 微信 ClawBot（推荐）；mp = 公众号测试号
CHANNEL = env("CHANNEL", "ilink").lower()

# DeepSeek
DEEPSEEK_KEY = env("DEEPSEEK_KEY")
DEEPSEEK_BASE_URL = env("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
MODEL = env("DEEPSEEK_MODEL", "deepseek-chat")
VISION_MODEL = env("VISION_MODEL", "deepseek-flash")

# 和风天气（新版接口，需要控制台「设置」页里的专属 API Host）
QWEATHER_KEY = env("QWEATHER_KEY")
QWEATHER_HOST = _https(env("QWEATHER_HOST"))

# 时区与数据目录
TZ = ZoneInfo(env("BOT_TIMEZONE", "Asia/Shanghai"))
DATA_DIR = Path(env("DATA_DIR") or ROOT / "data")
DB_PATH = Path(env("DB_PATH") or DATA_DIR / "bot.db")

# 微信 ClawBot（iLink）
ILINK_BASE_URL = env("ILINK_BASE_URL", "https://ilinkai.weixin.qq.com").rstrip("/")
ILINK_CDN_URL = env("ILINK_CDN_URL", "https://novac2c.cdn.weixin.qq.com/c2c").rstrip("/")
ILINK_ACCOUNTS_DIR = DATA_DIR / "ilink"
ILINK_ONLY_OWNER = env("ILINK_ONLY_OWNER", "1") == "1"
# 主动推送窗口：实测她最后一次发消息后 12 小时还能主动推送，24 小时就不行了。
# 超过这个小时数就不再尝试主动发送（暂存起来，等她下次说话时补发）。
ILINK_PUSH_WINDOW_HOURS = float(env("ILINK_PUSH_WINDOW_HOURS", "23") or 23)

# 管理员告警：出问题时（登录过期、DeepSeek 连续失败、余额不足……）发消息通知谁
# ilink 通道填管理员自己那个 ClawBot 账号的名字（就是 python -m bot login 后面写的名字）
ADMIN_ACCOUNT = env("ADMIN_ACCOUNT", "me")
# mp 通道填管理员的公众号 openid（日志里能看到）
ADMIN_OPENID = env("ADMIN_OPENID")
# DeepSeek 余额低于多少元时提醒
ALERT_BALANCE = float(env("ALERT_BALANCE", "5") or 5)

# 公众号测试号（CHANNEL=mp 时才需要）
WX_TOKEN = env("WX_TOKEN")
WX_APPID = env("WX_APPID")
WX_APPSECRET = env("WX_APPSECRET")
WX_TEMPLATE_ID = env("WX_TEMPLATE_ID")
MP_PORT = int(env("MP_PORT", "5000") or 5000)

REQUIRED = {
    "common": ["DEEPSEEK_KEY", "QWEATHER_KEY", "QWEATHER_HOST"],
    "mp": ["WX_TOKEN", "WX_APPID", "WX_APPSECRET"],
}


def missing_settings():
    names = list(REQUIRED["common"])
    if CHANNEL == "mp":
        names += REQUIRED["mp"]
    return [n for n in names if not env(n)]
