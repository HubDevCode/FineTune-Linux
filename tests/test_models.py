import math
import tempfile
import unittest
from pathlib import Path

from finetune.models import eq as eqm
from finetune.models import loudness
from finetune.models.volume import (
    VolumeHotkeyStep, VolumeTier, gain_to_slider, next_boost, normalize_boost, slider_to_gain,
    slider_fraction_for_system_gain, system_gain_for_slider_fraction,
)
from finetune.settings import AppSettings, SettingsManager, merge_priority_order, resolve_highest_priority


class VolumeMappingTests(unittest.TestCase):
    def test_square_law_round_trip(self):
        for s in (0.0, 0.1, 0.5, 0.75, 1.0):
            self.assertAlmostEqual(gain_to_slider(slider_to_gain(s)), s, places=6)
        self.assertAlmostEqual(slider_to_gain(0.5), 0.25)
        self.assertEqual(slider_to_gain(-1), 0.0)
        self.assertEqual(slider_to_gain(2.0), 1.0)

    def test_tier_mapping(self):
        self.assertAlmostEqual(slider_fraction_for_system_gain(0.25, VolumeTier.SOFTWARE), 0.5)
        self.assertAlmostEqual(slider_fraction_for_system_gain(0.25, VolumeTier.HARDWARE), 0.25)
        self.assertAlmostEqual(system_gain_for_slider_fraction(0.5, VolumeTier.SOFTWARE), 0.25)

    def test_boost_cycle(self):
        self.assertEqual([next_boost(b) for b in (1.0, 2.0, 3.0, 4.0)], [2.0, 3.0, 4.0, 1.0])
        self.assertEqual(normalize_boost("3"), 3.0)
        self.assertEqual(normalize_boost(5), 1.0)

    def test_hotkey_steps(self):
        self.assertEqual(VolumeHotkeyStep.NORMAL.slider_delta, 1 / 16)
        self.assertEqual(VolumeHotkeyStep("extraFine").slider_delta, 1 / 64)


class EQTests(unittest.TestCase):
    def test_twenty_presets_in_five_categories(self):
        self.assertEqual(len(eqm.PRESETS), 20)
        self.assertEqual(sum(len(eqm.presets_for(c)) for c in eqm.CATEGORIES), 20)
        for p in eqm.PRESETS:
            self.assertEqual(len(p.gains), eqm.BAND_COUNT)

    def test_normalize_and_clamp(self):
        s = eqm.EQSettings([1, 2])
        self.assertEqual(len(s.band_gains), 10)
        s = eqm.EQSettings([30] * 12)
        self.assertEqual(s.clamped_gains, [12.0] * 10)
        self.assertEqual(eqm.EQSettings([float("nan")] * 10).clamped_gains, [0.0] * 10)

    def test_matching_preset(self):
        self.assertEqual(eqm.matching_preset(eqm.preset_by_key("rock").gains).name, "Rock")
        self.assertIsNone(eqm.matching_preset([1] * 10))


class LoudnessTests(unittest.TestCase):
    def test_reference_level_is_flat(self):
        enabled, gains = loudness.section_gains_for_volume(1.0)
        self.assertFalse(enabled)
        self.assertTrue(all(abs(g) < 0.1 for g in gains))

    def test_low_volume_boosts_bass(self):
        enabled, gains = loudness.section_gains_for_volume(0.1)
        self.assertTrue(enabled)
        self.assertGreater(gains[0], 3.0)  # low shelf
        self.assertTrue(all(math.isfinite(g) for g in gains))

    def test_contour_1k_matches_phon(self):
        for phon in (40, 60, 80):
            self.assertAlmostEqual(loudness.contour_spl(phon)[17], phon, delta=0.5)


class SettingsTests(unittest.TestCase):
    def test_round_trip(self):
        with tempfile.TemporaryDirectory() as d:
            s = SettingsManager(Path(d))
            s.set_volume("firefox", 0.4)
            s.set_boost("firefox", 3.0)
            s.set_eq_settings(eqm.EQSettings([3] * 10), "firefox")
            s.pin_app("spotify", "Spotify", "spotify")
            s.set_device_volume_tier_override("dev", VolumeTier.SOFTWARE)
            s.create_user_preset("Mine", eqm.EQSettings([1] * 10))
            s.create_user_preset("Mine", eqm.EQSettings([2] * 10))
            s.flush_sync()
            t = SettingsManager(Path(d))
            self.assertEqual(t.get_volume("firefox"), 0.4)
            self.assertEqual(t.get_boost("firefox"), 3.0)
            self.assertEqual(t.get_eq_settings("firefox").band_gains, [3.0] * 10)
            self.assertTrue(t.is_pinned("spotify"))
            self.assertEqual(t.get_device_volume_tier_override("dev"), VolumeTier.SOFTWARE)
            self.assertEqual([p.name for p in t.get_user_presets()], ["Mine", "Mine 2"])

    def test_ignore_clears_state_and_unpins(self):
        with tempfile.TemporaryDirectory() as d:
            s = SettingsManager(Path(d))
            s.set_volume("a", 0.3)
            s.pin_app("a", "A", None)
            s.ignore_app("a", "A", None)
            self.assertIsNone(s.get_volume("a"))
            self.assertFalse(s.is_pinned("a"))
            self.assertTrue(s.is_ignored("a"))

    def test_prune_keeps_meaningful_entries(self):
        with tempfile.TemporaryDirectory() as d:
            s = SettingsManager(Path(d))
            s.set_volume("default_vol", 1.0)
            s.set_volume("custom", 0.5)
            s.set_volume("routed", 1.0)
            s.set_device_routing("routed", "x")
            s.prune_stale_settings(set())
            self.assertIsNone(s.get_volume("default_vol"))
            self.assertEqual(s.get_volume("custom"), 0.5)
            self.assertEqual(s.get_volume("routed"), 1.0)

    def test_corrupt_file_is_moved_aside(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "settings.json").write_text("{not json")
            s = SettingsManager(Path(d))
            self.assertEqual(s.app_volumes, {})
            self.assertTrue((Path(d) / "settings.json.corrupt").exists())

    def test_old_volumes_are_clamped(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "settings.json").write_text('{"appVolumes": {"a": 2.5, "b": -1}}')
            s = SettingsManager(Path(d))
            self.assertEqual(s.app_volumes, {"a": 1.0})

    def test_app_settings_defaults(self):
        a = AppSettings({"menuBarIconStyle": "Bogus", "hudStyle": "classic"})
        self.assertEqual(a.menu_bar_icon_style, "Default")
        self.assertEqual(a.hud_style, "classic")


class PriorityTests(unittest.TestCase):
    def test_merge_keeps_disconnected_anchored(self):
        old = ["a", "x", "b", "y", "c"]
        self.assertEqual(merge_priority_order(old, ["c", "a", "b"]), ["c", "a", "x", "b", "y"])

    def test_merge_prefix_group(self):
        self.assertEqual(merge_priority_order(["x", "a", "b"], ["b", "a"]), ["x", "b", "a"])

    def test_resolve_highest(self):
        self.assertEqual(resolve_highest_priority(["h", "s"], ["s", "h"]), "h")
        self.assertEqual(resolve_highest_priority(["h", "s"], ["s", "h"], excluding="h"), "s")
        self.assertEqual(resolve_highest_priority([], ["z"]), "z")
        self.assertIsNone(resolve_highest_priority(["a"], []))


if __name__ == "__main__":
    unittest.main()
