"""DeepSeek 调用：带工具的对话、一次性写作、看图识别食物。"""
import base64
import json
import logging
import time

from . import config
from .db import now
from .prompts import PERSONA, VISION_PROMPT

log = logging.getLogger(__name__)
_client = None
MAX_TOOL_ROUNDS = 6


def client():
    global _client
    if _client is None:
        from openai import OpenAI  # 延迟导入，方便在没有装 openai 的环境里跑单元测试
        _client = OpenAI(api_key=config.DEEPSEEK_KEY, base_url=config.DEEPSEEK_BASE_URL)
    return _client


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
                model=config.MODEL, messages=messages, tools=tools, timeout=60)
            timing.append(f"模型{time.monotonic() - t:.1f}s")
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
        messages=[{"role": "system", "content": f"{PERSONA}\n现在是北京时间{now_text()}。"},
                  {"role": "user", "content": f"{instruction}\n\n数据：{data}"}],
        timeout=60)
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
            {"type": "text", "text": VISION_PROMPT},
            {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
        ]}],
        timeout=90)
    log.info("看图耗时 %.1fs（图片 %dKB）", time.monotonic() - t, len(data) // 1024)
    raw = (resp.choices[0].message.content or "").strip()
    start, end = raw.find("{"), raw.rfind("}")
    try:
        return json.loads(raw[start:end + 1])
    except ValueError:
        return {"原始描述": raw[:500]}
