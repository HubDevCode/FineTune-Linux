import tempfile
import unittest
from pathlib import Path

from finetune.autoeq import parser
from finetune.autoeq.manager import AutoEQManager, edit_distance, match_score, normalize
from finetune.models.autoeq import HIGH_SHELF, LOW_SHELF, PEAKING, SOURCE_IMPORTED, AutoEQProfile, AutoEQFilter

SAMPLE = """Preamp: -6.2 dB
Filter 1: ON PK Fc 100 Hz Gain -2.3 dB Q 1.41
Filter 2: ON LSC Fc 105 Hz Gain 7.0 dB Q 0.71
Filter 3: ON HSC Fc 8000 Hz Gain 2.1 dB Q 0.71
Filter 4: OFF PK Fc 200 Hz Gain 1 dB Q 1
Filter 5: ON PK Fc 300 Hz Gain 45 dB Q 1
# comment
"""


class ParserTests(unittest.TestCase):
    def test_parse_parametric_eq(self):
        p = parser.parse(SAMPLE, "Test", SOURCE_IMPORTED)
        self.assertAlmostEqual(p.preamp_db, -6.2)
        self.assertEqual([f.type for f in p.filters], [PEAKING, LOW_SHELF, HIGH_SHELF])
        self.assertEqual(p.filters[1].frequency, 105)

    def test_no_filters_returns_none(self):
        self.assertIsNone(parser.parse("Preamp: -3 dB", "x", SOURCE_IMPORTED))

    def test_max_ten_filters(self):
        text = "\n".join(f"Filter {i}: ON PK Fc {100 + i} Hz Gain 1 dB Q 1" for i in range(15))
        self.assertEqual(len(parser.parse(text, "x", SOURCE_IMPORTED).filters), 10)

    def test_preamp_clamped(self):
        p = parser.parse("Preamp: -99 dB\nFilter: ON PK Fc 100 Hz Gain 1 dB Q 1", "x", SOURCE_IMPORTED)
        self.assertEqual(p.preamp_db, -30)

    def test_index_markdown(self):
        md = ("- [Sennheiser HD 600](./oratory1990/over-ear/Sennheiser%20HD%20600) by oratory1990\n"
              "- [Sennheiser HD 600](./crinacle/GRAS/over-ear/Sennheiser%20HD%20600) by crinacle on GRAS\n"
              "- [64 Audio A12t (m15 Apex module)](./crinacle/in-ear/64%20Audio%20A12t%20(m15%20Apex%20module)) by crinacle\n"
              "not a line\n")
        entries = parser.parse_index_markdown(md)
        self.assertEqual(len(entries), 2)
        hd = [e for e in entries if e.name == "Sennheiser HD 600"][0]
        self.assertEqual(hd.measured_by, "oratory1990")
        self.assertEqual(hd.relative_path, "oratory1990/over-ear/Sennheiser HD 600")
        a12 = [e for e in entries if e.name.startswith("64")][0]
        self.assertEqual(a12.relative_path, "crinacle/in-ear/64 Audio A12t (m15 Apex module)")

    def test_validated(self):
        p = AutoEQProfile("i", "n", SOURCE_IMPORTED, -40, [AutoEQFilter(PEAKING, -1, 1, 1),
                                                            AutoEQFilter(PEAKING, 100, 1, 1)])
        v = p.validated()
        self.assertEqual(v.preamp_db, -30)
        self.assertEqual(len(v.filters), 1)


class SearchTests(unittest.TestCase):
    def test_scoring_tiers(self):
        self.assertGreater(match_score("hd 600", normalize("hd 600"), "sennheiser hd 600", normalize("Sennheiser HD 600")), 100)
        self.assertGreater(match_score("hd600", "hd600", "sennheiser hd 600", "sennheiserhd600"), 0)
        self.assertGreater(match_score("senheiser", "senheiser", "sennheiser hd 600", "sennheiserhd600"), 0)
        self.assertEqual(match_score("zzz", "zzz", "sennheiser hd 600", "sennheiserhd600"), 0)
        self.assertEqual(edit_distance("kitten", "sitting"), 3)

    def test_import_and_search(self):
        with tempfile.TemporaryDirectory() as d:
            m = AutoEQManager(base_dir=Path(d), network=False)
            f = Path(d) / "My Cans ParametricEQ.txt"
            f.write_text(SAMPLE)
            prof = m.import_profile(str(f))
            self.assertEqual(prof.name, "My Cans")
            self.assertEqual(m.search("cans").entries[0].id, prof.id)
            m2 = AutoEQManager(base_dir=Path(d), network=False)
            self.assertIn(prof.id, m2.profiles)
            m2.delete_imported_profile(prof.id)
            self.assertNotIn(prof.id, m2.profiles)


if __name__ == "__main__":
    unittest.main()
