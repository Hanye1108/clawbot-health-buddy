"""微信 ClawBot 通道（腾讯 iLink Bot 协议）。

服务器主动去长轮询拉消息，不需要公网端口、域名或备案。
协议要点：
- 扫码登录得到 bot_token；所有请求带 Bearer token。
- POST ilink/bot/getupdates 长轮询收消息（约 35 秒），用 get_updates_buf 游标增量拉取。
- POST ilink/bot/sendmessage 发消息，必须带上对方最近一条消息里的 context_token。
- 图片走微信 CDN，AES-128-ECB 加密，需要下载后解密。
"""
import base64
import datetime
import json
import logging
import random
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

import requests

from .. import config
from ..db import kv_get, kv_set, now, now_iso, run
from . import Channel, chunks

log = logging.getLogger(__name__)
CHANNEL_VERSION = "1.0.2"
TEXT_LIMIT = 1000


def _headers(token):
    return {
        "Content-Type": "application/json",
        "AuthorizationType": "ilink_bot_token",
        "Authorization": f"Bearer {token}",
        "X-WECHAT-UIN": base64.b64encode(str(random.randint(0, 0xFFFFFFFF)).encode()).decode(),
    }


def _norm(user_id):
    return (user_id or "").split("@")[0]


def load_accounts():
    accounts = []
    folder = config.ILINK_ACCOUNTS_DIR
    if not folder.is_dir():
        return accounts
    for path in sorted(folder.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            log.error("账号文件损坏：%s", path)
            continue
        if not data.get("bot_token"):
            continue
        data["_name"] = path.stem
        accounts.append(data)
    return accounts


# ---------- 图片解密 ----------
def parse_aes_key(image_item):
    """优先用 image_item.aeskey（32位hex）；其次 media.aes_key（base64，
    解出来是16字节原始密钥，或32个hex字符）。"""
    hex_key = image_item.get("aeskey")
    if hex_key:
        return bytes.fromhex(hex_key)
    b64 = (image_item.get("media") or {}).get("aes_key")
    if not b64:
        return None
    raw = base64.b64decode(b64)
    if len(raw) == 16:
        return raw
    if len(raw) == 32:
        return bytes.fromhex(raw.decode("ascii"))
    raise ValueError(f"无法识别的 aes_key 长度：{len(raw)}")


def aes_ecb_decrypt(data, key):
    from cryptography.hazmat.primitives import padding
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    dec = Cipher(algorithms.AES(key), modes.ECB()).decryptor()
    plain = dec.update(data) + dec.finalize()
    unpadder = padding.PKCS7(128).unpadder()
    return unpadder.update(plain) + unpadder.finalize()


def download_image(image_item):
    media = image_item.get("media") or {}
    param = media.get("encrypt_query_param")
    if not param:
        raise ValueError("图片消息里没有 encrypt_query_param")
    r = requests.get(f"{config.ILINK_CDN_URL}/download",
                     params={"encrypted_query_param": param}, timeout=30)
    r.raise_for_status()
    key = parse_aes_key(image_item)
    return aes_ecb_decrypt(r.content, key) if key else r.content


# ---------- 单个 ClawBot 账号 ----------
class Account:
    def __init__(self, data, bot):
        self.name = data["_name"]
        self.token = data["bot_token"]
        self.base = (data.get("baseurl") or config.ILINK_BASE_URL).rstrip("/")
        self.bot_id = data.get("ilink_bot_id") or self.name
        self.owner = data.get("ilink_user_id", "")
        self.bot = bot
        self.worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix=f"ilink-{self.name}")
        self.seen = []

    def post(self, path, body, timeout=15):
        raw = json.dumps({**body, "base_info": {"channel_version": CHANNEL_VERSION}},
                         ensure_ascii=False).encode("utf-8")
        r = requests.post(f"{self.base}/{path}", data=raw, headers=_headers(self.token), timeout=timeout)
        r.raise_for_status()
        return r.json() if r.text.strip() else {}

    def send(self, uid, text, context_token):
        for part in chunks(text, TEXT_LIMIT):
            resp = self.post("ilink/bot/sendmessage", {"msg": {
                "from_user_id": "", "to_user_id": uid,
                "client_id": f"chb-{uuid.uuid4().hex[:16]}",
                "message_type": 2, "message_state": 2,
                "context_token": context_token,
                "item_list": [{"type": 1, "text_item": {"text": part}}]}})
            if resp.get("ret") not in (None, 0) or resp.get("errcode") not in (None, 0):
                log.warning("[%s] 发送失败：%s", self.name, resp)
                return False
        return True

    def poll_forever(self):
        key = f"ilink_cursor:{self.bot_id}"
        buf = kv_get(key, "")
        fails = 0
        log.info("[%s] 开始接收消息", self.name)
        while True:
            try:
                resp = self.post("ilink/bot/getupdates", {"get_updates_buf": buf}, timeout=45)
            except requests.exceptions.ReadTimeout:
                continue
            except Exception as e:  # noqa: BLE001
                fails += 1
                log.warning("[%s] 拉取消息出错（第%d次）：%s", self.name, fails, e)
                time.sleep(30 if fails >= 3 else 5)
                continue
            code = resp.get("errcode") or resp.get("ret") or 0
            if code == -14:
                log.error("[%s] 登录已过期，需要重新扫码：python -m bot login %s", self.name, self.name)
                time.sleep(300)
                continue
            if code:
                fails += 1
                log.warning("[%s] 接口返回错误：%s", self.name,
                            {k: v for k, v in resp.items() if k not in ("msgs", "get_updates_buf")})
                time.sleep(30 if fails >= 3 else 5)
                continue
            fails = 0
            if resp.get("get_updates_buf"):
                buf = resp["get_updates_buf"]
                kv_set(key, buf)
            for msg in resp.get("msgs") or []:
                self.dispatch(msg)

    def dispatch(self, msg):
        if msg.get("message_type") != 1:
            return
        mid = msg.get("message_id") or msg.get("client_id")
        if mid:
            if mid in self.seen:
                return
            self.seen = (self.seen + [mid])[-300:]
        uid = msg.get("from_user_id") or ""
        if config.ILINK_ONLY_OWNER and self.owner and _norm(uid) != _norm(self.owner):
            log.warning("[%s] 忽略非扫码人的消息（如确认是本人，请在 .env 设置 ILINK_ONLY_OWNER=0）"
                        "：from=%s owner=%s", self.name, uid, self.owner)
            return
        ctx = msg.get("context_token") or ""
        if ctx:
            run("INSERT INTO ilink_sessions (account_id, user_id, context_token, updated_at) "
                "VALUES (?,?,?,?) ON CONFLICT(account_id, user_id) DO UPDATE SET "
                "context_token=excluded.context_token, updated_at=excluded.updated_at",
                (self.bot_id, uid, ctx, now_iso()))
        self.worker.submit(self._handle, uid, msg.get("item_list") or [])

    def _handle(self, uid, items):
        try:
            texts, image = [], None
            for it in items:
                t = it.get("type")
                if t == 1 and it.get("text_item"):
                    texts.append(it["text_item"].get("text", ""))
                elif t == 3 and (it.get("voice_item") or {}).get("text"):
                    texts.append(it["voice_item"]["text"])   # 语音自带转文字
                elif t == 2 and it.get("image_item") and image is None:
                    image = it["image_item"]
            if image is not None:
                try:
                    data = download_image(image)
                except Exception:  # noqa: BLE001
                    log.exception("[%s] 图片下载/解密失败", self.name)
                    self.bot.send(uid, "这张照片我没收到清楚，能用文字告诉我吃了什么吗？")
                    return
                self.bot.handle_image(uid, data)
            elif any(s.strip() for s in texts):
                self.bot.handle_text(uid, "\n".join(texts))
            else:
                self.bot.send(uid, "这种消息我暂时还看不懂～可以发文字、语音或者照片给我。")
        except Exception:  # noqa: BLE001
            log.exception("[%s] 处理消息出错", self.name)


