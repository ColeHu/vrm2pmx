from pathlib import Path
import runpy
import unittest

API=runpy.run_path(str(Path(__file__).resolve().parents[1]/'scripts/arm_twist.py'))
SPEC=API['specifications']()[0]


class ArmTwist(unittest.TestCase):
    def test_vertical_helpers_share_scaled_length_and_main_tails_are_joints(self):
        segments=[]
        for spec in API['specifications']():
            end=(10,0,-4) if spec['name'].endswith('腕捩') else (8,1,-3)
            layout=API['compute_layout']((0,0,0),end)
            segments.append(dict(spec,layout=layout,distal_head=list(end)))
        display=API['display_plan'](segments)
        self.assertGreater(display['helper_length'],0)
        for spec in segments:
            self.assertEqual(spec['layout']['main_tail'],spec['distal_head'])
            for head,tail in zip(spec['layout']['helpers'],spec['layout']['helper_tails']):
                self.assertEqual(head[:2],tail[:2])
                self.assertAlmostEqual(tail[2]-head[2],display['helper_length'])

    def test_plugin_layout_and_expanded_dominant_span(self):
        layout=API['compute_layout']((0,0,0),(10,0,0),[(-1,0,0),(11,0,0)])
        self.assertEqual(layout['pivot'],[6,0,0])
        self.assertEqual(layout['minimum'],-7)
        self.assertEqual(layout['maximum'],5)
        self.assertEqual([p[0] for p in layout['helpers']],[2,5,8])

    def test_distribution_preserves_minor_skin_and_total(self):
        layout=API['compute_layout']((0,0,0),(10,0,0))
        old={SPEC['parent']:.99,'accessory':.01}
        new=API['transfer_weights'](old,(4.2,0,0),SPEC,layout)
        self.assertAlmostEqual(new['accessory'],.01)
        self.assertAlmostEqual(sum(new.values()),1)
        self.assertEqual(set(new),{SPEC['name']+'1',SPEC['name']+'2','accessory'})
        # Endpoints never leak weight onto the elbow, including exact t=4.
        endpoint=API['transfer_weights']({SPEC['parent']:1},(9.2,0,0),SPEC,layout)
        self.assertEqual(set(endpoint),{SPEC['name']})
        self.assertAlmostEqual(endpoint[SPEC['name']],1)

    def test_joint_mixture_transfers_only_parent_share(self):
        layout=API['compute_layout']((0,0,0),(10,0,0))
        old={SPEC['parent']:.4,SPEC['distal']:.6}
        new=API['transfer_weights'](old,(8,0,0),SPEC,layout)
        self.assertEqual(new,{SPEC['name']:.4,SPEC['distal']:.6})
        self.assertEqual(API['transfer_weights'](old,(2,0,0),SPEC,layout),old)

    def test_reject_nonfinite_collapsed_and_overflow(self):
        for end in [(0,0,0),(float('nan'),0,0)]:
            with self.assertRaises(ValueError):API['compute_layout']((0,0,0),end)
        layout=API['compute_layout']((0,0,0),(10,0,0))
        with self.assertRaisesRegex(ValueError,'four PMX'):
            API['transfer_weights']({SPEC['parent']:.991,'a':.003,'b':.003,'c':.003},(4.2,0,0),SPEC,layout)


if __name__=='__main__':
    unittest.main()
