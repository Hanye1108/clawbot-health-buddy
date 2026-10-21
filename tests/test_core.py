import datetime
import unittest
from unittest import mock

from helpers import DBTestCase, FakeChannel

from bot import core, records, reminders
from bot.db import kv_get, now
from bot.prompts import ADMIN_GUIDE, FEATURE_GUIDE, INTRO, INTRO_ASK_NAME, INTRO_VERSION, QUIET_NOTICE, WHATS_NEW

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

    # ---------- 自我介绍 ----------
    def test_greeting_gets_intro_and_name_question_only(self):
        self.bot.handle_text(U, "你好")
        self.assertEqual(self.texts(), INTRO + [INTRO_ASK_NAME])
        self.assertIn(FEATURE_GUIDE, self.texts())
        self.chat.assert_not_called()
        # 介绍写进了聊天历史，模型知道自己问过称呼
        self.bot.handle_text(U, "叫我小雨")
        self.assertEqual(self.texts()[-1], "好的～")
        msgs = self.chat.call_args[0][0]
        self.assertEqual(msgs[1], {"role": "user", "content": "你好"})
        self.assertIn({"role": "assistant", "content": INTRO_ASK_NAME}, msgs)

    def test_first_message_with_content_is_answered_after_intro(self):
        self.bot.handle_text(U, "午饭吃了一碗牛肉面")
        self.assertEqual(self.texts(), INTRO + ["好的～"])
        self.chat.assert_called_once()

    def test_admin_gets_admin_guide(self):
        self.ch.admin = U
        self.bot.handle_text(U, "你好")
        self.assertIn(ADMIN_GUIDE, self.texts())

    def test_existing_user_gets_whats_new_once(self):
        records.ensure_user(U)        # 老用户：有资料但没看过新版介绍
        self.bot.handle_text(U, "在吗吃了吗")
        self.bot.handle_text(U, "再来")
        self.assertEqual(self.texts().count(WHATS_NEW), 1)
        self.assertEqual(kv_get(f"intro:{U}"), INTRO_VERSION)

    def test_guide_command(self):
        records.ensure_user(U)
        self.bot.handle_text(U, "功能介绍")
        self.assertEqual(self.texts()[-1], FEATURE_GUIDE)

    # ---------- 对话 ----------
    def test_history_passed_to_model(self):
        self.bot.handle_text(U, "第一句")
        self.bot.handle_text(U, "第二句")
        messages = self.chat.call_args[0][0]
        self.assertEqual(messages[0]["role"], "system")
        self.assertIn("口味与忌口", messages[0]["content"])
        self.assertEqual(messages[-1], {"role": "user", "content": "第二句"})

    def test_commands_do_not_call_model(self):
        records.ensure_user(U)
        self.bot.handle_text(U, "关闭提醒")
        self.assertEqual(records.get_profile(U)["reminders"], 0)
        self.bot.handle_text(U, "关闭称重提醒")
        self.assertFalse(reminders.get_settings(U)["weigh"]["enabled"])
        self.bot.handle_text(U, "我的提醒设置")
        self.assertIn("称重提醒：关", self.texts()[-1])
        self.assertIn("总开关已关闭", self.texts()[-1])
        self.bot.handle_text(U, "清除数据")
        self.chat.assert_not_called()
        self.assertIn("删除", self.texts()[-1])
        self.assertTrue(reminders.get_settings(U)["weigh"]["enabled"])   # 设置也清了

    def test_push_failure_stores_pending_and_delivers_later(self):
        records.ensure_user(U)
        self.bot.handle_text(U, "嗨")   # 先过掉更新说明
        self.ch.sent.clear()
        self.ch.ok = False
        self.bot.push(U, "今天天气晴", nudge="早上好，回我一句", keep_hours=5)
        self.assertEqual(self.ch.fallbacks, [(U, "早上好，回我一句")])
        self.ch.ok = True
        self.bot.handle_text(U, "嗨")
        self.assertEqual(self.texts(), ["今天天气晴", "好的～"])

    # ---------- 推送 ----------
    def test_meal_skips_logged_meal_unless_forced(self):
        records.ensure_user(U)
        records.log_meal(U, {"meal": "lunch", "description": "面", "kcal": 500})
        core.meal(self.bot, U, "lunch")
        self.assertEqual(self.texts(), [])
        core.meal(self.bot, U, "lunch", force=True)
        core.meal(self.bot, U, "dinner")
        self.assertEqual(len(self.texts()), 2)

    def test_water_reminder_aware_of_intake(self):
        records.save_profile(U, {"water_goal_ml": 1000})
        core.water(self.bot, U)
        self.assertNotIn("已经喝了", self.texts()[-1])
        records.log_water(U, {"ml": 500})
        core.water(self.bot, U)
        self.assertIn("500ml", self.texts()[-1])
        records.log_water(U, {"ml": 500})
        n = len(self.texts())
        core.water(self.bot, U)          # 喝够了不提醒
        self.assertEqual(len(self.texts()), n)

    def test_tick_respects_settings_and_master_switch(self):
        records.ensure_user(U)
        at = now().replace(hour=12, minute=31, second=5, microsecond=0)
        with mock.patch("bot.reminders.now", return_value=at):
            core.tick(self.bot)
            core.tick(self.bot)           # 同一时段不重复发
        self.assertEqual(len(self.texts()), 1)
        reminders.set_setting(U, "lunch", times="13:00")
        records.set_reminders_flag(U, False)
        at2 = at.replace(hour=13, minute=0)
        with mock.patch("bot.reminders.now", return_value=at2):
            core.tick(self.bot)
        self.assertEqual(len(self.texts()), 1)
        records.set_reminders_flag(U, True)
        with mock.patch("bot.reminders.now", return_value=at2):
            core.tick(self.bot)
        self.assertEqual(len(self.texts()), 2)

    def test_quiet_notice_only_on_last_push_before_deadline(self):
        records.ensure_user(U)
        nxt = reminders.next_push_time(U, now())
        self.ch.deadline = nxt + datetime.timedelta(minutes=5)
        self.bot.push(U, "提醒A")
        self.assertEqual(self.texts()[-1], "提醒A")
        self.ch.deadline = nxt - datetime.timedelta(minutes=5)
        self.bot.push(U, "提醒B")
        self.bot.push(U, "提醒C")
        self.assertEqual(self.texts()[-2], "提醒B" + QUIET_NOTICE)
        self.assertEqual(self.texts()[-1], "提醒C")

    def test_weekly_without_data_does_not_call_model(self):
        records.ensure_user(U)
        with mock.patch("bot.llm.write") as write:
            core.weekly(self.bot, U)
            core.monthly(self.bot, U)
            core.night(self.bot, U)
            write.assert_not_called()
        self.assertEqual(self.texts(), [])
        core.weekly(self.bot, U, force=True)
        self.assertIn("还没有饮食或运动记录", self.texts()[-1])

    def test_night_and_monthly_with_data(self):
        records.ensure_user(U)
        records.log_meal(U, {"meal": "lunch", "description": "面", "kcal": 500})
        with mock.patch("bot.llm.write", return_value="小结") as write:
            core.night(self.bot, U)
            core.monthly(self.bot, U, force=True)
        self.assertEqual(write.call_count, 2)
        self.assertEqual(self.texts(), ["小结", "小结"])


if __name__ == "__main__":
    unittest.main()
