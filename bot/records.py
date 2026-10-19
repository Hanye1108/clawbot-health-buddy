"""用户资料、热量计算、饮食/运动/体重记录与统计。

所有数字都由这里的代码计算，大模型只负责把结果讲成人话。
"""
import datetime
import json

from .db import USER_TABLES, now, now_iso, run, today_str

# ---------- 常量 ----------
FIELDS = {"nickname", "sex", "age", "height_cm", "weight_kg",
          "goal_weight_kg", "activity", "city", "province"}
LIMITS = {"age": (10, 100), "height_cm": (120, 220),
          "weight_kg": (30, 250), "goal_weight_kg": (30, 250)}
ACTIVITY = {"sedentary": 1.2, "light": 1.375, "moderate": 1.55, "active": 1.725}
MEALS = {"breakfast": "早餐", "lunch": "午餐", "dinner": "晚餐", "snack": "加餐"}

# 运动强度（MET），消耗 = MET × 体重kg × 小时
MET = {"walking": 3.5, "brisk_walking": 4.3, "running": 9.8, "cycling": 7.0,
       "swimming": 6.0, "yoga": 2.5, "pilates": 3.0, "strength": 5.0, "dance": 6.0,
       "jump_rope": 11.0, "hiit": 8.0, "ball_games": 7.0, "hiking": 6.0,
       "stairs": 8.0, "other": 4.0}
EX_NAMES = {"walking": "散步", "brisk_walking": "快走", "running": "跑步", "cycling": "骑车",
            "swimming": "游泳", "yoga": "瑜伽", "pilates": "普拉提", "strength": "力量训练",
            "dance": "跳舞", "jump_rope": "跳绳", "hiit": "HIIT", "ball_games": "球类运动",
            "hiking": "爬山徒步", "stairs": "爬楼梯", "other": "其他运动"}

DEFICIT_KCAL = 400          # 每日温和热量缺口
FLOOR_KCAL = {"female": 1200, "male": 1500}   # 每日摄入安全下限
EXERCISE_ADD_BACK = 0.5     # 运动消耗只按一半加回当天额度（估算普遍偏高）
FAST_LOSS_WEEK_KG = 1.0     # 一周减重超过此值提示放慢
FAST_LOSS_4WEEK_KG = 4.0


def dumps(obj):
    return json.dumps(obj, ensure_ascii=False)


def parse_day(value):
    day = value or today_str()
    try:
        d = datetime.date.fromisoformat(day)
    except ValueError:
        return None, "date 格式应为 YYYY-MM-DD"
    today = now().date()
    if d > today or (today - d).days > 14:
        return None, "只能记录最近14天内、不晚于今天的日期"
    return day, None


# ---------- 用户与聊天记录 ----------
def get_profile(uid):
    rows = run("SELECT * FROM profiles WHERE user_id=?", (uid,), fetch=True)
    return dict(rows[0]) if rows else {}


def ensure_user(uid):
    run("INSERT OR IGNORE INTO profiles (user_id, reminders, weigh_reminder, updated_at) "
        "VALUES (?, 1, 1, ?)", (uid, now_iso()))


def clear_user_data(uid):
    for table in USER_TABLES:
        if table == "ilink_sessions":
            continue  # 保留会话令牌，否则连"已删除"都发不出去
        run(f"DELETE FROM {table} WHERE user_id=?", (uid,))
    run("INSERT INTO profiles (user_id, reminders, weigh_reminder, updated_at) "
        "VALUES (?, 0, 1, ?)", (uid, now_iso()))


def set_reminders_flag(uid, enabled, kind="all"):
    ensure_user(uid)
    col = "weigh_reminder" if kind == "weigh" else "reminders"
    run(f"UPDATE profiles SET {col}=? WHERE user_id=?", (1 if enabled else 0, uid))


def add_chat(uid, role, content):
    run("INSERT INTO chat (user_id, role, content, ts) VALUES (?,?,?,?)",
        (uid, role, content, now_iso()))
    run("DELETE FROM chat WHERE user_id=? AND id NOT IN "
        "(SELECT id FROM chat WHERE user_id=? ORDER BY id DESC LIMIT 200)", (uid, uid))


def load_history(uid, n=20):
    rows = run("SELECT role, content FROM chat WHERE user_id=? ORDER BY id DESC LIMIT ?",
               (uid, n), fetch=True)
    msgs = [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]
    while msgs and msgs[0]["role"] != "user":
        msgs.pop(0)
    return msgs


