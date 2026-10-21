"""Source toe articulation, actual skin rotation and suffix-name references."""
import json
import math
from pathlib import Path
import runpy


def run(output_dir,mmd_package):
    import bpy
    from mathutils import Matrix,Vector
    root=Path(__file__).resolve().parents[1];output=Path(output_dir);output.mkdir(parents=True,exist_ok=True)
    rig=runpy.run_path(str(root/'scripts/auto_rig.py'))
    source=bpy.data.scenes.new('toe_pivot_names_fixture');bpy.context.window.scene=source
    arm,mesh,mapping=runpy.run_path(str(root/'tests/rig_fixture.py'))['fixture']('1.0')
    pivots={side:arm.data.bones[mapping[key+'Toes']].head_local.copy() for side,key in (('L','left'),('R','right'))}
    result=rig['convert_rig'](arm.name,output,options=dict(mmd_package=mmd_package))
    assert result['status']=='rig_prepared',result.get('validation')
    bpy.context.window.scene=bpy.data.scenes[result['scene']]
    target=bpy.data.objects[result['armature']];skin=bpy.data.objects[result['meshes'][0]]
    for role,name in result['roles'].items():
        if role.startswith(('左','右')) or role.endswith(('左','右')):
            assert name.endswith(('.L','.R')),(role,name)
        assert target.pose.bones[name].mmd_bone.name_j==role
    assert '足首.L' in target.data.bones and '足先EX.L' in target.data.bones
    order_api=runpy.run_path(str(root/'scripts/foot_order.py'))
    assert order_api['validate'](target.name)['status']=='passed'
    assert not order_api['repair'](target.name,work_copy=True)['changed']
    target.pose.bones['足先EX.L'].mmd_bone.transform_order=0
    assert order_api['validate'](target.name)['status']=='failed'
    assert rig['validate_rig'](target.name,scan=False)['checks']['foot_order']['status']=='failed'
    assert order_api['repair'](target.name,work_copy=True)['status']=='passed'
    # In-memory writer checks the actual PMX schedule and IK effector fields.
    import importlib
    writer=getattr(importlib.import_module(mmd_package+'.core.pmx.exporter'),'__PmxExporter')()
    model=importlib.import_module(mmd_package+'.core.pmx').Model()
    for name,value in [('model',model),('armature',target),('scale',1.0),('ik_angle_limits','DEFAULT')]:
        setattr(writer,'_PmxExporter__'+name,value)
    bone_map=getattr(writer,'_PmxExporter__exportBones')(bpy.data.objects[result['model_root']],[skin])
    indices={b.name:i for i,b in enumerate(model.bones)}
    for prefix in ('左','右'):
        ex=model.bones[indices[prefix+'足先EX']]
        assert ex.parent==indices[prefix+'足首']
        for role in ('足ＩＫ','つま先ＩＫ'):
            ik=model.bones[indices[prefix+role]]
            assert ik.isIK and ex.transform_order>ik.transform_order
        assert model.bones[indices[prefix+'足ＩＫ']].target==indices[prefix+'足首']
        assert model.bones[indices[prefix+'つま先ＩＫ']].target==indices[prefix+'つま先']
    # Repair an evaluated armature after changing a controller's layer.
    # Merely editing pose ID properties can leave the exporter's copy stale.
    target.pose.bones['足ＩＫ.L'].mmd_bone.transform_order=7
    order_api['repair'](target.name,work_copy=True)
    getattr(writer,'_PmxExporter__exportBones')(bpy.data.objects[result['model_root']],[skin])
    assert next(b for b in model.bones if b.name=='左足先EX').transform_order==8
    assert not order_api['repair'](target.name,work_copy=True)['changed']
    target.pose.bones['足ＩＫ.L'].mmd_bone.transform_order=1
    target.pose.bones['足先EX.L'].mmd_bone.transform_order=2
    order_api['repair'](target.name,work_copy=True)
    assert '左足首' not in target.data.bones
    assert skin.vertex_groups.get('足先EX.L') and not skin.vertex_groups.get('左足先EX')
    for key in skin.data.shape_keys.key_blocks:key.value=0
    def coords():
        target.update_tag();bpy.context.view_layer.update()
        evaluated=skin.evaluated_get(bpy.context.evaluated_depsgraph_get());data=evaluated.to_mesh()
        try:return [v.co.copy() for v in data.vertices]
        finally:evaluated.to_mesh_clear()
    baseline=coords();maximum_error=0.;moved=0
    transform=target.matrix_world.inverted()@skin.matrix_world;inverse=transform.inverted()
    for side in ('L','R'):
        pose=target.pose.bones['足先EX.'+side];pivot=pivots[side]
        assert (pose.bone.head_local-pivot).length<1e-6
        marker=target.data.bones['つま先.'+side].head_local
        ankle=target.data.bones['足首.'+side].head_local
        forward=marker-ankle;forward.z=0;forward.normalize()
        span=(marker-pivot).dot(forward)
        extension=(pose.bone.tail_local-marker).dot(forward)
        # Native additional-transform construction rebuilds float rest matrices.
        tolerance=max((marker-ankle).length*2e-5,1e-6)
        assert abs(extension-max(span*.05,(marker-ankle).xy.length*.01))<tolerance,(side,extension,span)
        before=pose.matrix_basis.copy();group=skin.vertex_groups[pose.name]
        for degrees in (-30,30):
            pose.matrix_basis=Matrix.Rotation(math.radians(degrees),4,'X')
            actual=coords()
            axis=pose.bone.matrix_local.to_3x3().col[0].normalized()
            rotation=Matrix.Rotation(math.radians(degrees),3,axis)
            for v,co,point in zip(skin.data.vertices,baseline,actual):
                weight=next((g.weight for g in v.groups if g.group==group.index),0.)
                if not weight:continue
                p=transform@co;expected=inverse@(p+(pivot+rotation@(p-pivot)-p)*weight)
                maximum_error=max(maximum_error,(expected-point).length)
                moved+=int((point-co).length>1e-6)
        pose.matrix_basis=before;coords()
    assert moved>0 and maximum_error<1e-5,(moved,maximum_error)
    pivot_api=runpy.run_path(str(root/'scripts/toe_pivot.py'))
    checkpoint=target.data.bones['足先EX.L'].head_local.copy()
    try:pivot_api['repair'](target.name,{'左':[5.,0.,0.]},work_copy=True)
    except ValueError as e:assert 'outside the local foot' in str(e)
    else:raise AssertionError('Display offset must not enter a toe pivot')
    assert target.data.bones['足先EX.L'].head_local==checkpoint
    assert max((a-b).length for a,b in zip(baseline,coords()))<1e-6
    assert rig['backtest_rig'](target.name)['status']=='passed'
    frames=bpy.data.objects[result['model_root']].mmd_root.display_item_frames
    assert all(item.name in target.data.bones for f in frames for item in f.data if item.type=='BONE')
    for p in target.pose.bones:
        for c in p.constraints:
            if getattr(c,'target',None)==target and getattr(c,'subtarget',''):assert c.subtarget in target.data.bones
        if p.mmd_bone.has_additional_rotation:assert p.mmd_bone.additional_transform_bone in target.data.bones
    report=dict(status='passed',suffix_names=True,source_toe_pivots_preserved=True,tail_slightly_beyond_markers=True,world_offset_guard=True,
                skin_rotation_max_error=maximum_error,nonzero_skin_motion=moved,ik_twist_waist_backtest=True,
                pmx_foot_order=True,pmx_ik_effectors=True)
    (output/'toe-pivot-names-test.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    return report
