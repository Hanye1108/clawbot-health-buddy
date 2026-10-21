import unittest
from unittest import mock

from helpers import DBTestCase, FakeChannel

from bot import config, core, persona
from bot import prompts as P


class PersonaTest(DBTestCase):
    def setUp(self):
        super().setUp()
        self.path = config.DATA_DIR / "persona.yaml"
        patcher = mock.patch.object(persona, "USER_PATH", self.path)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(P.load)       # 测完恢复默认

    def test_default_and_identity(self):
        P.load()
        self.assertEqual(P.NAME, "小禾")
        self.assertIn("DeepSeek", P.SYSTEM_PROMPT)
        self.assertIn(config.MODEL, P.SYSTEM_PROMPT)
        self.assertIn("小禾", P.INTRO[0])
        self.assertNotIn("{name}", "".join(P.INTRO) + P.WHATS_NEW)
        self.assertTrue(P.QUIET_NOTICE.startswith("\n\n（"))
        self.assertIn("【底线】", P.SYSTEM_PROMPT)        # 安全规则不在 persona 里，改不掉

    def test_override_partial(self):
        self.path.write_text("name: 阿暖\npersonality: 元气满满\nforbidden_words: [胖]\n"
                             "reminders:\n  water:\n    - 喝水啦{name}提醒你\n", encoding="utf-8")
        P.load()
        self.assertEqual(P.NAME, "阿暖")
        self.assertIn("元气满满", P.SYSTEM_PROMPT)
        self.assertIn("不要使用这些词：胖", P.SYSTEM_PROMPT)
        self.assertEqual(P.WATER, ["喝水啦阿暖提醒你"])
        self.assertEqual(len(P.MEAL_PROMPTS["lunch"]), 2)    # 没写的用默认
        self.assertIn("阿暖", P.INTRO[0])

    def test_errors_keep_old(self):
        P.load()
        for bad, word in (("name: [", "格式有误"), ("nmae: 阿暖\n", "不认识"), ("intro: []\n", "intro"),
                          ("reminders:\n  water: []\n", "water")):
            self.path.write_text(bad, encoding="utf-8")
            with self.assertRaises(persona.PersonaError) as cm:
                P.load()
            self.assertIn(word, str(cm.exception))
            self.assertEqual(P.NAME, "小禾")

    def test_reload_command(self):
        ch = FakeChannel()
        ch.admin = "admin"
        bot = core.Bot(ch)
        from bot import records
        records.ensure_user("admin")
        with mock.patch("bot.llm.chat", return_value="ok"):
            bot.handle_text("admin", "嗨")      # 先过掉更新说明
        self.path.write_text("name: 阿暖\n", encoding="utf-8")
        bot.handle_text("admin", "#reload")
        self.assertIn("阿暖", ch.sent[-1][1])
        self.path.write_text("name: [\n", encoding="utf-8")
        bot.handle_text("admin", "#reload")
        self.assertIn("加载失败", ch.sent[-1][1])
        self.assertEqual(P.NAME, "阿暖")
        ch.admin = None
        with mock.patch("bot.llm.chat", return_value="普通回复"):
            bot.handle_text("admin", "#reload")       # 非管理员当普通聊天
        self.assertEqual(ch.sent[-1][1], "普通回复")


if __name__ == "__main__":
    unittest.main()


class RelationshipTest(DBTestCase):
    def setUp(self):
        super().setUp()
        self.path = config.DATA_DIR / "persona.yaml"
        patcher = mock.patch.object(persona, "USER_PATH", self.path)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(P.load)

    def test_relationship(self):
        P.load()
        self.assertNotIn("【角色关系】", P.SYSTEM_PROMPT)
        self.path.write_text("name: 小新\nrelationship: 她的恋人\n", encoding="utf-8")
        P.load()
        self.assertIn("你的身份是：她的恋人", P.SYSTEM_PROMPT)
        self.assertIn("如实说自己是AI", P.SYSTEM_PROMPT)
        self.path.write_text("relationship: [1]\n", encoding="utf-8")
        with self.assertRaises(persona.PersonaError):
            P.load()

    def test_clear_chat_keeps_records(self):
        from bot import records
        ch = FakeChannel()
        bot = core.Bot(ch)
        records.ensure_user("u")
        records.add_chat("u", "user", "hi")
        records.log_water("u", {"ml": 250})
        bot.handle_text("u", "清空聊天记录")
        self.assertEqual(records.load_history("u"), [])
        self.assertEqual(records.water_summary("u")["喝水合计毫升"], 250)