def save_profile(uid, args):
    clean = {}
    for k, v in args.items():
        if k not in FIELDS or v in (None, ""):
            continue
        if k in LIMITS:
            try:
                v = float(v)
            except (TypeError, ValueError):
                return f"{k} 不是有效数字，请向用户确认"
            lo, hi = LIMITS[k]
            if not lo <= v <= hi:
                return f"{k}={v} 超出合理范围({lo}-{hi})，请向用户确认"
            if k == "age":
                v = int(v)
        if k == "sex" and v not in ("female", "male"):
            return "sex 只能是 female 或 male"
        if k == "activity" and v not in ACTIVITY:
            return "activity 取值不对"
        clean[k] = v
    if not clean:
        return "没有可保存的内容"
    cols = ", ".join(clean)
    ph = ", ".join("?" for _ in clean)
    upd = ", ".join(f"{k}=excluded.{k}" for k in clean)
    run(f"INSERT INTO profiles (user_id, {cols}, updated_at) VALUES (?, {ph}, ?) "
        f"ON CONFLICT(user_id) DO UPDATE SET {upd}, updated_at=excluded.updated_at",
        (uid, *clean.values(), now_iso()))
    if "weight_kg" in clean:
        record_weight(uid, clean["weight_kg"], today_str())
    return "已保存：" + dumps(clean)


# ---------- 热量目标 ----------
def target_info(p):
    missing = [k for k in ("sex", "age", "height_cm", "weight_kg", "activity") if not p.get(k)]
    if missing:
        return {"缺少资料": missing}

    w, h, age = p["weight_kg"], p["height_cm"], p["age"]
    male = p["sex"] == "male"
    bmr = 10 * w + 6.25 * h - 5 * age + (5 if male else -161)   # Mifflin-St Jeor
    tdee = bmr * ACTIVITY[p["activity"]]
    bmi = w / (h / 100) ** 2
    floor_kcal = FLOOR_KCAL["male" if male else "female"]

    res = {"BMI": round(bmi, 1), "基础代谢大卡": round(bmr),
           "每日总消耗大卡": round(tdee), "安全下限大卡": floor_kcal, "备注": []}

    if age < 18:
        target = round(tdee)
        res["备注"].append("未满18岁不设减重热量缺口，建议在家长陪同下咨询医生或营养师")
    elif bmi < 18.5:
        target = round(tdee)
        res["备注"].append("BMI偏低，不建议减重，建议咨询医生或营养师")
    else:
        target = max(round(tdee - DEFICIT_KCAL), floor_kcal)
        if target > tdee:
            target = round(tdee)
            res["备注"].append("按安全下限，目前不适合再减少摄入，以维持为主")

    gap = tdee - target
    res["每日建议摄入大卡"] = target
    if gap > 0:
        res["预计每周减重公斤"] = round(gap * 7 / 7700, 1)
    gw = p.get("goal_weight_kg")
    if gw and gw / (h / 100) ** 2 < 18.5:
        res["备注"].append("目标体重对应的BMI低于18.5，偏低，建议重新考虑目标，可咨询医生")
    res["说明"] = "以上是公式估算值，个体差异很大，不是医嘱"
    return res


def calc_target(uid):
    return dumps(target_info(get_profile(uid)))


# ---------- 体重 ----------
def record_weight(uid, kg, day):
    run("DELETE FROM weights WHERE user_id=? AND day=?", (uid, day))
    run("INSERT INTO weights (user_id, day, kg, ts) VALUES (?,?,?,?)", (uid, day, kg, now_iso()))
    latest = run("SELECT kg FROM weights WHERE user_id=? ORDER BY day DESC LIMIT 1",
                 (uid,), fetch=True)[0]
    run("UPDATE profiles SET weight_kg=?, updated_at=? WHERE user_id=?",
        (latest["kg"], now_iso(), uid))


def weight_trend(uid):
    rows = run("SELECT day, kg FROM weights WHERE user_id=? ORDER BY day DESC LIMIT 60",
               (uid,), fetch=True)
    if not rows:
        return {"体重记录": "暂无"}
    latest = rows[0]
    ld = datetime.date.fromisoformat(latest["day"])

    def change(days):
        lo = (ld - datetime.timedelta(days=days + 7)).isoformat()
        hi = (ld - datetime.timedelta(days=days)).isoformat()
        for r in rows:
            if lo <= r["day"] <= hi:
                return round(latest["kg"] - r["kg"], 1)
        return None

    res = {"最近一次": {"日期": latest["day"], "公斤": latest["kg"]},
           "最近几次": [{"日期": r["day"], "公斤": r["kg"]} for r in rows[:5]]}
    c7, c28 = change(7), change(28)
    if c7 is not None:
        res["约一周变化公斤"] = c7
    if c28 is not None:
        res["约四周变化公斤"] = c28
    if (c7 is not None and c7 < -FAST_LOSS_WEEK_KG) or (c28 is not None and c28 < -FAST_LOSS_4WEEK_KG):
        res["减重偏快提示"] = "减得偏快，要温和建议放慢、好好吃饭，不要表扬速度"
    res["说明"] = "体重每天会波动1公斤左右，看趋势更有意义"
    return res


