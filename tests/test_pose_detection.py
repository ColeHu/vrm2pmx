"""Geometry labels are distinct from target-template acceptance."""
import copy
import json
import math
from pathlib import Path
import runpy
import unittest

ROOT = Path(__file__).resolve().parents[1]
API = runpy.run_path(str(ROOT/'scripts/pose_detection.py'))
TEMPLATE = json.loads((ROOT/'references/standard-apose.json').read_text(encoding='utf-8'))


def pose(left_drop, right_drop):
    value = copy.deepcopy(TEMPLATE)
    for row in value['segments']:
        side = 'left' if row['semantic'].startswith('left') else 'right'
        drop = math.radians(left_drop if side=='left' else right_drop)
        row['direction'] = [(1 if side=='left' else -1)*math.cos(drop),0,-math.sin(drop)]
    return value


class PoseDetection(unittest.TestCase):
    def test_t_pose(self):
        result = API['classify_directions'](pose(0,0),TEMPLATE)
        self.assertEqual(result['classification'],'t_pose')
        self.assertEqual(result['recommended_action'],'normalize_to_apose')

    def test_broad_a_pose_is_not_necessarily_canonical(self):
        result = API['classify_directions'](pose(25,25),TEMPLATE)
        self.assertEqual(result['classification'],'a_pose')
        self.assertFalse(result['matches_target_apose'])
        self.assertEqual(result['recommended_action'],'normalize_to_apose')

    def test_exact_template_is_kept(self):
        result = API['classify_directions'](TEMPLATE,TEMPLATE)
        self.assertEqual(result['classification'],'a_pose')
        self.assertTrue(result['matches_target_apose'])
        self.assertEqual(result['recommended_action'],'keep')

    def test_palm_roll_mismatch_requires_normalization(self):
        value=copy.deepcopy(TEMPLATE)
        row=next(r for r in value['segments'] if r['semantic']=='leftHand')
        row['palm_normal']=[-x for x in row['palm_normal']]
        result=API['classify_directions'](value,TEMPLATE)
        self.assertEqual(result['classification'],'a_pose')
        self.assertFalse(result['matches_target_apose'])

    def test_intermediate_and_asymmetric(self):
        self.assertEqual(API['classify_directions'](pose(12,12),TEMPLATE)['classification'],'intermediate')
        result = API['classify_directions'](pose(0,40),TEMPLATE)
        self.assertEqual(result['classification'],'asymmetric')
        self.assertEqual(result['status'],'passed')

    def test_hanging_arms_are_not_forced_into_a(self):
        result = API['classify_directions'](pose(85,85),TEMPLATE)
        self.assertEqual(result['status'],'needs_adaptation')
        self.assertEqual(result['recommended_action'],'adapt')


if __name__=='__main__':
    unittest.main()
