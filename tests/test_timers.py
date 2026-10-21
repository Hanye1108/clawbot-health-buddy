import datetime
import unittest
from unittest import mock

from helpers import DBTestCase, FakeChannel

from bot import core, timers
from bot.db import now, run

U = "user-t"


class FakeScheduler:
    def __init__(self):
        self.jobs = {}

    def add_job(self, func, trigger, run_date, args, id, **kw):
        self.jobs[id] = (run_date, args)

    def remove_job(self, id):
        self.jobs.pop(id)


class TimerTest(DBTestCase):
    def setUp(self):
        super().setUp()
        self.ch = FakeChannel()
        self.bot = core.Bot(self.ch)
        self.sched = FakeScheduler()
        timers.attach(self.sched, self.bot)
        self.addCleanup(timers.attach, None, None)

    def texts(self):
        return [t for _, t in self.ch.sent]

    def test_delay_and_fire_once(self):
        r = timers.create(U, {"text": "喝水", "delay_seconds": 20})
        self.assertIn("已设置", r)
        (job, (due, (tid,))), = self.sched.jobs.items()
        self.assertAlmostEqual((due - now()).total_seconds(), 20, delta=2)
        timers.fire(tid)
        timers.fire(tid)                          # 不重复发
        self.assertEqual(self.texts(), ["⏰ 喝水"])
        self.assertEqual(timers.list_timers(U), "没有待发的临时提醒")

    def test_at_time_rolls_to_tomorrow(self):
        past = (now() - datetime.timedelta(minutes=5)).strftime("%H:%M")
        r = timers.create(U, {"text": "开会", "at": past})
        self.assertIn("明天", r)
        future = (now() + datetime.timedelta(days=2)).strftime("%Y-%m-%d 09:00")
        self.assertIn("后天 09:00", timers.create(U, {"text": "体检", "at": future}))

    def test_validation(self):
        self.assertIn("最短", timers.create(U, {"text": "x", "delay_seconds": 3}))
        self.assertIn("最多只能提前", timers.create(U, {"text": "x", "delay_seconds": 86400 * 40}))
        self.assertIn("缺少提醒内容", timers.create(U, {"delay_seconds": 60}))
        self.assertIn("格式", timers.create(U, {"text": "x", "at": "下午三点"}))
        self.assertIn("已经过去", timers.create(U, {"text": "x", "at": "2020-01-01 08:00"}))
        for _ in range(timers.MAX_PER_USER):
            timers.create(U, {"text": "x", "delay_seconds": 60})
        self.assertIn("最多同时", timers.create(U, {"text": "x", "delay_seconds": 60}))

    def test_daily_repeat_reschedules(self):
        timers.create(U, {"text": "吃维生素", "delay_seconds": 60, "repeat": "daily"})
        (_, (due, (tid,))), = self.sched.jobs.items()
        timers.fire(tid)
        self.assertEqual(self.texts(), ["⏰ 吃维生素"])
        new_due = self.sched.jobs[f"timer-{tid}"][0]
        self.assertAlmostEqual((new_due - due).total_seconds(), 86400, delta=1)
        self.assertIn("每天", timers.list_timers(U))

    def test_cancel(self):
        timers.create(U, {"text": "a", "delay_seconds": 60})
        timers.create(U, {"text": "b", "delay_seconds": 120})
        ids = [r["id"] for r in run("SELECT id FROM timers", fetch=True)]
        self.assertIn(str(ids[0]), timers.cancel(U, {"timer_ids": [ids[0]]}))
        self.assertIn("没有找到", timers.cancel("someone-else", {"timer_ids": [ids[1]]}))
        self.assertIn(str(ids[1]), timers.cancel(U, {"all": True}))
        self.assertEqual(self.sched.jobs, {})
        timers.fire(ids[0])
        self.assertEqual(self.texts(), [])

    def test_overdue_fired_by_tick_and_reloaded_on_start(self):
        old = (now() - datetime.timedelta(minutes=3)).isoformat()
        run("INSERT INTO timers (user_id, due_at, text, repeat, sent, created_at) VALUES (?,?,?,?,0,?)",
            (U, old, "补发的", "none", old))
        with mock.patch("bot.reminders.due", return_value=[]):
            core.tick(self.bot)
        self.assertEqual(self.texts(), ["⏰ 补发的"])
        timers.create(U, {"text": "未来的", "delay_seconds": 600})
        fresh = FakeScheduler()
        timers.attach(fresh, self.bot)
        self.assertEqual(len(fresh.jobs), 1)

    def test_warns_when_past_push_window(self):
        self.ch.deadline = now() + datetime.timedelta(hours=1)
        self.assertIn("补发", timers.create(U, {"text": "x", "delay_seconds": 7200}))
        self.assertNotIn("补发", timers.create(U, {"text": "y", "delay_seconds": 60}))

    def test_tool_dispatch(self):
        from bot.tools import run_tool
        self.assertIn("已设置", run_tool(U, "set_timer", {"text": "喝水", "delay_seconds": 30}))
        self.assertIn("喝水", run_tool(U, "list_timers", {}))
        self.assertIn("已取消", run_tool(U, "cancel_timer", {"all": True}))


if __name__ == "__main__":
    unittest.main()