def log_weight(uid, args):
    try:
        kg = float(args.get("kg"))
    except (TypeError, ValueError):
        return "kg 需要是数字"
    if not 30 <= kg <= 250:
        return f"体重 {kg} 公斤超出合理范围，请向用户确认（是不是把斤当成了公斤？）"
    day, err = parse_day(args.get("date"))
    if err:
        return err
    ensure_user(uid)
    record_weight(uid, round(kg, 1), day)
    res = {"状态": "已记录", "趋势": weight_trend(uid)}
    t = target_info(get_profile(uid))
    if "每日建议摄入大卡" in t:
        res["按新体重的每日建议摄入大卡"] = t["每日建议摄入大卡"]
        if t["备注"]:
            res["备注"] = t["备注"]
    return dumps(res)


# ---------- 饮食与运动 ----------
def day_summary(uid, day=None):
    day = day or today_str()
    rows = run("SELECT id, meal, description, kcal FROM meals WHERE user_id=? AND day=? ORDER BY id",
               (uid, day), fetch=True)
    ex = run("SELECT id, description, minutes, kcal FROM exercises "
             "WHERE user_id=? AND day=? ORDER BY id", (uid, day), fetch=True)
    total = sum(r["kcal"] for r in rows)
    ex_total = sum(r["kcal"] for r in ex)
    res = {"日期": day,
           "饮食": [{"编号": r["id"], "餐次": MEALS.get(r["meal"], r["meal"]),
                     "内容": r["description"], "大卡": r["kcal"]} for r in rows],
           "饮食合计大卡": total,
           "运动": [{"编号": r["id"], "内容": r["description"], "分钟": r["minutes"],
                     "消耗大卡": r["kcal"]} for r in ex],
           "运动消耗大卡": ex_total}
    target = target_info(get_profile(uid)).get("每日建议摄入大卡")
    if target:
        bonus = round(ex_total * EXERCISE_ADD_BACK)
        res["每日建议摄入大卡"] = target
        res["还剩大卡"] = target + bonus - total
        if bonus:
            res["额度说明"] = f"运动消耗按一半（{bonus}大卡）加回今天的额度，因为运动消耗的估算通常偏高"
    return res


def has_meal(uid, day, meal):
    return bool(run("SELECT 1 FROM meals WHERE user_id=? AND day=? AND meal=? LIMIT 1",
                    (uid, day, meal), fetch=True))


def log_meal(uid, args):
    meal = args.get("meal")
    desc = (args.get("description") or "").strip()
    if meal not in MEALS:
        return "meal 取值不对"
    if not desc:
        return "缺少内容描述"
    try:
        kcal = int(round(float(args.get("kcal"))))
    except (TypeError, ValueError):
        return "kcal 需要是数字"
    if not 0 <= kcal <= 3000:
        return "kcal 超出合理范围(0-3000)，请向用户确认"
    day, err = parse_day(args.get("date"))
    if err:
        return err
    run("INSERT INTO meals (user_id, day, meal, description, kcal, ts) VALUES (?,?,?,?,?,?)",
        (uid, day, meal, desc[:200], kcal, now_iso()))
    return dumps({"状态": "已记录", **day_summary(uid, day)})


def delete_meal(uid, args):
    try:
        mid = int(args.get("meal_id"))
    except (TypeError, ValueError):
        return "meal_id 需要是数字"
    if not run("SELECT 1 FROM meals WHERE id=? AND user_id=?", (mid, uid), fetch=True):
        return "没有找到这个编号的记录"
    run("DELETE FROM meals WHERE id=? AND user_id=?", (mid, uid))
    return "已删除"


