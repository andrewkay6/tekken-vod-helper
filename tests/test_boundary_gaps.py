import sys
import os
import unittest
import math
import copy

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts"))
from inspect_boundary_gaps import inspect_gaps


class TestInspectGaps(unittest.TestCase):

    def test_invalid_project_metadata_and_rows(self):
        for duration in (True, -1, 0, float('nan'), float('inf')):
            with self.subTest(duration=duration), self.assertRaises(ValueError):
                inspect_gaps({'duration': duration, 'matches': []})
        for row in (None, [], {'start': -1, 'end': 2}):
            with self.subTest(row=row), self.assertRaises(ValueError):
                inspect_gaps({'matches': [row]})

    def test_ordering_and_gaps(self):
        proj = {
            "duration": 100.0,
            "matches": [
                {"start": 0, "end": 10},
                {"start": 20, "end": 30},
                {"start": 50, "end": 60},
            ]
        }
        res = inspect_gaps(proj, limit=5)
        self.assertEqual(len(res["gaps"]), 2)
        self.assertEqual(res["gaps"][0]["gap_seconds"], 20.0)
        self.assertEqual(res["gaps"][0]["prev_idx"], 1)
        self.assertEqual(res["gaps"][0]["next_idx"], 2)
        self.assertEqual(res["gaps"][1]["gap_seconds"], 10.0)
        self.assertEqual(res["gaps"][1]["prev_idx"], 0)
        self.assertEqual(res["gaps"][1]["next_idx"], 1)

    def test_missing_ends_derived(self):
        proj = {
            "duration": 100.0,
            "matches": [
                {"start": 0, "end": 10},
                {"start": 20},
                {"start": 40, "end": 50},
            ]
        }
        res = inspect_gaps(proj)
        self.assertEqual(len(res["unresolved_indices"]), 0)
        self.assertEqual(res["derived_indices"], [1])
        # The explicit end at 10 and next start at 20 still establish a gap.
        self.assertEqual(len(res["gaps"]), 1)
        self.assertEqual(res["gaps"][0]["gap_seconds"], 10)

    def test_false_start_long_duration(self):
        proj = {
            "duration": 100.0,
            "matches": [
                {"start": 0, "end": 2},
                {"start": 2, "end": 80},
                {"start": 80, "end": 82},
            ]
        }
        res = inspect_gaps(proj, limit=5)
        self.assertEqual(len(res["long_matches"]), 1)
        self.assertEqual(res["long_matches"][0]["match_idx"], 1)
        self.assertTrue(res["long_matches"][0]["ratio_to_median"] > 2.0)

    def test_invalid_nonfinite_input(self):
        proj = {
            "duration": 100.0,
            "matches": [
                {"start": 0, "end": 10},
                {"start": float('inf'), "end": 20},
                {"start": 30, "end": 40},
                {"start": True, "end": 50},
            ]
        }
        with self.assertRaises(ValueError):
            inspect_gaps(proj, limit=5)

    def test_no_overlap(self):
        proj = {
            "duration": 50.0,
            "matches": [
                {"start": 0, "end": 20},
                {"start": 10, "end": 25},
                {"start": 30, "end": 40},
            ]
        }
        with self.assertRaises(ValueError):
            inspect_gaps(proj, limit=5)

    def test_missing_final_unresolved(self):
        proj = {
            "matches": [
                {"start": 0, "end": 10},
                {"start": 20},
            ]
        }
        res = inspect_gaps(proj)
        self.assertIn(1, res["unresolved_indices"])
        self.assertEqual(res["project_duration"], 0)

    def test_next_missing_end_gap(self):
        proj = {
            "duration": 100.0,
            "matches": [
                {"start": 0, "end": 10},
                {"start": 30},
            ]
        }
        res = inspect_gaps(proj)
        self.assertEqual(len(res["gaps"]), 1)
        self.assertEqual(res["gaps"][0]["gap_seconds"], 20.0)

    def test_invalid_input_raises(self):
        with self.assertRaises(ValueError):
            inspect_gaps("not_a_dict", limit=5)
        with self.assertRaises(ValueError):
            inspect_gaps({}, limit=-1)
        with self.assertRaises(ValueError):
            inspect_gaps({}, limit=1.5)
        with self.assertRaises(ValueError):
            inspect_gaps({}, limit=True)
        with self.assertRaises(ValueError):
            inspect_gaps({"matches": [{"start": 5, "end": 3}]})
        with self.assertRaises(ValueError):
            inspect_gaps({"matches": [{"start": 10}], "duration": 5})

    def test_no_mutation(self):
        proj = {
            "duration": 100.0,
            "matches": [
                {"start": 0, "end": 10},
                {"start": 20},
            ]
        }
        orig = copy.deepcopy(proj)
        inspect_gaps(proj)
        self.assertEqual(proj, orig)

    def test_overlap_raises(self):
        proj = {
            "matches": [
                {"start": 0, "end": 5},
                {"start": 4, "end": 10},
            ]
        }
        with self.assertRaises(ValueError):
            inspect_gaps(proj)

    def test_boundary_reject(self):
        proj = {
            "matches": [{"start": 100, "end": 110}],
            "duration": 100
        }
        with self.assertRaises(ValueError):
            inspect_gaps(proj)


if __name__ == "__main__":
    unittest.main()
