import importlib.util
from pathlib import Path
import unittest

spec=importlib.util.spec_from_file_location('toe_geometry',Path(__file__).resolve().parents[1]/'scripts/toe_geometry.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)


class ToeGeometryTest(unittest.TestCase):
    def test_pivot_preserved_and_tail_slightly_past_furthest_marker(self):
        pivot=[.1,-.1,.03]
        r=module.layout([.1,0,.1],pivot,[.1,-.2,.03],[.1,-.21,.03])
        self.assertEqual(r['head'],pivot)
        self.assertAlmostEqual(r['tail'][1],-.2155,places=12)
        self.assertEqual(r['tail'][2],pivot[2])

    def test_scale_and_translation(self):
        points=[[.1,0,.1],[.1,-.1,.03],[.1,-.2,.03]]
        a=module.layout(*points)
        translated=[[v*3+shift for v,shift in zip(p,[2,4,6])] for p in points]
        b=module.layout(*translated)
        for x,y,shift in zip(a['tail'],b['tail'],[2,4,6]):self.assertAlmostEqual(x*3+shift,y,places=12)

    def test_invalid_geometry(self):
        with self.assertRaises(ValueError):module.layout([0,0,0],[0,0,0],[0,0,1])
        with self.assertRaises(ValueError):module.layout([0,0,0],[0,-2,0],[0,-1,0])


if __name__=='__main__':unittest.main()