def log_exercise(uid, args):
    kind = args.get("kind") if args.get("kind") in MET else "other"
    desc = (args.get("description") or EX_NAMES[kind]).strip()[:100]
    try:
        minutes = int(round(float(args.get("minutes") or 0)))
    except (TypeError, ValueError):
        return "minutes 需要是数字"
    if not 0 <= minutes <= 600:
        return "运动时长超出合理范围(0-600分钟)，请向用户确认"
    day, err = parse_day(args.get("date"))
    if err:
        return err

    note = ""
    if args.get("kcal") not in (None, ""):
        try:
            kcal = int(round(float(args.get("kcal"))))
        except (TypeError, ValueError):
            return "kcal 需要是数字"
        source = "用户提供"
    else:
        if minutes <= 0:
            return "需要运动时长（分钟），请问用户运动了多久"
        w = get_profile(uid).get("weight_kg")
        if not w:
            w, note = 60, "还不知道体重，按60公斤估算的"
        kcal = round(MET[kind] * w * minutes / 60)
        source = "公式估算"
    if not 0 <= kcal <= 3000:
        return "消耗超出合理范围(0-3000大卡)，请向用户确认"

    run("INSERT INTO exercises (user_id, day, kind, description, minutes, kcal, source, ts) "
        "VALUES (?,?,?,?,?,?,?,?)", (uid, day, kind, desc, minutes, kcal, source, now_iso()))
    res = {"状态": "已记录", "这次消耗大卡": kcal, "来源": source}
    if note:
        res["备注"] = note
    res.update(day_summary(uid, day))
    return dumps(res)


def delete_exercise(uid, args):
    try:
        eid = int(args.get("exercise_id"))
    except (TypeError, ValueError):
        return "exercise_id 需要是数字"
    if not run("SELECT 1 FROM exercises WHERE id=? AND user_id=?", (eid, uid), fetch=True):
        return "没有找到这个编号的运动记录"
    run("DELETE FROM exercises WHERE id=? AND user_id=?", (eid, uid))
    return "已删除"


def week_stats(uid):
    end = now().date()
    days = [(end - datetime.timedelta(days=i)).isoformat() for i in range(6, -1, -1)]
    rows = run("SELECT day, meal, kcal FROM meals WHERE user_id=? AND day>=? AND day<=?",
               (uid, days[0], days[-1]), fetch=True)
    by = {d: [] for d in days}
    for r in rows:
        by[r["day"]].append((r["meal"], r["kcal"]))

    detail, complete = [], []
    no_breakfast = 0
    for d in days:
        items = by[d]
        if not items:
            continue
        total = sum(k for _, k in items)
        kinds = {m for m, _ in items}
        full = {"breakfast", "lunch", "dinner"} <= kinds
        if "breakfast" not in kinds:
            no_breakfast += 1
        detail.append({"日期": d, "合计大卡": total, "记录餐数": len(items), "三餐齐全": full})
        if full:
            complete.append(total)

    ex = run("SELECT day, minutes, kcal FROM exercises WHERE user_id=? AND day>=? AND day<=?",
             (uid, days[0], days[-1]), fetch=True)

    res = {"统计区间": f"{days[0]} 至 {days[-1]}", "有记录的天数": len(detail),
           "三餐齐全的天数": len(complete), "没记早餐的天数": no_breakfast,
           "每日明细": detail,
           "运动天数": len({r["day"] for r in ex}),
           "运动总分钟": sum(r["minutes"] for r in ex),
           "运动估算消耗大卡": sum(r["kcal"] for r in ex),
           "体重": weight_trend(uid)}
    t = target_info(get_profile(uid))
    target, floor_kcal = t.get("每日建议摄入大卡"), t.get("安全下限大卡")
    if complete:
        avg = round(sum(complete) / len(complete))
        res["三餐齐全日的平均大卡"] = avg
        if target:
            res["每日建议摄入大卡"] = target
            res["平均与目标的差"] = avg - target
        if floor_kcal and avg < floor_kcal:
            res["偏低提示"] = "平均摄入低于安全下限，不要表扬，要温和提醒好好吃饭并建议咨询营养师"
    if len(complete) < 3:
        res["数据提示"] = "三餐齐全的天数不足3天，饮食方面只能看个大概，不要下结论"
    return res


# ---------- 待补发消息（主动推送失败时暂存） ----------
def add_pending(uid, text, keep_hours):
    run("DELETE FROM pending WHERE expires_at < ?", (now_iso(),))
    expires = (now() + datetime.timedelta(hours=keep_hours)).isoformat()
    run("INSERT INTO pending (user_id, text, ts, expires_at) VALUES (?,?,?,?)",
        (uid, text, now_iso(), expires))


def take_pending(uid):
    rows = run("SELECT id, text FROM pending WHERE user_id=? AND expires_at >= ? ORDER BY id",
               (uid, now_iso()), fetch=True)
    run("DELETE FROM pending WHERE user_id=?", (uid,))
    return [r["text"] for r in rows]
