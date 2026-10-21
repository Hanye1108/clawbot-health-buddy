import base64
import datetime
import os
import unittest
from unittest import mock

from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from helpers import DBTestCase

from bot import config
from bot.channels import chunks, ilink
from bot.db import now, now_iso, run


def encrypt(data, key):
    p = padding.PKCS7(128).padder()
    padded = p.update(data) + p.finalize()
    enc = Cipher(algorithms.AES(key), modes.ECB()).encryptor()
    return enc.update(padded) + enc.finalize()


class CryptoTest(unittest.TestCase):
    def test_roundtrip(self):
        key = os.urandom(16)
        data = b"\xff\xd8\xff" + os.urandom(1000)
        self.assertEqual(ilink.aes_ecb_decrypt(encrypt(data, key), key), data)

    def test_key_formats(self):
        key = os.urandom(16)
        self.assertEqual(ilink.parse_aes_key({"aeskey": key.hex()}), key)
        self.assertEqual(ilink.parse_aes_key({"media": {"aes_key": base64.b64encode(key).decode()}}), key)
        hex_b64 = base64.b64encode(key.hex().encode()).decode()
        self.assertEqual(ilink.parse_aes_key({"media": {"aes_key": hex_b64}}), key)
        self.assertIsNone(ilink.parse_aes_key({"media": {}}))

    def test_chunks(self):
        self.assertEqual(chunks("abcde", 2), ["ab", "cd", "e"])
        self.assertEqual(chunks("", 2), [""])


class FakeBot:
    def __init__(self):
        self.texts, self.images, self.sent = [], [], []

    def handle_text(self, uid, text):
        self.texts.append((uid, text))

    def handle_image(self, uid, data, mime=None):
        self.images.append((uid, data))

    def send(self, uid, text):
        self.sent.append((uid, text))


class DispatchTest(DBTestCase):
    def setUp(self):
        super().setUp()
        self.bot = FakeBot()
        self.acc = ilink.Account({"_name": "me", "bot_token": "t", "ilink_bot_id": "bot1@im.bot",
                                  "ilink_user_id": "owner@im.wechat"}, self.bot)
        # 同步执行，方便断言
        self.acc.worker = mock.Mock(submit=lambda f, *a: f(*a))

    def msg(self, uid="owner@im.wechat", items=None, mid="1", ctx="ctx1"):
        return {"message_type": 1, "message_id": mid, "from_user_id": uid, "context_token": ctx,
                "item_list": items or [{"type": 1, "text_item": {"text": "你好"}}]}

    def test_text_dispatch_and_session_saved(self):
        self.acc.dispatch(self.msg())
        self.assertEqual(self.bot.texts, [("owner@im.wechat", "你好")])
        rows = run("SELECT * FROM ilink_sessions", fetch=True)
        self.assertEqual(rows[0]["context_token"], "ctx1")
        self.acc.dispatch(self.msg(mid="2", ctx="ctx2"))
        rows = run("SELECT * FROM ilink_sessions", fetch=True)
        self.assertEqual((len(rows), rows[0]["context_token"]), (1, "ctx2"))

    def test_duplicates_and_bot_messages_ignored(self):
        self.acc.dispatch(self.msg())
        self.acc.dispatch(self.msg())
        self.acc.dispatch({**self.msg(mid="9"), "message_type": 2})
        self.assertEqual(len(self.bot.texts), 1)

    def test_owner_filter(self):
        old = config.ILINK_ONLY_OWNER
        config.ILINK_ONLY_OWNER = True
        try:
            self.acc.dispatch(self.msg(uid="stranger@im.wechat"))
            self.assertEqual(self.bot.texts, [])
            # 只比较 @ 前面的部分
            self.acc.dispatch(self.msg(uid="owner@other", mid="3"))
            self.assertEqual(len(self.bot.texts), 1)
        finally:
            config.ILINK_ONLY_OWNER = old

    def test_voice_text_used(self):
        self.acc.dispatch(self.msg(items=[{"type": 3, "voice_item": {"text": "午饭吃了面"}}]))
        self.assertEqual(self.bot.texts[0][1], "午饭吃了面")

    def test_image_downloaded_and_decrypted(self):
        key = os.urandom(16)
        data = b"\x89PNG\r\n\x1a\n" + os.urandom(200)
        resp = mock.Mock(content=encrypt(data, key), raise_for_status=lambda: None)
        item = {"type": 2, "image_item": {"aeskey": key.hex(), "media": {"encrypt_query_param": "abc"}}}
        with mock.patch("bot.channels.ilink.requests.get", return_value=resp) as get:
            self.acc.dispatch(self.msg(items=[item]))
        self.assertEqual(self.bot.images, [("owner@im.wechat", data)])
        self.assertEqual(get.call_args.kwargs["params"], {"encrypted_query_param": "abc"})

    def test_unknown_type_gets_hint(self):
        self.acc.dispatch(self.msg(items=[{"type": 4, "file_item": {}}]))
        self.assertIn("看不懂", self.bot.sent[0][1])


class ChannelSendTest(DBTestCase):
    def test_send_requires_session(self):
        ch = ilink.IlinkChannel(FakeBot())
        self.assertFalse(ch.send_text("nobody", "hi"))

    def test_send_uses_latest_context_token(self):
        config.ILINK_ACCOUNTS_DIR.mkdir(parents=True, exist_ok=True)
        (config.ILINK_ACCOUNTS_DIR / "me.json").write_text(
            '{"bot_token": "t", "ilink_bot_id": "bot1", "ilink_user_id": "u"}')
        ch = ilink.IlinkChannel(FakeBot())
        run("INSERT INTO ilink_sessions VALUES ('bot1', 'u', 'ctxA', ?)", (now_iso(),))
        with mock.patch.object(ilink.Account, "post", return_value={}) as post:
            self.assertTrue(ch.send_text("u", "hello"))
        body = post.call_args[0][1]["msg"]
        self.assertEqual((body["to_user_id"], body["context_token"]), ("u", "ctxA"))
        self.assertEqual(body["item_list"][0]["text_item"]["text"], "hello")

    def test_send_skipped_after_push_window(self):
        config.ILINK_ACCOUNTS_DIR.mkdir(parents=True, exist_ok=True)
        (config.ILINK_ACCOUNTS_DIR / "me.json").write_text(
            '{"bot_token": "t", "ilink_bot_id": "bot1", "ilink_user_id": "u"}')
        ch = ilink.IlinkChannel(FakeBot())
        old = (now() - datetime.timedelta(hours=config.ILINK_PUSH_WINDOW_HOURS + 1)).isoformat()
        run("INSERT INTO ilink_sessions VALUES ('bot1', 'u', 'ctxA', ?)", (old,))
        with mock.patch.object(ilink.Account, "post", return_value={}) as post:
            self.assertFalse(ch.send_text("u", "hello"))
            post.assert_not_called()
        self.assertLess(ch.push_deadline("u"), now())


if __name__ == "__main__":
    unittest.main()
