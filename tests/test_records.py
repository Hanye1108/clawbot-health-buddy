import datetime
import json
import unittest

from helpers import DBTestCase

from bot import records
from bot.db import now, run

U = "user-a"


def profile(**kw):
    base = {"sex": "female", "age": 28, "height_cm": 165, "weight_kg": 60, "activity": "sedentary"}
    base.update(kw)
    return base


class TargetTest(unittest.TestCase):
    def test_normal_adult_hits_floor(self):
        t = records.target_info(profile())
        # BMR = 600 + 1031.25 - 140 - 161 = 1330.25；TDEE ≈ 1596；减400后低于下限 → 取下限1200
        self.assertEqual(t["基础代谢大卡"], 1330)
        self.assertEqual(t["每日建议摄入大卡"], 1200)
        self.assertEqual(t["安全下限大卡"], 1200)

    def test_deficit_applied_when_room(self):
        t = records.target_info(profile(weight_kg=80, activity="moderate"))
        self.assertEqual(t["每日建议摄入大卡"], t["每日总消耗大卡"] - 400)
        self.assertIn("预计每周减重公斤", t)

    def test_minor_gets_maintenance(self):
        t = records.target_info(profile(age=16))
        self.assertEqual(t["每日建议摄入大卡"], t["每日总消耗大卡"])
        self.assertTrue(any("18岁" in n for n in t["备注"]))

    def test_low_bmi_gets_maintenance(self):
        t = records.target_info(profile(weight_kg=48))
        self.assertEqual(t["每日建议摄入大卡"], t["每日总消耗大卡"])
        self.assertTrue(any("BMI偏低" in n for n in t["备注"]))

    def test_low_goal_flagged(self):
        t = records.target_info(profile(goal_weight_kg=45))
        self.assertTrue(any("目标体重" in n for n in t["备注"]))

    def test_missing_fields(self):
        self.assertIn("缺少资料", records.target_info({"sex": "female"}))


class RecordsTest(DBTestCase):
    def test_profile_validation(self):
        self.assertIn("超出合理范围", records.save_profile(U, {"weight_kg": 500}))
        self.assertIn("已保存", records.save_profile(U, profile()))
        self.assertEqual(records.get_profile(U)["weight_kg"], 60)
        # 保存体重会同时写入体重记录
        self.assertEqual(len(run("SELECT * FROM weights WHERE user_id=?", (U,), fetch=True)), 1)

    def test_meal_log_and_delete(self):
        records.save_profile(U, profile())
        r = json.loads(records.log_meal(U, {"meal": "lunch", "description": "牛肉面", "kcal": 650}))
        self.assertEqual(r["饮食合计大卡"], 650)
        self.assertEqual(r["还剩大卡"], 1200 - 650)
        mid = r["饮食"][0]["编号"]
        self.assertEqual(records.delete_meal(U, {"meal_id": mid}), "已删除")
        self.assertEqual(records.day_summary(U)["饮食合计大卡"], 0)
        self.assertIn("没有找到", records.delete_meal("someone-else", {"meal_id": mid}))

    def test_meal_rejects_bad_input(self):
        self.assertIn("超出合理范围", records.log_meal(U, {"meal": "lunch", "description": "x", "kcal": 9999}))
        future = (now().date() + datetime.timedelta(days=1)).isoformat()
        self.assertIn("不晚于今天", records.log_meal(
            U, {"meal": "lunch", "description": "x", "kcal": 100, "date": future}))

    def test_exercise_estimate_and_half_add_back(self):
        records.save_profile(U, profile())
        r = json.loads(records.log_exercise(U, {"kind": "brisk_walking", "minutes": 40}))
        self.assertEqual(r["这次消耗大卡"], round(4.3 * 60 * 40 / 60))   # 172
        self.assertEqual(r["还剩大卡"], 1200 + round(172 * 0.5))
        r2 = json.loads(records.log_exercise(U, {"kind": "dance", "kcal": 250}))
        self.assertEqual(r2["来源"], "用户提供")
        self.assertIn("运动了多久", records.log_exercise(U, {"kind": "running"}))

    def test_fast_weight_loss_flag(self):
        records.save_profile(U, profile(weight_kg=56))
        week_ago = (now().date() - datetime.timedelta(days=7)).isoformat()
        records.record_weight(U, 56, week_ago)
        r = json.loads(records.log_weight(U, {"kg": 54}))
        self.assertEqual(r["趋势"]["约一周变化公斤"], -2.0)
        self.assertIn("减重偏快提示", r["趋势"])
        self.assertEqual(records.get_profile(U)["weight_kg"], 54)

    def test_weight_jin_mistake_rejected(self):
        self.assertIn("斤", records.log_weight(U, {"kg": 260}))

    def test_week_stats_flags(self):
        records.save_profile(U, profile())
        s = records.week_stats(U)
        self.assertEqual(s["有记录的天数"], 0)
        self.assertIn("数据提示", s)
        for meal in ("breakfast", "lunch", "dinner"):
            records.log_meal(U, {"meal": meal, "description": "很少", "kcal": 200})
        s = records.week_stats(U)
        self.assertEqual(s["三餐齐全的天数"], 1)
        self.assertIn("偏低提示", s)

    def test_clear_data_keeps_session_and_disables_reminders(self):
        records.save_profile(U, profile())
        records.add_chat(U, "user", "hi")
        run("INSERT INTO ilink_sessions VALUES ('bot', ?, 'ctx', 'now')", (U,))
        records.clear_user_data(U)
        p = records.get_profile(U)
        self.assertEqual(p["reminders"], 0)
        self.assertIsNone(p["weight_kg"])
        self.assertEqual(records.load_history(U), [])
        self.assertEqual(len(run("SELECT * FROM ilink_sessions", fetch=True)), 1)

    def test_pending_expiry(self):
        records.add_pending(U, "fresh", keep_hours=5)
        records.add_pending(U, "old", keep_hours=-1)
        self.assertEqual(records.take_pending(U), ["fresh"])
        self.assertEqual(records.take_pending(U), [])


if __name__ == "__main__":
    unittest.main()
