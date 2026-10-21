import unittest
from unittest import mock

from helpers import DBTestCase, FakeChannel

from bot import config, core, records
from bot.channels import ilink

ADMIN, HER = "admin-uid", "her-uid"


class AlertTest(DBTestCase):
    def setUp(self):
        super().setUp()
        self.ch = FakeChannel()
        self.ch.admin = ADMIN
        self.bot = core.Bot(self.ch)

    def to_admin(self):
        return [t for u, t in self.ch.sent if u == ADMIN]

    def test_alert_cooldown(self):
        self.assertTrue(self.bot.alert("x", "出事了"))
        self.assertFalse(self.bot.alert("x", "又出事了"))
        self.assertEqual(len(self.to_admin()), 1)
        self.assertIn("出事了", self.to_admin()[0])

    def test_alert_kept_until_admin_talks(self):
        self.ch.ok = False
        self.bot.alert("x", "出事了")
        self.assertEqual(len(records.take_pending(ADMIN)), 1)

    def test_no_admin_only_logs(self):
        self.ch.admin = None
        self.assertFalse(self.bot.alert("x", "出事了"))
        self.assertEqual(self.ch.sent, [])

    def test_llm_failures_alert_after_three(self):
        records.ensure_user(HER)
        with mock.patch("bot.llm.chat", side_effect=RuntimeError("Insufficient Balance")):
            for _ in range(4):
                self.bot.handle_text(HER, "你好")
        alerts = self.to_admin()
        self.assertEqual(len(alerts), 1)
        self.assertIn("Insufficient Balance", alerts[0])
        # 用户收到的是正常的道歉，看不到错误细节
        her = [t for u, t in self.ch.sent if u == HER]
        self.assertTrue(all("Insufficient" not in t for t in her))

    def test_status_admin_only(self):
        records.ensure_user(ADMIN)
        records.ensure_user(HER)
        with mock.patch("bot.llm.get_balance", return_value={"available": True, "total": "42.00", "currency": "CNY"}), \
             mock.patch("bot.llm.chat", return_value="普通回复") as chat:
            self.bot.handle_text(ADMIN, "#status")
            self.bot.handle_text(HER, "#status")
        self.assertIn("42.00", self.to_admin()[-1])
        self.assertIn("通道正常", self.to_admin()[-1])
        self.assertEqual(chat.call_count, 1)          # 她发的 #status 当普通聊天处理

    def test_balance_job(self):
        with mock.patch("bot.llm.get_balance", return_value={"available": True, "total": "2.10", "currency": "CNY"}):
            core.balance_job(self.bot)
        self.assertIn("2.10", self.to_admin()[0])
        with mock.patch("bot.llm.get_balance", return_value={"available": True, "total": "50", "currency": "CNY"}):
            from bot.db import kv_set
            kv_set("alert:balance", "")
            core.balance_job(self.bot)
        self.assertEqual(len(self.to_admin()), 1)

    def test_morning_weather_failure_alerts(self):
        records.save_profile(HER, {"city": "某地"})
        with mock.patch("bot.core.get_weather", return_value="天气接口出错：401"), \
             mock.patch("bot.llm.write") as write:
            core.morning_job(self.bot)
            write.assert_not_called()
        self.assertIn("401", self.to_admin()[0])


class IlinkAdminTest(DBTestCase):
    def setUp(self):
        super().setUp()
        config.ILINK_ACCOUNTS_DIR.mkdir(parents=True, exist_ok=True)
        (config.ILINK_ACCOUNTS_DIR / "me.json").write_text(
            '{"bot_token": "t1", "ilink_bot_id": "b1", "ilink_user_id": "admin@im.wechat"}')
        (config.ILINK_ACCOUNTS_DIR / "her.json").write_text(
            '{"bot_token": "t2", "ilink_bot_id": "b2", "ilink_user_id": "her@im.wechat"}')
        self.bot = mock.Mock()
        self.ch = ilink.IlinkChannel(self.bot)

    def test_admin_uid_from_account_name(self):
        self.assertEqual(self.ch.admin_uid(), "admin@im.wechat")

    def test_expired_alert_not_kept_for_admins_own_account(self):
        self.ch.accounts["b2"].report_expired()
        self.assertTrue(self.bot.alert.call_args.kwargs["keep"])
        self.ch.accounts["b1"].report_expired()
        self.assertFalse(self.bot.alert.call_args.kwargs["keep"])
        self.assertIn("login me", self.bot.alert.call_args.args[1])

    def test_status_lines(self):
        lines = self.ch.status_lines()
        self.assertEqual(len(lines), 2)
        self.assertTrue(all("还没收到过消息" in line for line in lines))


if __name__ == "__main__":
    unittest.main()
