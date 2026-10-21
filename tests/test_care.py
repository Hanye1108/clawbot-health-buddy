import datetime
import json
import unittest
from unittest import mock

from helpers import DBTestCase, FakeChannel

from bot import config, core, records, reminders
from bot.db import kv_get, now, run

U = "user-c"


class CareTest(DBTestCase):
    def setUp(self):
        super().setUp()
        self.ch = FakeChannel()
        self.bot = core.Bot(self.ch)
        records.ensure_user(U)

    def test_slots_deterministic_and_spaced(self):
        d = datetime.date(2026, 10, 22)
        reminders.set_setting(U, "care", times="10:00-20:00", count=3)
        a = reminders.care_slots(U, d)
        self.assertEqual(a, reminders.care_slots(U, d))
        self.assertEqual(len(a), 3)
        mins = [int(t[:2]) * 60 + int(t[3:]) for t in a]
        self.assertTrue(all(600 <= m <= 1200 for m in mins))
        self.assertTrue(all(b - a_ >= reminders.CARE_GAP_MINUTES for a_, b in zip(mins, mins[1:])))
        busy = [630, 750, 930, 1140]      # 喝水、午餐、喝水、晚餐
        self.assertTrue(all(abs(m - b) >= reminders.CARE_AVOID_MINUTES for m in mins for b in busy))
        self.assertNotEqual(reminders.care_slots(U, d), reminders.care_slots(U, d + datetime.timedelta(days=1)))

    def test_settings_and_validation(self):
        self.assertIn("随机关心：开，每天 10:00-20:00 之间随机 1 次", reminders.describe(U))
        reminders.set_setting(U, "care", count=2)
        self.assertIn("10:00-20:00 之间随机 2 次", reminders.describe(U))
        reminders.set_setting(U, "care", times="14:00-18:00")
        self.assertIn("14:00-18:00 之间随机 2 次", reminders.describe(U))
        with self.assertRaises(ValueError):
            reminders.set_setting(U, "care", times="14:00-14:30")
        with self.assertRaises(ValueError):
            reminders.set_setting(U, "care", count=5)
        with self.assertRaises(ValueError):
            reminders.set_setting(U, "care", times="09:00")
        from bot.tools import run_tool
        self.assertIn("已更新", run_tool(U, "set_reminder", {"kind": "care", "enabled": False}))
        self.assertIn("随机关心：关", reminders.describe(U))

    def test_due_includes_care(self):
        d = now().date()
        slot = reminders.care_slots(U, d)[0]
        at = datetime.datetime.combine(d, datetime.time(int(slot[:2]), int(slot[3:]), 30), tzinfo=config.TZ)
        self.assertIn("care", [k for k, _ in reminders.due(U, at)])
        nxt = reminders.next_push_time(U, at - datetime.timedelta(minutes=2))
        self.assertLessEqual(nxt, at)

    def test_care_message(self):
        with mock.patch("bot.llm.write", return_value="下午好呀，忙完记得歇一会儿～") as write:
            core.care(self.bot, U)
        ctx = json.loads(write.call_args[0][1])
        self.assertIn("今天的记录", ctx)
        self.assertEqual(self.ch.sent[-1][1], "下午好呀，忙完记得歇一会儿～")
        self.assertIn("下午好呀", kv_get(f"care_history:{U}"))
        # 她刚聊过就不打扰
        records.add_chat(U, "user", "在吗")
        with mock.patch("bot.llm.write") as write:
            core.care(self.bot, U)
            write.assert_not_called()
        # 历史里有上次发的话，提示不要重复
        old = (now() - datetime.timedelta(hours=3)).isoformat()
        run("UPDATE chat SET ts=? WHERE user_id=?", (old, U))
        with mock.patch("bot.llm.write", return_value="嗨") as write:
            core.care(self.bot, U)
        self.assertIn("下午好呀", json.dumps(json.loads(write.call_args[0][1])["你之前主动发过的话（不要重复）"],
                                          ensure_ascii=False))


if __name__ == "__main__":
    unittest.main()
