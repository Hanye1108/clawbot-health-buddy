"""新手引导：第一次使用时，一项一项问出称呼、性别年龄、身高体重、运动量、城市。

进度由代码记录（还缺哪些、哪些被跳过），每轮把进度告诉模型，由模型用自然的话来问。
"""
import json

from . import records
from .db import kv_get, kv_set

# 按提问顺序；同一组的字段一起问
STEPS = [
    ("称呼", ["nickname"]),
    ("性别和年龄", ["sex", "age"]),
    ("身高和体重", ["height_cm", "weight_kg"]),
    ("平时运动多少", ["activity"]),
    ("所在城市", ["city"]),
]
STEP_NAMES = [name for name, _ in STEPS]


def _key(uid):
    return f"onboard:{uid}"


def state(uid):
    raw = kv_get(_key(uid))
    if not raw:
        return None
    try:
        return json.loads(raw)
    except ValueError:
        return None


def start(uid):
    kv_set(_key(uid), json.dumps({"status": "active", "skipped": []}, ensure_ascii=False))


def finish(uid):
    s = state(uid) or {"skipped": []}
    s["status"] = "done"
    kv_set(_key(uid), json.dumps(s, ensure_ascii=False))
    return "引导已结束"


def active(uid):
    s = state(uid)
    return bool(s and s.get("status") == "active")


def remaining(uid):
    """还没收集、也没被跳过的步骤。"""
    s = state(uid) or {"skipped": []}
    p = records.get_profile(uid)
    out = []
    for name, fields in STEPS:
        if name in s.get("skipped", []):
            continue
        missing = [f for f in fields if not p.get(f)]
        if missing:
            out.append((name, missing))
    return out


def skip(uid, step):
    if step not in STEP_NAMES:
        return f"step 只能是：{'、'.join(STEP_NAMES)}"
    s = state(uid) or {"status": "active", "skipped": []}
    if step not in s["skipped"]:
        s["skipped"].append(step)
    kv_set(_key(uid), json.dumps(s, ensure_ascii=False))
    left = remaining(uid)
    return f"已跳过「{step}」。" + (f"下一个：{left[0][0]}" if left else "资料问完了，请调用 finish_onboarding")


def prompt_for(uid):
    """注入系统提示的引导说明；不在引导中返回空字符串。"""
    if not active(uid):
        return ""
    left = remaining(uid)
    if not left:
        finish(uid)
        return ("\n\n【新手引导刚完成】资料已经收集完。如果性别、年龄、身高、体重、运动量都有，"
                "调用 calc_target，用两三句话告诉她每天大概吃多少合适（说明是估算）；然后告诉她可以开始了："
                "直接说吃了什么、拍照、运动、喝水都行，发「功能介绍」可以再看说明。")
    skipped = (state(uid) or {}).get("skipped", [])
    nxt, fields = left[0]
    lines = [
        "\n\n【新手引导进行中】这是她第一次使用，正在一项一项了解她的基本情况。",
        f"还没问的：{'、'.join(n for n, _ in left)}。" + (f"她选择跳过：{'、'.join(skipped)}。" if skipped else ""),
        f"这一轮要问：{nxt}（字段：{', '.join(fields)}）。",
        "规则：每次只问一项，语气轻松，一两句话说明为什么问（比如身高体重用来算每天吃多少合适、城市用来发早安天气）；"
        "她回答后马上用 save_profile 保存，再问下一项；问体重时特别说明不想说可以跳过；"
        "活动量用选项问：几乎不运动 / 每周运动1-3次 / 每周3-5次 / 几乎每天或体力劳动；"
        "她说「跳过」「不想说」或明显不想回答时，调用 skip_onboarding_step，不要追问；"
        "她说「先不填了」「以后再说」时调用 finish_onboarding，告诉她以后随时可以补充；"
        "如果她这条消息在说别的事（比如记饮食、问天气），先正常处理，最后再顺带问下一项。"
        "全部问完后调用 finish_onboarding。",
    ]
    return "\n".join(lines)