class IlinkChannel(Channel):
    def __init__(self, bot):
        self.bot = bot
        self.accounts = {}
        for data in load_accounts():
            acc = Account(data, bot)
            self.accounts[acc.bot_id] = acc

    def _session(self, uid):
        rows = run("SELECT account_id, context_token, updated_at FROM ilink_sessions WHERE user_id=? "
                   "ORDER BY updated_at DESC LIMIT 1", (uid,), fetch=True)
        return dict(rows[0]) if rows else {}

    def push_deadline(self, uid):
        s = self._session(uid)
        if not s.get("updated_at"):
            return None
        last = datetime.datetime.fromisoformat(s["updated_at"])
        return last + datetime.timedelta(hours=config.ILINK_PUSH_WINDOW_HOURS)

    def send_text(self, uid, text):
        s = self._session(uid)
        acc = self.accounts.get(s.get("account_id"))
        ctx = s.get("context_token")
        if not acc or not ctx:
            log.warning("没有该用户的会话令牌，无法发送（她需要先给 ClawBot 发一条消息）")
            return False
        deadline = self.push_deadline(uid)
        if deadline and now() > deadline:
            # 超时后发送接口未必报错，但消息送不到，所以按时间主动判断
            log.info("距她上次发消息已超过 %s 小时，微信不允许主动发送，先暂存",
                     config.ILINK_PUSH_WINDOW_HOURS)
            return False
        try:
            return acc.send(uid, text, ctx)
        except Exception as e:  # noqa: BLE001
            log.warning("[%s] 发送异常：%s", acc.name, e)
            return False

    def run(self):
        if not self.accounts:
            raise SystemExit("没有找到 ClawBot 账号。请先运行：python -m bot login me")
        threads = []
        for acc in self.accounts.values():
            t = threading.Thread(target=acc.poll_forever, name=f"poll-{acc.name}", daemon=True)
            t.start()
            threads.append(t)
        for t in threads:
            t.join()


