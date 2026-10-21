"""读取「人设」配置：bot/persona_default.yaml 是默认值，项目根目录的 persona.yaml 覆盖它。"""
from pathlib import Path

from . import config

DEFAULT_PATH = Path(__file__).with_name("persona_default.yaml")
USER_PATH = config.ROOT / "persona.yaml"
TEXT_KEYS = ("name", "personality", "style", "ask_name", "feature_guide", "whats_new", "quiet_notice")
REMINDER_KEYS = ("breakfast", "lunch", "dinner", "water", "weigh")


class PersonaError(ValueError):
    pass


def _read(path):
    import yaml
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as e:
        mark = getattr(e, "problem_mark", None)
        where = f"第 {mark.line + 1} 行" if mark else ""
        raise PersonaError(f"{path.name} {where}格式有误：{getattr(e, 'problem', e)}") from e
    if not isinstance(data, dict):
        raise PersonaError(f"{path.name} 的内容应该是「名字: 值」这样的格式")
    return data


def user_file():
    return USER_PATH if USER_PATH.is_file() else None


def load():
    data = _read(DEFAULT_PATH)
    if user_file():
        user = _read(USER_PATH)
        unknown = set(user) - set(data)
        if unknown:
            raise PersonaError(f"persona.yaml 里有不认识的项：{'、'.join(sorted(unknown))}（拼写错了吗？）")
        for k, v in user.items():
            if k == "reminders" and isinstance(v, dict):
                data["reminders"] = {**data["reminders"], **v}
            elif v is not None:
                data[k] = v
    return _validate(data)


def _validate(p):
    for k in TEXT_KEYS:
        if not isinstance(p.get(k), str) or not p[k].strip():
            raise PersonaError(f"「{k}」不能为空，应该是一段文字")
    if not isinstance(p.get("intro"), list) or not p["intro"] or not all(isinstance(x, str) and x.strip() for x in p["intro"]):
        raise PersonaError("「intro」应该是一个或多个文字段落（每段以「- 」开头）")
    try:
        p["reply_max_chars"] = int(p.get("reply_max_chars") or 150)
    except (TypeError, ValueError):
        raise PersonaError("「reply_max_chars」应该是数字")
    words = p.get("forbidden_words") or []
    if not isinstance(words, list):
        raise PersonaError("「forbidden_words」应该是列表，比如 [胖, 肥]")
    p["forbidden_words"] = [str(w) for w in words]
    for k in REMINDER_KEYS:
        items = p.get("reminders", {}).get(k)
        if not isinstance(items, list) or not items or not all(isinstance(x, str) and x.strip() for x in items):
            raise PersonaError(f"「reminders → {k}」至少要有一条文字")

    name = p["name"].strip()

    def fill(text):
        return text.replace("{name}", name).strip()
    for k in TEXT_KEYS:
        p[k] = fill(p[k])
    p["intro"] = [fill(x) for x in p["intro"]]
    p["reminders"] = {k: [fill(x) for x in p["reminders"][k]] for k in REMINDER_KEYS}
    return p


def persona_prompt(p):
    """给模型的身份与性格说明。"""
    lines = [
        f"你的名字是「{p['name']}」，是一个 AI 健康生活助手，底层是 DeepSeek 公司的大模型（当前使用的模型：{config.MODEL}，"
        "这是 DeepSeek 官方 API 的模型名，具体指向哪个版本由 DeepSeek 决定），"
        "由用户身边的人部署在自己的服务器上、通过微信和用户聊天。",
        f"被问到你是谁、是什么模型、谁做的时，如实回答：你叫{p['name']}，基于 DeepSeek 大模型；"
        "不要说自己是其他公司的模型，也不要编造版本号以外的细节。",
        "你的工作是帮用户养成喝水、规律吃饭、适量运动等好习惯，也可以聊日常。",
        f"性格：{p['personality']}",
        f"说话方式：{p['style']}",
        f"每次回复尽量不超过{p['reply_max_chars']}字。",
    ]
    if p["forbidden_words"]:
        lines.append(f"不要使用这些词：{'、'.join(p['forbidden_words'])}。")
    return "\n".join(lines)
