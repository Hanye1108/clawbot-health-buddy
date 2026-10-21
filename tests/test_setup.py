import os
import tempfile
import unittest
from pathlib import Path

from bot import setup_wizard as w


class SetupTest(unittest.TestCase):
    def test_write_env_updates_and_appends(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / ".env"
            p.write_text("# 注释\nexport DEEPSEEK_KEY=\"old\"\nCHANNEL=ilink\n", encoding="utf-8")
            w.write_env(p, {"DEEPSEEK_KEY": "sk-new", "PUBLIC_URL": "http://1.2.3.4"})
            text = p.read_text(encoding="utf-8")
            self.assertIn("# 注释", text)
            self.assertIn("DEEPSEEK_KEY=sk-new", text)
            self.assertNotIn("old", text)
            self.assertIn("CHANNEL=ilink", text)
            self.assertTrue(text.rstrip().endswith("PUBLIC_URL=http://1.2.3.4"))
            self.assertEqual(oct(os.stat(p).st_mode & 0o777), "0o600")
            self.assertEqual(w.read_env(p)["DEEPSEEK_KEY"], "sk-new")

    def test_clean(self):
        self.assertEqual(w.clean("QWEATHER_HOST", "abc.re.qweatherapi.com/ "), ("https://abc.re.qweatherapi.com", None))
        self.assertIsNone(w.clean("QWEATHER_HOST", "abcxyz.qweatherapi.com")[0])
        self.assertIn("sk-", w.clean("DEEPSEEK_KEY", "abc")[1])
        self.assertEqual(w.clean("PUBLIC_URL", "1.2.3.4:5000")[0], "http://1.2.3.4:5000")
        self.assertIsNone(w.clean("QWEATHER_KEY", "a b")[0])
        self.assertEqual(w.mask("sk-1234567890abcd"), "sk-1****abcd")


if __name__ == "__main__":
    unittest.main()
