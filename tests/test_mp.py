import hashlib
import time
import unittest
from unittest import mock

from helpers import DBTestCase

from bot import config
from bot.channels.mp import MpChannel


class MpTest(DBTestCase):
    def setUp(self):
        super().setUp()
        config.WX_TOKEN = "tok"
        self.bot = mock.Mock()
        self.ch = MpChannel(self.bot)
        self.app = self.ch.make_app().test_client()

    def sig(self):
        ts, nonce = str(int(time.time())), "n"
        s = hashlib.sha1("".join(sorted(["tok", ts, nonce])).encode()).hexdigest()
        return {"timestamp": ts, "nonce": nonce, "signature": s}

    def test_signature_and_echo(self):
        self.assertEqual(self.app.get("/wx", query_string={"signature": "bad"}).status_code, 403)
        r = self.app.get("/wx", query_string={**self.sig(), "echostr": "hello"})
        self.assertEqual(r.data, b"hello")

    def test_text_message_dispatched(self):
        xml = ("<xml><ToUserName>me</ToUserName><FromUserName>u1</FromUserName>"
               "<MsgType>text</MsgType><Content>你好</Content><MsgId>42</MsgId></xml>")
        with mock.patch("bot.channels.mp.threading.Thread") as th:
            r = self.app.post("/wx", query_string=self.sig(), data=xml.encode())
            r2 = self.app.post("/wx", query_string=self.sig(), data=xml.encode())   # 重复消息
        self.assertEqual((r.data, r2.data), (b"success", b"success"))
        self.assertEqual(th.call_count, 1)
        self.assertEqual(th.call_args.kwargs["args"], ("u1", "你好"))


if __name__ == "__main__":
    unittest.main()
