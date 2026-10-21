"""表情包：项目根目录的 stickers/ 文件夹，按心情分子文件夹，里面放图片。

    stickers/
      开心/  a.gif  b.png
      抱抱/  ...
      晚安/  ...

文件夹名就是心情，模型根据聊天氛围挑一个心情，程序从里面随机选一张发出去。
加了新图片或新文件夹后，管理员发 #reload 生效（或重启）。
"""
import logging
import random

from . import config
from .db import now, run

log = logging.getLogger(__name__)
EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
MAX_BYTES = 2 * 1024 * 1024
MIN_GAP_REPLIES = 3          # 两张表情包之间至少隔几条回复
DAILY_MAX = 12               # 每人每天最多几张

_library = {}


def load():
    """扫描 stickers 文件夹，返回 {心情: [文件路径...]}。"""
    global _library
    lib = {}
    folder = config.STICKERS_DIR
    if folder.is_dir():
        for sub in sorted(p for p in folder.iterdir() if p.is_dir() and not p.name.startswith(".")):
            files = [f for f in sorted(sub.iterdir())
                     if f.suffix.lower() in EXTS and f.is_file() and f.stat().st_size <= MAX_BYTES]
            if files:
                lib[sub.name] = files
    _library = lib
    log.info("表情包：%s", "、".join(f"{k}({len(v)})" for k, v in lib.items()) or "没有")
    return lib


def moods():
    return sorted(_library)


def tool_spec():
    """有表情包时才提供这个工具。"""
    if not _library:
        return None
    return {"type": "function", "function": {
        "name": "send_sticker",
        "description": "在这条回复后面配一张表情包。只在轻松、开心、撒娇、鼓励、道早安晚安这类氛围下偶尔使用，"
                       "每条回复最多一次；严肃话题、她难过或聊到健康风险时不要用。",
        "parameters": {"type": "object", "properties": {
            "mood": {"type": "string", "enum": moods(), "description": "表情包的心情"}},
            "required": ["mood"]}}}


def _recent_count(uid, since):
    rows = run("SELECT COUNT(*) AS n FROM chat WHERE user_id=? AND role='assistant' AND content LIKE '[表情包:%' "
               "AND ts >= ?", (uid, since), fetch=True)
    return rows[0]["n"]


def allowed(uid):
    """频率限制：最近几条回复里发过就不发；每天有上限。返回 (是否允许, 原因)。"""
    rows = run("SELECT content FROM chat WHERE user_id=? AND role='assistant' ORDER BY id DESC LIMIT ?",
               (uid, MIN_GAP_REPLIES), fetch=True)
    if any(r["content"].startswith("[表情包:") for r in rows):
        return False, "刚发过表情包，这次先不发，正常文字回复就好"
    start = now().replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    if _recent_count(uid, start) >= DAILY_MAX:
        return False, "今天表情包发得够多了，这次不发"
    return True, ""


def pick(mood):
    files = _library.get(mood)
    if not files:
        return None
    path = random.choice(files)
    try:
        return path.read_bytes()
    except OSError:
        log.exception("读取表情包失败：%s", path)
        return None
