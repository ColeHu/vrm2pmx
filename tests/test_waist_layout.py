import math
from pathlib import Path
import runpy
import unittest

API=runpy.run_path(str(Path(__file__).resolve().parents[1]/'scripts/waist_tools.py'))


class WaistLayout(unittest.TestCase):
    def test_anatomical_offsets_and_socket_pivots(self):
        layout=API['compute_layout']((0,0,1),(.1,0,.9),(-.1,0,.9))
        self.assertGreater(layout['waist_head'][1],0)
        self.assertLess(layout['waist_head'][2],1)
        self.assertLess(layout['waist_tail'][1],0)
        self.assertGreater(layout['waist_tail'][2],1)
        self.assertEqual(layout['cancel_left_head'],[.1,0,.9])
        self.assertEqual(layout['cancel_right_head'],[-.1,0,.9])

    def test_scale_translation_and_yaw_transport(self):
        a=API['compute_layout']((0,0,1),(.1,0,.9),(-.1,0,.9))
        def transform(v):
            return [3-2*v[1],4+2*v[0],5+2*v[2]]
        b=API['compute_layout'](transform((0,0,1)),transform((.1,0,.9)),transform((-.1,0,.9)))
        for key in ('waist_head','waist_tail','cancel_left_head','cancel_right_tail'):
            for actual,expected in zip(b[key],transform(a[key])):
                self.assertAlmostEqual(actual,expected)

    def test_collapsed_hips_rejected(self):
        with self.assertRaises(ValueError):
            API['compute_layout']((0,0,1),(0,0,.9),(0,0,.9))


if __name__=='__main__':
    unittest.main()
