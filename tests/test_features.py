import datetime
import json
import unittest
from unittest import mock

from helpers import DBTestCase

from bot import config, records, reminders, web
from bot.db import now, run

U = "user-f"


class ReminderSettingsTest(DBTestCase):
    def test_defaults(self):
        s = reminders.get_settings(U)
        self.assertEqual(s["water"]["times"], ["10:30", "15:30"])
        self.assertFalse(s["night"]["enabled"])
        self.assertEqual(s["weekly"]["days"], "sun")

    def test_set_and_validate(self):
        reminders.set_setting(U, "breakfast", times="9:00")
        reminders.set_setting(U, "water", times="10:00，14:00, 17:30", days="weekdays")
        s = reminders.get_settings(U)
        self.assertEqual(s["breakfast"]["times"], ["09:00"])
        self.assertEqual((s["water"]["times"], s["water"]["days"]), (["10:00", "14:00", "17:30"], "weekdays"))
        with self.assertRaises(ValueError):
            reminders.set_setting(U, "lunch", times="25:00")
        with self.assertRaises(ValueError):
            reminders.set_setting(U, "weekly", days="weekdays")
        res = json.loads(reminders.tool_set(U, {"kind": "night", "enabled": True}))
        self.assertIn("睡前小结：开", res["当前设置"])
        self.assertIn("只能设置 enabled", reminders.tool_set(U, {"kind": "all", "times": "08:00"}))

    def test_due_days_and_grace(self):
        sunday = datetime.date(2026, 10, 25)          # 周日
        at = datetime.datetime.combine(sunday, datetime.time(20, 32), tzinfo=config.TZ)
        kinds = [k for k, _ in reminders.due(U, at)]
        self.assertEqual(kinds, ["weekly"])
        self.assertEqual(reminders.due(U, at), [])    # 不重复
        late = at.replace(hour=20, minute=40)          # 超过5分钟就不补发了
        run("DELETE FROM reminder_log")
        self.assertEqual(reminders.due(U, late), [])
        reminders.set_setting(U, "breakfast", days="weekdays")
        self.assertEqual(reminders.due(U, at.replace(hour=8, minute=30)), [])

    def test_next_push_time(self):
        t = now().replace(hour=20, minute=0, second=0, microsecond=0)
        nxt = reminders.next_push_time(U, t)
        self.assertEqual(((nxt.date() - t.date()).days, nxt.hour, nxt.minute), (1, 7, 0))
        reminders.set_setting(U, "night", enabled=True)
        nxt = reminders.next_push_time(U, t)
        self.assertEqual((nxt.hour, nxt.minute), (21, 30))


class PrefsWaterTest(DBTestCase):
    def test_prefs(self):
        r = json.loads(records.update_prefs(U, {"add": {"不吃": ["香菜", "香菜"], "喜欢": ["辣"]}}))
        self.assertEqual(r["当前"]["不吃"], ["香菜"])
        r = json.loads(records.update_prefs(U, {"remove": ["香菜"]}))
        self.assertEqual(r["当前"]["不吃"], [])
        self.assertIn("类别只能是", records.update_prefs(U, {"add": {"别的": ["x"]}}))

    def test_water(self):
        records.save_profile(U, {"weight_kg": 60})
        self.assertEqual(records.water_goal(U), 1800)
        r = json.loads(records.log_water(U, {"ml": 250}))
        self.assertEqual((r["喝水合计毫升"], r["约几杯"]), (250, 1.0))
        self.assertIn("超出", records.log_water(U, {"ml": 5000}))
        wid = r["喝水"][0]["编号"]
        self.assertEqual(records.delete_water(U, {"water_id": wid}), "已删除")
        self.assertEqual(records.day_summary(U)["喝水合计毫升"], 0)


class MonthTest(DBTestCase):
    def test_month_stats(self):
        records.save_profile(U, {"sex": "female", "age": 28, "height_cm": 165, "weight_kg": 60,
                                 "activity": "sedentary"})
        for m in ("breakfast", "lunch", "dinner"):
            records.log_meal(U, {"meal": m, "description": "x", "kcal": 500})
        records.log_exercise(U, {"kind": "running", "minutes": 30})
        s = records.month_stats(U)
        self.assertEqual((s["三餐齐全的天数"], s["三餐齐全日的平均大卡"], s["运动天数"]), (1, 1500, 1))
        self.assertIn("数据提示", s)
        self.assertIn("错误", records.month_stats(U, "2999-01"))
        self.assertIn("错误", records.month_stats(U, "abc"))
        last = (now().date().replace(day=1) - datetime.timedelta(days=1)).strftime("%Y-%m")
        self.assertEqual(records.month_stats(U, last)["有饮食记录的天数"], 0)


