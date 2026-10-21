"""DeepSeek 调用：带工具的对话、一次性写作、看图识别食物。"""
import base64
import json
import logging
import time

from . import config
from .db import now
from . import prompts as P

log = logging.getLogger(__name__)
_client = None
MAX_TOOL_ROUNDS = 6
# 配置的模型名 → API 实际返回的模型名（deepseek-chat 这类是别名，官方会指向不同的具体版本）
actual_models = {}


def opts():
    """额外请求参数：思考模式开关（见 .env 的 DEEPSEEK_THINKING）。"""
    if config.DEEPSEEK_THINKING in ("enabled", "disabled"):
        return {"extra_body": {"thinking": {"type": config.DEEPSEEK_THINKING}}}
    return {}


def _note(resp, requested):
    real = getattr(resp, "model", None)
    if real and actual_models.get(requested) != real:
        actual_models[requested] = real
        log.info("模型 %s 实际由 %s 响应", requested, real)


def client():
    global _client
    if _client is None:
        from openai import OpenAI  # 延迟导入，方便在没有装 openai 的环境里跑单元测试
        _client = OpenAI(api_key=config.DEEPSEEK_KEY, base_url=config.DEEPSEEK_BASE_URL)
    return _client


def get_balance():
    """查询 DeepSeek 账户余额（官方接口 GET /user/balance）。"""
    import requests
    r = requests.get(config.DEEPSEEK_BASE_URL.rstrip("/") + "/user/balance",
                     headers={"Authorization": f"Bearer {config.DEEPSEEK_KEY}"}, timeout=10)
    r.raise_for_status()
    data = r.json()
    infos = data.get("balance_infos") or [{}]
    info = next((i for i in infos if i.get("currency") == "CNY"), infos[0])
    return {"available": bool(data.get("is_available")),
            "total": info.get("total_balance", "?"), "currency": info.get("currency", "")}


def list_models():
    """这个 Key 能用的模型列表。"""
    return sorted(m.id for m in client().models.list().data)


def describe_models():
    lines = []
    for name in dict.fromkeys([config.MODEL, config.VISION_MODEL]):
        real = actual_models.get(name)
        lines.append(f"{name} → 实际响应：{real}" if real and real != name else
                     f"{name}" + ("（还没调用过，实际版本未知）" if not real else ""))
    return lines


def now_text():
    return now().strftime("%Y年%m月%d日 %H:%M")


def chat(messages, tools, run_tool, fallback="我查得有点久，请再说一次吧。"):
    """多轮工具调用，直到模型给出最终文字回复。"""
    messages = list(messages)
    start = time.monotonic()
    timing = []
    try:
        for _ in range(MAX_TOOL_ROUNDS):
            t = time.monotonic()
            resp = client().chat.completions.create(
                model=config.MODEL, messages=messages, tools=tools, timeout=60, **opts())
            timing.append(f"模型{time.monotonic() - t:.1f}s")
            _note(resp, config.MODEL)
            msg = resp.choices[0].message
            if not msg.tool_calls:
                return msg.content or fallback
            messages.append(msg)
            for call in msg.tool_calls:
                try:
                    args = json.loads(call.function.arguments or "{}")
                except ValueError:
                    args = {}
                t = time.monotonic()
                result = run_tool(call.function.name, args)
                timing.append(f"{call.function.name}{time.monotonic() - t:.1f}s")
                messages.append({"role": "tool", "tool_call_id": call.id, "content": result})
        return fallback
    finally:
        log.info("回复耗时 %.1fs：%s", time.monotonic() - start, " → ".join(timing))


def write(instruction, data):
    """一次性生成一段文字（不带工具），用于定时推送。"""
    resp = client().chat.completions.create(
        model=config.MODEL,
        messages=[{"role": "system", "content": f"{P.PERSONA}\n现在是北京时间{now_text()}。"},
                  {"role": "user", "content": f"{instruction}\n\n数据：{data}"}],
        timeout=60, **opts())
    return (resp.choices[0].message.content or "").strip()


def guess_mime(data):
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:4] == b"GIF8":
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return "image/jpeg"


def analyze_photo(data, mime=None):
    if len(data) > 10 * 1024 * 1024:
        return {"错误": "图片太大"}
    mime = mime if (mime or "").startswith("image/") else guess_mime(data)
    b64 = base64.b64encode(data).decode()
    t = time.monotonic()
    resp = client().chat.completions.create(
        model=config.VISION_MODEL,
        messages=[{"role": "user", "content": [
            {"type": "text", "text": P.VISION_PROMPT},
            {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
        ]}],
        timeout=90, **opts())
    _note(resp, config.VISION_MODEL)
    log.info("看图耗时 %.1fs（图片 %dKB）", time.monotonic() - t, len(data) // 1024)
    raw = (resp.choices[0].message.content or "").strip()
    start, end = raw.find("{"), raw.rfind("}")
    try:
        return json.loads(raw[start:end + 1])
    except ValueError:
        return {"原始描述": raw[:500]}
