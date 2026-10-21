"""命令行入口。

  python -m bot            启动机器人（默认）
  python -m bot check      检查配置和外部服务是否可用
  python -m bot login me   扫码绑定一个微信 ClawBot（me 是自己起的名字）
  python -m bot accounts   列出已绑定的 ClawBot 账号
"""
import argparse
import logging
import sys

from . import __version__, config


def cmd_run():
    from .channels.ilink import IlinkChannel
    from .core import Bot, start_scheduler
    from .db import init_db

    missing = config.missing_settings()
    if missing:
        raise SystemExit(f"缺少配置：{', '.join(missing)}。请编辑 .env 后重试（可先运行 python -m bot check）")
    init_db()
    bot = Bot()
    if config.CHANNEL == "mp":
        from .channels.mp import MpChannel
        bot.channel = MpChannel(bot)
    elif config.CHANNEL == "ilink":
        bot.channel = IlinkChannel(bot)
        from .web import serve_in_background
        serve_in_background()
    else:
        raise SystemExit(f"CHANNEL 只能是 ilink 或 mp，现在是 {config.CHANNEL}")
    start_scheduler(bot)
    logging.getLogger(__name__).info("clawbot-health-buddy %s 已启动，通道：%s", __version__, config.CHANNEL)
    bot.channel.run()


def cmd_check():
    import requests
    ok = True

    def show(good, name, hint=""):
        nonlocal ok
        ok = ok and good
        print(("✅ " if good else "❌ ") + name + ("" if good else f"\n   → {hint}"))

    print(f"项目版本 {__version__}，通道 {config.CHANNEL}，数据目录 {config.DATA_DIR}\n")
    for name in config.missing_settings():
        show(False, f"配置项 {name}", "在 .env 里填写")

    try:
        from .db import init_db, kv_get, kv_set
        init_db()
        kv_set("check", "1")
        show(kv_get("check") == "1", f"数据库可写（{config.DB_PATH}）")
    except Exception as e:  # noqa: BLE001
        show(False, "数据库可写", str(e))

    if config.DEEPSEEK_KEY:
        try:
            from .llm import client
            r = client().chat.completions.create(
                model=config.MODEL, messages=[{"role": "user", "content": "回复OK"}], max_tokens=5, timeout=30)
            show(bool(r.choices), f"DeepSeek 对话模型（{config.MODEL}）")
        except Exception as e:  # noqa: BLE001
            show(False, f"DeepSeek 对话模型（{config.MODEL}）",
                 f"{e}\n   检查 DEEPSEEK_KEY 是否正确、账户是否有余额、模型名是否仍可用")

        try:
            from .llm import get_balance
            bal = get_balance()
            show(bal["available"], f"DeepSeek 余额：{bal['total']} {bal['currency']}", "余额不足，去 platform.deepseek.com 充值")
        except Exception as e:  # noqa: BLE001
            show(False, "DeepSeek 余额查询", str(e))

    if config.QWEATHER_KEY and config.QWEATHER_HOST:
        try:
            from .weather import lookup_city
            loc = lookup_city("北京")
            show(bool(loc), "和风天气城市搜索", "检查 QWEATHER_KEY 和 QWEATHER_HOST（要用控制台「设置」页里自己的 API Host）")
        except requests.exceptions.ConnectionError:
            show(False, "和风天气", f"连不上 {config.QWEATHER_HOST}，Host 是否抄错？（文档里的 abcxyz 是示例，不能用）")
        except Exception as e:  # noqa: BLE001
            show(False, "和风天气", str(e))

    if config.CHANNEL == "ilink":
        from .channels.ilink import load_accounts
        accs = load_accounts()
        show(bool(accs), f"已绑定 ClawBot 账号：{', '.join(a['_name'] for a in accs) or '无'}",
             "运行 python -m bot login me 扫码绑定")
        if accs:
            names = [a["_name"] for a in accs]
            show(config.ADMIN_ACCOUNT in names, f"告警接收账号：{config.ADMIN_ACCOUNT}",
                 f"没有名为 {config.ADMIN_ACCOUNT} 的账号，出问题时只会写日志。把 .env 里的 ADMIN_ACCOUNT 改成 "
                 f"{'/'.join(names)} 之一")
        try:
            requests.get(config.ILINK_BASE_URL, timeout=10)
            show(True, "能访问微信 iLink 服务器")
        except Exception as e:  # noqa: BLE001
            show(False, "能访问微信 iLink 服务器", str(e))

    if config.WEB_ENABLED:
        print(("ℹ️  趋势图网页对外地址：" + config.PUBLIC_URL) if config.PUBLIC_URL else
              "ℹ️  没有配置 PUBLIC_URL，「看趋势图」功能不可用（可选，见 DEPLOY.md）")

    print("\n全部通过 🎉" if ok else "\n还有问题需要处理")
    return 0 if ok else 1


def cmd_accounts():
    from .channels.ilink import load_accounts
    accs = load_accounts()
    if not accs:
        print("还没有绑定任何 ClawBot 账号。运行：python -m bot login me")
    for a in accs:
        print(f"{a['_name']}: bot={a.get('ilink_bot_id', '?')}")
    return 0


def main(argv=None):
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    p = argparse.ArgumentParser(prog="python -m bot", description="clawbot-health-buddy")
    sub = p.add_subparsers(dest="cmd")
    sub.add_parser("run", help="启动机器人（默认）")
    sub.add_parser("check", help="检查配置和外部服务")
    lp = sub.add_parser("login", help="扫码绑定微信 ClawBot")
    lp.add_argument("name", nargs="?", default="me", help="给这个账号起的名字，如 me、her")
    sub.add_parser("accounts", help="列出已绑定的账号")
    a = p.parse_args(argv)

    if a.cmd == "check":
        return cmd_check()
    if a.cmd == "login":
        from .channels.ilink import login
        return login(a.name)
    if a.cmd == "accounts":
        return cmd_accounts()
    cmd_run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
