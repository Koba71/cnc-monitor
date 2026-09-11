# -*- coding: utf-8 -*-
"""Юнит-тесты разбора текстов окна AutoCut — без Windows API."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from parse import has_any_value, parse_data, raw_text


class ParseDataTests(unittest.TestCase):
    def test_coords_and_speed_in_one_static(self):
        texts = {
            "X:-0016527": (10, 10, 120, 30),
            "Y:00087044": (10, 40, 120, 60),
            "<35.0Hz": (10, 70, 120, 90),
            "No.12": (10, 100, 80, 120),
        }
        data = parse_data(texts)
        self.assertEqual(data["X"], "-0016527")
        self.assertEqual(data["Y"], "00087044")
        self.assertEqual(data["Speed"], "35.0")
        self.assertEqual(data["DetailNo"], "12")
        self.assertTrue(has_any_value(data))

    def test_chinese_colon_and_hz_spacing(self):
        texts = {
            "X：123": (0, 0, 40, 16),
            "Y：-9": (0, 20, 40, 36),
            "35 Hz": (0, 40, 40, 56),
        }
        data = parse_data(texts)
        self.assertEqual(data["X"], "123")
        self.assertEqual(data["Y"], "-9")
        self.assertEqual(data["Speed"], "35")

    def test_speed_with_comma_decimal(self):
        data = parse_data({"<35,5Hz": (0, 0, 40, 16)})
        self.assertEqual(data["Speed"], "35.5")

    def test_working_and_surplus_in_same_label(self):
        texts = {
            "Working Time 006:34:39": (0, 0, 200, 20),
            "Surplus Time: 001:02:03": (0, 24, 200, 44),
        }
        data = parse_data(texts)
        self.assertEqual(data["WorkingTime"], "006:34:39")
        self.assertEqual(data["SurplusTime"], "001:02:03")

    def test_nearby_value_to_the_right(self):
        texts = {
            "Working Time": (0, 0, 80, 18),
            "006:34:39": (90, 0, 160, 18),
            "Surplus Time": (0, 30, 80, 48),
            "000:10:00": (90, 30, 160, 48),
            "X": (0, 60, 20, 78),
            "-44": (30, 60, 70, 78),
            "Y": (0, 90, 20, 108),
            "100": (30, 90, 70, 108),
            "Speed": (0, 120, 50, 138),
            "12.5Hz": (60, 120, 110, 138),
            "No.": (0, 150, 30, 168),
            "7": (40, 150, 55, 168),
        }
        data = parse_data(texts)
        self.assertEqual(data["WorkingTime"], "006:34:39")
        self.assertEqual(data["SurplusTime"], "000:10:00")
        self.assertEqual(data["X"], "-44")
        self.assertEqual(data["Y"], "100")
        self.assertEqual(data["Speed"], "12.5")
        self.assertEqual(data["DetailNo"], "7")

    def test_duplicate_static_suffix_stripped(self):
        self.assertEqual(raw_text("006:34:39 [2]"), "006:34:39")
        texts = {
            "Working Time": (0, 0, 80, 18),
            "006:34:39 [2]": (90, 0, 160, 18),
        }
        data = parse_data(texts)
        self.assertEqual(data["WorkingTime"], "006:34:39")

    def test_empty_texts_have_no_values(self):
        data = parse_data({})
        self.assertFalse(has_any_value(data))
        data = parse_data(None)
        self.assertFalse(has_any_value(data))


if __name__ == "__main__":
    unittest.main()