def login(name):
    """扫码登录一个 ClawBot 账号，凭据保存到 data/ilink/<name>.json"""
    import os
    import sys

    folder = config.ILINK_ACCOUNTS_DIR
    folder.mkdir(parents=True, exist_ok=True)
    os.chmod(folder, 0o700)
    path = folder / f"{name}.json"

    r = requests.get(f"{config.ILINK_BASE_URL}/ilink/bot/get_bot_qrcode",
                     params={"bot_type": 3}, timeout=15).json()
    code = r["qrcode"]
    content = r.get("qrcode_img_content") or code
    try:
        import qrcode
        q = qrcode.QRCode(border=1)
        q.add_data(content)
        q.make()
        q.print_ascii(invert=True)
    except ImportError:
        print("（没装 qrcode，无法在终端画二维码）")
    print("\n用要绑定的那个人的微信扫上面的二维码并确认。")
    print("终端二维码扫不出来时，把这个链接转成二维码再扫：", content)

    deadline = time.time() + 480
    while time.time() < deadline:
        try:
            s = requests.get(f"{config.ILINK_BASE_URL}/ilink/bot/get_qrcode_status",
                             params={"qrcode": code}, headers={"iLink-App-ClientVersion": "1"},
                             timeout=40).json()
        except requests.exceptions.RequestException:
            time.sleep(2)
            continue
        status = s.get("status")
        if status == "confirmed":
            s["saved_at"] = int(time.time())
            path.write_text(json.dumps(s, ensure_ascii=False), encoding="utf-8")
            os.chmod(path, 0o600)
            print(f"\n✅ 登录成功，已保存到 {path}")
            print("返回的字段：", sorted(s.keys()))
            print("下一步：重启服务让它生效 → systemctl restart clawbot-health-buddy")
            return 0
        if status == "expired":
            print("二维码过期了，请重新运行")
            return 1
        time.sleep(1)
    print("超时了，请重新运行")
    sys.stdout.flush()
    return 1
