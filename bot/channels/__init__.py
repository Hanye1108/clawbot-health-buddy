"""微信通道：ilink = 微信 ClawBot（个人微信，推荐）；mp = 公众号测试号。"""


class Channel:
    def send_text(self, uid, text):
        raise NotImplementedError

    def send_fallback(self, uid, text):
        """正常发送失败时的兜底方式；没有就返回 False。"""
        return False

    def push_deadline(self, uid):
        """主动推送的截止时间（datetime）；没有这个限制就返回 None。"""
        return None

    def admin_uid(self):
        """管理员的用户 ID（用来接收告警）；没有就返回 None。"""
        return None

    def status_lines(self):
        return []

    def run(self):
        raise NotImplementedError


def chunks(text, size):
    text = text or ""
    return [text[i:i + size] for i in range(0, len(text), size)] or [""]
