import base64
import os
import unittest
from unittest import mock

from helpers import DBTestCase, FakeChannel

from bot import config, core, records, stickers
from bot.channels import ilink

U = "user-s"
PNG = b"\x89PNG\r\n\x1a\n" + b"x" * 100


def make_lib():
    for mood in ("开心", "晚安", "空的"):
        (config.STICKERS_DIR / mood).mkdir(parents=True, exist_ok=True)
    (config.STICKERS_DIR / "开心" / "a.png").write_bytes(PNG)
    (config.STICKERS_DIR / "晚安" / "b.gif").write_bytes(b"GIF89a" + b"y" * 50)
    (config.STICKERS_DIR / "开心" / "note.txt").write_text("忽略")
    stickers.load()


def fake_chat(calls):
    """模拟模型：按 calls 依次调用工具，然后回复文字。"""
    def chat(messages, tools, run_tool, **kw):
        chat.tools = [t["function"]["name"] for t in tools]
        chat.results = [run_tool(name, args) for name, args in calls]
        return "好呀～"
    return chat


class StickerTest(DBTestCase):
    def setUp(self):
        super().setUp()
        self.ch = FakeChannel()
        self.bot = core.Bot(self.ch)
        records.ensure_user(U)
        from bot.db import kv_set
        from bot.prompts import INTRO_VERSION
        kv_set(f"intro:{U}", INTRO_VERSION)

    def test_library_and_tool_only_when_present(self):
        self.assertIsNone(stickers.tool_spec())
        make_lib()
        self.assertEqual(stickers.moods(), ["开心", "晚安"])
        self.assertEqual(stickers.tool_spec()["function"]["parameters"]["properties"]["mood"]["enum"], ["开心", "晚安"])

    def test_sticker_sent_after_text_with_cooldown(self):
        make_lib()
        chat = fake_chat([("send_sticker", {"mood": "开心"}), ("send_sticker", {"mood": "晚安"})])
        with mock.patch("bot.llm.chat", side_effect=chat):
            self.bot.handle_text(U, "我今天跑了5公里")
        self.assertIn("send_sticker", chat.tools)
        self.assertIn("不要再配", chat.results[1])
        self.assertEqual(self.ch.sent[-1][1], "好呀～")
        self.assertEqual(self.ch.images, [(U, PNG)])
        self.assertEqual(records.load_history(U)[-1]["content"], "[表情包:开心]")
        chat2 = fake_chat([("send_sticker", {"mood": "开心"})])
        with mock.patch("bot.llm.chat", side_effect=chat2):
            self.bot.handle_text(U, "嘿嘿")
        self.assertIn("刚发过", chat2.results[0])
        self.assertEqual(len(self.ch.images), 1)

    def test_unknown_mood_and_no_library(self):
        chat = fake_chat([])
        with mock.patch("bot.llm.chat", side_effect=chat):
            self.bot.handle_text(U, "hi")
        self.assertNotIn("send_sticker", chat.tools)
        make_lib()
        chat = fake_chat([("send_sticker", {"mood": "生气"})])
        with mock.patch("bot.llm.chat", side_effect=chat):
            self.bot.handle_text(U, "hi")
        self.assertIn("没有这种表情包", chat.results[0])
        self.assertFalse(getattr(self.ch, "images", []))


class IlinkImageTest(DBTestCase):
    def test_upload_flow(self):
        acc = ilink.Account({"_name": "me", "bot_token": "t", "ilink_bot_id": "b"}, mock.Mock())
        posts = []

        def post(path, body, timeout=15):
            posts.append((path, body))
            return {"upload_param": "UP=="} if path.endswith("getuploadurl") else {}
        cdn = mock.Mock(status_code=200, headers={"x-encrypted-param": "EQP"})
        data = os.urandom(1000)
        with mock.patch.object(acc, "post", side_effect=post), \
             mock.patch("bot.channels.ilink.requests.post", return_value=cdn) as rp:
            self.assertTrue(acc.send_image("her@im.wechat", data, "CTX"))
        up = posts[0][1]
        key = bytes.fromhex(up["aeskey"])
        uploaded = rp.call_args.kwargs["data"]
        self.assertEqual(ilink.aes_ecb_decrypt(uploaded, key), data)
        self.assertEqual(up["filesize"], len(uploaded))
        self.assertEqual(up["filesize"], (1000 // 16 + 1) * 16)
        self.assertEqual(rp.call_args.kwargs["params"]["encrypted_query_param"], "UP==")
        msg = posts[1][1]["msg"]
        item = msg["item_list"][0]
        self.assertEqual((msg["context_token"], item["type"]), ("CTX", 2))
        self.assertEqual(item["image_item"]["media"]["encrypt_query_param"], "EQP")
        self.assertEqual(base64.b64decode(item["image_item"]["media"]["aes_key"]).decode(), up["aeskey"])

    def test_cdn_failure(self):
        acc = ilink.Account({"_name": "me", "bot_token": "t", "ilink_bot_id": "b"}, mock.Mock())
        cdn = mock.Mock(status_code=500, headers={"x-error-message": "bad"})
        with mock.patch.object(acc, "post", return_value={"upload_param": "UP"}), \
             mock.patch("bot.channels.ilink.requests.post", return_value=cdn):
            with self.assertRaises(RuntimeError):
                acc.send_image("u", b"x", "CTX")


if __name__ == "__main__":
    unittest.main()