class WebTest(DBTestCase):
    def setUp(self):
        super().setUp()
        self._web_old = (config.PUBLIC_URL, config.WEB_ENABLED)
        config.PUBLIC_URL, config.WEB_ENABLED = "http://example.test", True
        from flask import Flask
        self.client = web.register(Flask(__name__)).test_client()
        records.save_profile(U, {"nickname": "小雨<script>", "weight_kg": 60})
        records.record_weight(U, 61, (now().date() - datetime.timedelta(days=10)).isoformat())
        records.log_meal(U, {"meal": "lunch", "description": "牛肉面", "kcal": 650})
        records.log_water(U, {"ml": 500})

    def tearDown(self):
        config.PUBLIC_URL, config.WEB_ENABLED = self._web_old
        super().tearDown()

    def test_link_page_and_export(self):
        msg = web.link_for_tool(U)
        self.assertIn("http://example.test/r/", msg)
        token = msg.split("/r/")[1].split()[0]
        r = self.client.get(f"/r/{token}")
        self.assertEqual(r.status_code, 200)
        page = r.get_data(as_text=True)
        self.assertIn("<svg", page)
        self.assertNotIn("<script>", page)                  # 名字被转义
        self.assertEqual(r.headers["Cache-Control"], "no-store")
        csv_text = self.client.get(f"/r/{token}/export.csv").get_data(as_text=True)
        self.assertIn("牛肉面", csv_text)
        self.assertIn("喝水", csv_text)

    def test_bad_and_expired_tokens(self):
        self.assertEqual(self.client.get("/r/nope").status_code, 404)
        url, _ = web.create_link(U)
        token = url.rsplit("/", 1)[1]
        run("UPDATE report_links SET expires_at=?", ((now() - datetime.timedelta(minutes=1)).isoformat(),))
        self.assertEqual(self.client.get(f"/r/{token}").status_code, 404)

    def test_not_configured(self):
        config.PUBLIC_URL = ""
        self.assertIn("还没有开启", web.link_for_tool(U))

    def test_empty_user_page(self):
        url, _ = web.create_link("nobody")
        r = self.client.get("/r/" + url.rsplit("/", 1)[1])
        self.assertIn("还没有", r.get_data(as_text=True))


class ToolsTest(DBTestCase):
    def test_new_tools_dispatch(self):
        from bot.tools import TOOLS, run_tool
        names = {t["function"]["name"] for t in TOOLS}
        for n in ("set_reminder", "get_reminders", "update_preferences", "log_water", "delete_water",
                  "get_month_summary", "get_report_link"):
            self.assertIn(n, names)
        self.assertIn("已更新", run_tool(U, "set_reminder", {"kind": "water", "times": "11:00"}))
        self.assertIn("喝水提醒：开，每天 11:00", run_tool(U, "get_reminders", {}))
        self.assertIn("已记录", run_tool(U, "log_water", {"ml": 250}))
        with mock.patch.object(config, "PUBLIC_URL", ""):
            self.assertIn("还没有开启", run_tool(U, "get_report_link", {}))


if __name__ == "__main__":
    unittest.main()


class OnboardingTest(DBTestCase):
    def setUp(self):
        super().setUp()
        from helpers import FakeChannel
        from bot import core
        self.ch = FakeChannel()
        self.bot = core.Bot(self.ch)

    def test_flow(self):
        from bot import onboarding
        from bot.prompts import INTRO_ASK_NAME
        with mock.patch("bot.llm.chat", return_value="ok") as chat:
            self.bot.handle_text(U, "你好")
            self.assertEqual(self.ch.sent[-1][1], INTRO_ASK_NAME)
            self.assertTrue(onboarding.active(U))
            self.bot.handle_text(U, "叫我小雨")
            system = chat.call_args[0][0][0]["content"]
            self.assertIn("这一轮要问：称呼", system)
            records.save_profile(U, {"nickname": "小雨", "sex": "female", "age": 28})
            self.bot.handle_text(U, "x")
            self.assertIn("这一轮要问：身高和体重", chat.call_args[0][0][0]["content"])
            self.assertIn("已跳过", onboarding.skip(U, "身高和体重"))
            self.bot.handle_text(U, "x")
            self.assertIn("这一轮要问：平时运动多少", chat.call_args[0][0][0]["content"])
            records.save_profile(U, {"activity": "light", "city": "杭州"})
            self.bot.handle_text(U, "x")
            self.assertIn("新手引导刚完成", chat.call_args[0][0][0]["content"])
            self.assertFalse(onboarding.active(U))
            self.bot.handle_text(U, "x")
            self.assertNotIn("新手引导", chat.call_args[0][0][0]["content"])

    def test_restart_and_clear(self):
        from bot import onboarding
        records.save_profile(U, {"nickname": "a", "sex": "female", "age": 28, "height_cm": 160,
                                 "weight_kg": 50, "activity": "light", "city": "x"})
        self.bot.handle_text(U, "重新填写资料")
        self.assertIn("都填好啦", self.ch.sent[-1][1])
        records.clear_user_data(U)
        self.assertIsNone(onboarding.state(U))
        with mock.patch("bot.llm.chat", return_value="ok") as chat:
            self.bot.handle_text(U, "重新填写资料")
            self.assertIn("这一轮要问：称呼", chat.call_args[0][0][0]["content"])


class ThinkingOptsTest(unittest.TestCase):
    def test_opts(self):
        from bot import llm
        with mock.patch.object(config, "DEEPSEEK_THINKING", ""):
            self.assertEqual(llm.opts(), {})
        with mock.patch.object(config, "DEEPSEEK_THINKING", "disabled"):
            self.assertEqual(llm.opts(), {"extra_body": {"thinking": {"type": "disabled"}}})
