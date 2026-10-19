import unittest
from unittest import mock

from helpers import DBTestCase, FakeChannel

from bot import core, records
from bot.prompts import WELCOME

U = "user-b"


class CoreTest(DBTestCase):
    def setUp(self):
        super().setUp()
        self.ch = FakeChannel()
        self.bot = core.Bot(self.ch)
        patcher = mock.patch("bot.llm.chat", return_value="好的～")
        self.chat = patcher.start()
        self.addCleanup(patcher.stop)

    def texts(self):
        return [t for _, t in self.ch.sent]

    def test_new_user_gets_welcome_then_reply(self):
        self.bot.handle_text(U, "你好")
        self.assertEqual(self.texts(), [WELCOME, "好的～"])
        self.bot.handle_text(U, "再来")
        self.assertEqual(self.texts()[-1], "好的～")
        self.assertEqual(self.texts().count(WELCOME), 1)

    def test_history_passed_to_model(self):
        self.bot.handle_text(U, "第一句")
        self.bot.handle_text(U, "第二句")
        messages = self.chat.call_args[0][0]
        self.assertEqual(messages[0]["role"], "system")
        self.assertEqual(messages[-1], {"role": "user", "content": "第二句"})

    def test_commands_do_not_call_model(self):
        records.ensure_user(U)
        self.bot.handle_text(U, "关闭提醒")
        self.assertEqual(records.get_profile(U)["reminders"], 0)
        self.bot.handle_text(U, "关闭称重提醒")
        self.assertEqual(records.get_profile(U)["weigh_reminder"], 0)
        self.bot.handle_text(U, "清除数据")
        self.chat.assert_not_called()
        self.assertIn("删除", self.texts()[-1])

    def test_push_failure_stores_pending_and_delivers_later(self):
        records.ensure_user(U)
        self.ch.ok = False
        self.bot.push(U, "今天天气晴", nudge="早上好，回我一句", keep_hours=5)
        self.assertEqual(self.ch.fallbacks, [(U, "早上好，回我一句")])
        self.ch.ok = True
        self.bot.handle_text(U, "嗨")
        self.assertEqual(self.texts(), ["今天天气晴", "好的～"])

    def test_meal_job_skips_logged_meal(self):
        records.ensure_user(U)
        records.log_meal(U, {"meal": "lunch", "description": "面", "kcal": 500})
        core.meal_job(self.bot, "lunch")
        self.assertEqual(self.texts(), [])
        core.meal_job(self.bot, "dinner")
        self.assertEqual(len(self.texts()), 1)

    def test_reminders_off_skips_jobs(self):
        records.ensure_user(U)
        records.set_reminders_flag(U, False)
        core.water_job(self.bot)
        self.assertEqual(self.texts(), [])

    def test_weekly_without_data_does_not_call_model(self):
        records.ensure_user(U)
        with mock.patch("bot.llm.write") as write:
            core.weekly_job(self.bot)
            write.assert_not_called()
            core.weekly_job(self.bot, only=U)
            write.assert_not_called()
        self.assertIn("还没有饮食或运动记录", self.texts()[-1])


if __name__ == "__main__":
    unittest.main()
