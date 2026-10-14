import unittest

from finetune.control.url_handler import URLHandler, paired, parse_bool, parse_url


class FakeApp:
    def __init__(self, ident):
        self.identifier = ident

    def matches(self, q):
        return q.lower() == self.identifier


class FakeEngine:
    def __init__(self):
        self.vol, self.mute, self.boost, self.dev = {}, {}, {}, {}
        self.apps = {"spotify": FakeApp("spotify")}
        self.reset = False

    def find_app(self, q):
        return next((a for a in self.apps.values() if a.matches(q)), None)

    def set_volume(self, i, v): self.vol[i] = v
    def get_volume(self, i): return self.vol.get(i, 1.0)
    def set_mute(self, i, m): self.mute[i] = m
    def get_mute(self, i): return self.mute.get(i, False)
    def toggle_mute(self, i): self.mute[i] = not self.get_mute(i)
    def set_boost(self, i, b): self.boost[i] = b
    def get_boost(self, i): return self.boost.get(i, 1.0)
    def set_device(self, i, d): self.dev[i] = d
    def reset_all_apps(self): self.reset = True


class URLTests(unittest.TestCase):
    def setUp(self):
        self.e = FakeEngine()
        self.h = URLHandler(self.e)

    def test_parse(self):
        self.assertEqual(parse_url("finetune://reset")[0], "reset")
        self.assertEqual(parse_url("http://x")[0], None)
        self.assertEqual(paired([("app", "a"), ("volume", "1"), ("volume", "2")], "volume"), [("a", "1")])
        self.assertTrue(parse_bool("YES"))
        self.assertIsNone(parse_bool("maybe"))

    def test_set_volumes_multi(self):
        self.h.handle("finetune://set-volumes?app=SPOTIFY&volume=50&app=discord&volume=20&app=x&volume=900")
        self.assertEqual(self.e.vol, {"spotify": 0.5, "discord": 0.2})

    def test_set_volume_with_boost(self):
        self.h.handle("finetune://set-volumes?app=spotify&volume=300")
        self.assertEqual(self.e.boost["spotify"], 3.0)
        self.assertAlmostEqual(self.e.vol["spotify"], 1.0)

    def test_step_and_mute(self):
        self.e.vol["spotify"] = 0.25
        self.h.handle("finetune://step-volume?app=spotify&direction=up")
        self.assertAlmostEqual(self.e.vol["spotify"], 0.55 ** 2)
        self.h.handle("finetune://set-mute?app=spotify&muted=true")
        self.assertTrue(self.e.mute["spotify"])
        self.h.handle("finetune://toggle-mute?app=spotify")
        self.assertFalse(self.e.mute["spotify"])

    def test_device_and_reset(self):
        self.h.handle("finetune://set-device?app=spotify&device=alsa_output.x")
        self.assertEqual(self.e.dev["spotify"], "alsa_output.x")
        self.h.handle("finetune://set-device?app=spotify&device=default")
        self.assertIsNone(self.e.dev["spotify"])
        self.h.handle("finetune://reset")
        self.assertTrue(self.e.reset)


if __name__ == "__main__":
    unittest.main()
