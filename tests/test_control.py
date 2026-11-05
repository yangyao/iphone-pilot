import importlib.util
import math
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location(
    'hybrid_control', Path(__file__).resolve().parents[1] / 'hardware/tools/hybrid_control.py')
control = importlib.util.module_from_spec(spec)
spec.loader.exec_module(control)


class CoordinateTests(unittest.TestCase):
    def test_pixel_mapping(self):
        self.assertEqual(control.normalized(0, 498), 0)
        self.assertEqual(control.normalized(249, 498), 16384)
        self.assertLessEqual(control.normalized(497, 498), 32767)

    def test_invalid_coordinates(self):
        for value, extent in [(-1, 498), (498, 498), (0, 0), (1, -1),
                              (math.nan, 498), (math.inf, 498)]:
            with self.subTest(value=value, extent=extent):
                with self.assertRaises(ValueError):
                    control.normalized(value, extent)
