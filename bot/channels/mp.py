"""公众号测试号通道（备选）。

需要微信能从公网访问到本服务的 80 端口（可用 Nginx/OpenResty 反向代理到 MP_PORT）。
主动推送：客服消息只能在用户最近48小时内发过消息时使用，失败时改发模板消息兜底。
"""
import hashlib
import json
import logging
import threading
import time
import xml.etree.ElementTree as ET

import requests

from .. import config
from .. import records
from . import Channel, chunks

log = logging.getLogger(__name__)
API = "https://api.weixin.qq.com/cgi-bin"


class MpChannel(Channel):
    def __init__(self, bot):
        self.bot = bot
        self._tk = {"value": None, "expire": 0}
        self.seen = []

    def access_token(self):
        if self._tk["value"] and time.time() < self._tk["expire"]:
            return self._tk["value"]
        r = requests.get(f"{API}/token", params={
            "grant_type": "client_credential", "appid": config.WX_APPID,
            "secret": config.WX_APPSECRET}, timeout=10).json()
        if "access_token" not in r:
            raise RuntimeError(f"获取access_token失败: {r}")
        self._tk = {"value": r["access_token"], "expire": time.time() + r["expires_in"] - 300}
        return self._tk["value"]

    def _post(self, path, payload):
        return requests.post(f"{API}/{path}", params={"access_token": self.access_token()},
                             data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                             timeout=10).json()

    def send_text(self, uid, text):
        try:
            for part in chunks(text, 600):
                r = self._post("message/custom/send", {
                    "touser": uid, "msgtype": "text", "text": {"content": part}})
                if r.get("errcode") != 0:
                    log.warning("客服消息发送失败: %s", r)
                    return False
            return True
        except Exception:  # noqa: BLE001
            log.exception("客服消息异常")
            return False

    def send_fallback(self, uid, text):
        if not config.WX_TEMPLATE_ID:
            return False
        try:
            r = self._post("message/template/send", {
                "touser": uid, "template_id": config.WX_TEMPLATE_ID,
                "data": {"content": {"value": text[:100], "color": "#173177"}}})
            if r.get("errcode") != 0:
                log.warning("模板消息发送失败: %s", r)
                return False
            return True
        except Exception:  # noqa: BLE001
            log.exception("模板消息异常")
            return False

    def check_signature(self, args):
        items = sorted([config.WX_TOKEN, args.get("timestamp", ""), args.get("nonce", "")])
        return hashlib.sha1("".join(items).encode()).hexdigest() == args.get("signature")

    def _image(self, uid, pic_url):
        try:
            r = requests.get(pic_url, timeout=15)
            r.raise_for_status()
            mime = r.headers.get("Content-Type", "").split(";")[0]
        except Exception:  # noqa: BLE001
            log.exception("图片下载失败")
            self.bot.send(uid, "这张照片我没收到清楚，能用文字告诉我吃了什么吗？")
            return
        self.bot.handle_image(uid, r.content, mime)

    def make_app(self):
        from flask import Flask, request
        app = Flask(__name__)

        @app.route("/wx", methods=["GET", "POST"])
        def wx():
            if not self.check_signature(request.args):
                return "forbidden", 403
            if request.method == "GET":
                return request.args.get("echostr", "")

            root = ET.fromstring(request.data)
            uid = root.findtext("FromUserName")
            msg_type = root.findtext("MsgType")

            if msg_type == "event":
                if root.findtext("Event") == "subscribe":
                    records.ensure_user(uid)
                    threading.Thread(target=self.bot.welcome, args=(uid,), daemon=True).start()
                return "success"
            if msg_type not in ("text", "image"):
                return "success"

            msg_id = root.findtext("MsgId")
            if msg_id in self.seen:
                return "success"
            self.seen = (self.seen + [msg_id])[-200:]

            if msg_type == "image":
                threading.Thread(target=self._image, args=(uid, root.findtext("PicUrl")),
                                 daemon=True).start()
            else:
                threading.Thread(target=self.bot.handle_text,
                                 args=(uid, root.findtext("Content") or ""), daemon=True).start()
            return "success"

        return app

    def run(self):
        self.make_app().run(host="0.0.0.0", port=config.MP_PORT)
