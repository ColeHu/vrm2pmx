"""Known smooth groups are identified by evidence, unrelated nodes stay blocked."""
from pathlib import Path
import runpy
from types import SimpleNamespace as NS
import unittest

API = runpy.run_path(str(Path(__file__).resolve().parents[1]/'scripts/modifier_policy.py'))


def smooth(nodes=(), name='Smooth by Angle'):
    kinds={'NodeGroupInput','NodeGroupOutput','GeometryNodeInputMeshEdgeAngle','GeometryNodeSetShadeSmooth'} | set(nodes)
    group=NS(name=name,nodes=[NS(bl_idname=n) for n in kinds],library_weak_reference=None)
    return NS(type='NODES',name='arbitrary_modifier_label',node_group=group,show_viewport=True,show_render=True)


class ModifierPolicy(unittest.TestCase):
    def test_graph_and_group_not_modifier_label(self):
        self.assertIsNotNone(API['smooth_by_angle_evidence'](smooth(name='Smooth by Angle.003')))
        unrelated=smooth({'GeometryNodeTransform'})
        unrelated.name='按角度平滑'
        self.assertIsNone(API['smooth_by_angle_evidence'](unrelated))

    def test_unrecognized_group_is_not_disabled(self):
        self.assertIsNone(API['smooth_by_angle_evidence'](smooth(name='Unrelated node group')))

    def test_render_only_triangulation_and_source_plan_are_preserved(self):
        tri=NS(type='TRIANGULATE',name='三角化',show_viewport=False,show_render=True)
        obj=NS(name='work_mesh',type='MESH',data=NS(users=1),modifiers=[tri,smooth()])
        plan=API['plan_mesh_modifiers'](obj)
        self.assertEqual(len(plan['disable_on_copy']),2)
        self.assertTrue(tri.show_render)
        result=API['disable_on_copy'](obj,plan['disable_on_copy'],work_copy=True)
        self.assertEqual(len(result),2)
        self.assertTrue(all(not m.show_viewport and not m.show_render for m in obj.modifiers))

    def test_explicit_work_copy_and_unknown_modifiers(self):
        mod=NS(type='BEVEL',name='bevel',show_viewport=True,show_render=True)
        obj=NS(name='mesh',type='MESH',data=NS(users=1),modifiers=[mod])
        self.assertEqual(len(API['plan_mesh_modifiers'](obj)['unsupported']),1)
        with self.assertRaises(ValueError):
            API['disable_on_copy'](obj,[],work_copy=False)


if __name__=='__main__':
    unittest.main()
