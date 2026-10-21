"""Real native append constraints + analytic skin deformation, no PMX files."""
import json
from pathlib import Path
import runpy


def run(output_dir,mmd_package):
    import bpy
    from mathutils import Matrix,Vector,Quaternion
    import math
    root=Path(__file__).resolve().parents[1]
    api=runpy.run_path(str(root/'scripts/arm_twist.py'))
    rig=runpy.run_path(str(root/'scripts/auto_rig.py'))
    prep=runpy.run_path(str(root/'scripts/prepare_model.py'))
    source=bpy.data.scenes.new('twist_skin_source');bpy.context.window.scene=source
    arm,mesh,mapping=runpy.run_path(str(root/'tests/rig_fixture.py'))['fixture']('1.0')
    mesh.vertex_groups[mapping['leftUpperArm']].add([15],.99,'REPLACE')
    mesh.vertex_groups[mapping['hips']].add([15],.01,'REPLACE')
    masked=mesh.shape_key_add(name='masked_arm_expression')
    masked.data[16].co.y-=.01;masked.vertex_group=mapping['leftUpperArm']
    bpy.context.view_layer.update()
    before=prep['source_fingerprint'](arm.name)
    result=rig['convert_rig'](arm.name,output_dir,options={'mmd_package':mmd_package,'internal_naming':'legacy'})
    assert result['status']=='rig_prepared',result.get('validation')
    bpy.context.window.scene=bpy.data.scenes[result['scene']]
    target=bpy.data.objects[result['armature']];skin=bpy.data.objects[result['meshes'][0]]
    assert len(api['required_names']())==16
    for spec in api['specifications']():
        assert all(result['roles'][spec['name']+suffix]==spec['name']+suffix for suffix in ('','1','2','3'))
    assert len(skin.data.shape_keys.key_blocks)==len(mesh.data.shape_keys.key_blocks)
    mask=skin.data.shape_keys.key_blocks['masked_arm_expression'].vertex_group
    assert mask.startswith('prep_shape_mask_左腕'),mask
    assert abs(skin.vertex_groups[mask].weight(15)-.99)<1e-6
    assert abs(skin.vertex_groups['下半身'].weight(15)-.01)<1e-6
    for key in skin.data.shape_keys.key_blocks:key.value=0
    def coords():
        target.update_tag();bpy.context.view_layer.update()
        obj=skin.evaluated_get(bpy.context.evaluated_depsgraph_get());data=obj.to_mesh()
        try:return [v.co.copy() for v in data.vertices]
        finally:obj.to_mesh_clear()
    baseline=coords()
    errors=[];moved=[]
    to_arm=target.matrix_world.inverted()@skin.matrix_world
    to_mesh=to_arm.inverted()
    names={g.index:g.name for g in skin.vertex_groups}
    for spec in api['specifications']():
        main=target.pose.bones[spec['name']]
        axis=(target.data.bones[spec['distal']].head_local-target.data.bones[spec['parent']].head_local).normalized()
        saved=main.matrix_basis.copy()
        for degrees in (-60,60):
            main.matrix_basis=Matrix.Rotation(math.radians(degrees),4,'Y')
            actual=coords();worst=0;count=0
            for vertex,co,posed in zip(skin.data.vertices,baseline,actual):
                original=to_arm@co;expected=Vector((0,0,0));total=0;affected=False
                for group in vertex.groups:
                    name=names[group.group];b=target.data.bones.get(name)
                    if not b or not b.use_deform:continue
                    value=original.copy();fraction=None;pivot=None
                    if name in [spec['name']+str(i) for i in (1,2,3)]:
                        fraction=int(name[-1])/4;pivot=b.head_local
                    elif name==main.name or main.name in [p.name for p in b.parent_recursive]:
                        fraction=1;pivot=main.bone.head_local
                    if fraction is not None:
                        value=pivot+Quaternion(axis,math.radians(degrees*fraction))@(value-pivot)
                        affected=True
                    expected+=value*group.weight;total+=group.weight
                if total>0 and affected:
                    expected=to_mesh@(expected/total)
                    worst=max(worst,(expected-posed).length)
                    count+=(posed-co).length>1e-5
            assert worst<1e-5,(spec['name'],degrees,worst)
            assert count>6,(spec['name'],degrees,count)
            errors.append(worst);moved.append(count)
        main.matrix_basis=saved;coords()
    neutral=max((a-b).length for a,b in zip(coords(),baseline))
    assert neutral<1e-6,neutral
    # Detect metadata tampering rather than trusting the stored success report.
    mm=target.pose.bones['左手捩2'].mmd_bone
    mm.additional_transform_influence=.2
    assert api['validate'](target,motion=False)['status']=='failed'
    mm.additional_transform_influence=.5
    assert api['validate'](target)['status']=='passed'
    # Existing rigs can be repaired without redoing skin; test the new display
    # contract rejects old layout and repair restores identical evaluated skin.
    bpy.context.view_layer.objects.active=target
    bpy.ops.object.mode_set(mode='EDIT')
    target.data.edit_bones['左手捩2'].tail=target.data.edit_bones['左手捩2'].head+Vector((.01,0,0))
    target.data.edit_bones['左腕捩'].tail=target.data.edit_bones['左腕捩'].head+Vector((.01,0,0))
    bpy.ops.object.mode_set(mode='OBJECT')
    assert api['validate'](target,motion=False)['status']=='failed'
    skin_before_repair=[[(g.group,g.weight) for g in v.groups] for v in skin.data.vertices]
    target.data.use_mirror_x=True
    api['align_display'](target,mmd_package=mmd_package,work_copy=True)
    assert target.data.use_mirror_x
    assert api['validate'](target)['status']=='passed'
    assert skin_before_repair==[[(g.group,g.weight) for g in v.groups] for v in skin.data.vertices]
    assert max((a-b).length for a,b in zip(coords(),baseline))<1e-6
    # Use the installed exporter's bone writer in memory: verify the exact
    # fields manual PMX export will consume, without writing a PMX file.
    import importlib
    writer=getattr(importlib.import_module(mmd_package+'.core.pmx.exporter'),'__PmxExporter')()
    model=importlib.import_module(mmd_package+'.core.pmx').Model()
    for name,value in [('model',model),('armature',target),('scale',1.0),('ik_angle_limits','DEFAULT')]:
        setattr(writer,'_PmxExporter__'+name,value)
    getattr(writer,'_PmxExporter__exportBones')(bpy.data.objects[result['model_root']],[skin])
    indices={b.name:i for i,b in enumerate(model.bones)}
    for spec in api['specifications']():
        b=model.bones[indices[spec['name']]]
        assert b.parent==indices[spec['parent']] and b.axis is not None and b.visible
        assert model.bones[indices[spec['distal']]].parent==indices[spec['name']]
        assert indices[spec['parent']]<indices[spec['name']]<indices[spec['distal']]
        assert b.displayConnection==indices[spec['distal']]
        assert (target.data.bones[spec['name']].tail_local-target.data.bones[spec['distal']].head_local).length<1e-6
        for i in (1,2,3):
            helper=model.bones[indices[spec['name']+str(i)]]
            assert helper.hasAdditionalRotate and not helper.hasAdditionalLocation
            assert helper.additionalTransform==[indices[spec['name']],i/4]
            assert not helper.visible
            assert (Vector(helper.displayConnection)-Vector((0,0,0))).length>0
            vector=target.data.bones[spec['name']+str(i)].tail_local-target.data.bones[spec['name']+str(i)].head_local
            assert vector.z>0 and abs(vector.x)+abs(vector.y)<1e-6
    # Only main controls enter visible PMX frames; helpers remain skin bones.
    frames=bpy.data.objects[result['model_root']].mmd_root.display_item_frames
    frame_names={item.name for frame in frames for item in frame.data if item.type=='BONE'}
    assert all(s['name'] in frame_names for s in api['specifications']())
    assert not any(s['name']+str(i) in frame_names for s in api['specifications']() for i in (1,2,3))
    bpy.context.window.scene=source;bpy.context.view_layer.update()
    assert prep['source_fingerprint'](arm.name)==before
    assert not list(Path(output_dir).rglob('*.pmx'))
    row=dict(status='passed',bones=16,analytic_skin_max_error=max(errors),minimum_moved_vertices=min(moved),
             neutral_return_error=neutral,source_unchanged=True,metadata_tamper_detected=True,
             shape_mask_preserved=True,minor_weights_preserved=True,exporter_bone_fields_in_memory=True,
             vertical_helpers=True,main_display_targets_downstream=True,in_place_display_repair=True,
             native_append_motion=result['validation']['checks']['arm_twist']['motion'])
    Path(output_dir,'arm-twist-test.json').write_text(json.dumps(row,ensure_ascii=False,indent=2),encoding='utf-8')
    return row
