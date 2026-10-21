import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("ENV_FILE", "/nonexistent/.env")   # 测试不读取真实配置

from bot import config, db  # noqa: E402


class DBTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._old = (config.DB_PATH, config.DATA_DIR, config.ILINK_ACCOUNTS_DIR)
        self._old_stickers = config.STICKERS_DIR
        config.STICKERS_DIR = Path(self.tmp.name) / "stickers"
        config.DATA_DIR = Path(self.tmp.name)
        config.DB_PATH = config.DATA_DIR / "bot.db"
        config.ILINK_ACCOUNTS_DIR = config.DATA_DIR / "ilink"
        db.init_db()

    def tearDown(self):
        config.DB_PATH, config.DATA_DIR, config.ILINK_ACCOUNTS_DIR = self._old
        config.STICKERS_DIR = self._old_stickers
        from bot import stickers
        stickers._library = {}
        self.tmp.cleanup()


class FakeChannel:
    def __init__(self, ok=True, fallback_ok=False):
        self.sent, self.fallbacks = [], []
        self.ok, self.fallback_ok = ok, fallback_ok

    def send_text(self, uid, text):
        if self.ok:
            self.sent.append((uid, text))
        return self.ok

    def send_fallback(self, uid, text):
        self.fallbacks.append((uid, text))
        return self.fallback_ok

    deadline = None
    admin = None

    def push_deadline(self, uid):
        return self.deadline

    def admin_uid(self):
        return self.admin

    def send_image(self, uid, data):
        self.images = getattr(self, "images", []) + [(uid, data)]
        return self.ok

    def status_lines(self):
        return ["通道正常"]
