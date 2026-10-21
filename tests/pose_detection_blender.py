"""Synthetic T, canonical A, intermediate and asymmetric Rest -> A regressions."""
import json
from pathlib import Path
import runpy


def add_cancellable_modifiers(mesh):
    import bpy
    tri=mesh.modifiers.new('三角化','TRIANGULATE')
    render_only=mesh.modifiers.new('render_only_triangulate','TRIANGULATE')
    render_only.show_viewport=False
    group=bpy.data.node_groups.new('Smooth by Angle','GeometryNodeTree')
    group.interface.new_socket(name='Geometry',in_out='INPUT',socket_type='NodeSocketGeometry')
    group.interface.new_socket(name='Geometry',in_out='OUTPUT',socket_type='NodeSocketGeometry')
    source=group.nodes.new('NodeGroupInput')
    target=group.nodes.new('NodeGroupOutput')
    shade=group.nodes.new('GeometryNodeSetShadeSmooth')
    group.nodes.new('GeometryNodeInputMeshEdgeAngle')
    group.links.new(source.outputs['Geometry'],shade.inputs['Geometry'])
    group.links.new(shade.outputs['Geometry'],target.inputs['Geometry'])
    smooth=mesh.modifiers.new('按角度平滑','NODES')
    smooth.node_group=group
    return [tri,render_only,smooth]


def run(output_dir, package):
    import bpy
    from mathutils import Matrix
    root = Path(__file__).resolve().parents[1]
    fixture=runpy.run_path(str(root/'tests/rig_fixture.py'))['fixture']
    pose=runpy.run_path(str(root/'scripts/pose_detection.py'))
    template_api=runpy.run_path(str(root/'scripts/apose_template.py'))
    bake_api=runpy.run_path(str(root/'scripts/apose_bake.py'))
    rig=runpy.run_path(str(root/'scripts/auto_rig.py'))
    template=json.loads((root/'references/standard-apose.json').read_text(encoding='utf-8'))
    output=Path(output_dir)
    output.mkdir(parents=True,exist_ok=True)
    previous=bpy.context.window.scene
    results=[]
    try:
        for version in ('0.0','1.0'):
            for kind in ('t_pose','a_pose','intermediate','asymmetric'):
                scene=bpy.data.scenes.new('pose_detection_test_'+version+'_'+kind)
                bpy.context.window.scene=scene
                arm,mesh,mapping=fixture(version)
                if kind=='asymmetric':
                    bpy.context.view_layer.objects.active=arm
                    bpy.ops.object.mode_set(mode='EDIT')
                    try:
                        for name in ('腰','腰キャンセル.L','腰キャンセル.R','腰キャンセル.L.001'):
                            bone=arm.data.edit_bones.new(name)
                            bone.head=(0,.2,1);bone.tail=(0,.2,1.05)
                            bone.parent=arm.data.edit_bones['root']
                    finally:
                        bpy.ops.object.mode_set(mode='OBJECT')
                if kind!='t_pose':
                    template_api['apply_template'](arm.name,mapping,template,
                        alpha=1 if kind in ('a_pose','asymmetric') else .3,work_copy=True)
                    if kind=='asymmetric':
                        # Left A and right T in the same source, not an averaged label.
                        for semantic,name in mapping.items():
                            if semantic.startswith('right'):
                                arm.pose.bones[name].matrix_basis=Matrix.Identity(4)
                        bpy.context.view_layer.update()
                    bake_api['bake_apose'](arm.name,[mesh.name],1e-5)
                cancelled=add_cancellable_modifiers(mesh)
                bpy.context.view_layer.update()
                source_state=rig['_source_state'](arm,[mesh])
                coords=[v.co.copy() for v in mesh.data.vertices]
                keys={key.name:[v.co.copy() for v in key.data] for key in mesh.data.shape_keys.key_blocks}
                measured=pose['detect_pose'](arm.name,mapping)
                assert measured['classification']==kind,measured
                assert rig['_source_state'](arm,[mesh])==source_state
                if kind=='a_pose':
                    for _ in range(2):
                        repeat=pose['normalize_apose'](arm.name,[mesh.name],mapping,work_copy=True)
                        assert repeat['action']=='kept_existing_apose'
                    assert all(v.co==co for key in mesh.data.shape_keys.key_blocks for v,co in zip(key.data,keys[key.name]))
                plan=rig['plan_rig'](arm.name,options={'mmd_package':package})
                assert plan['pose_detection']['classification']==kind
                scheduled=plan['modifier_plans'][mesh.name]['disable_on_copy']
                assert len(scheduled)==3
                assert [m.show_viewport for m in cancelled]==[True,False,True]
                assert all(m.show_render for m in cancelled)
                if kind=='asymmetric':
                    assert len(plan['reused_controls'])==9
                    assert plan['reused_controls']['腰キャンセル左']=='腰キャンセル.L'
                    assert '腰キャンセル.L.001' in plan['duplicate_controls']
                    weighted=mesh.vertex_groups.new(name='腰')
                    weighted.add([0],.1,'REPLACE')
                    try:
                        try:
                            rig['plan_rig'](arm.name,options={'mmd_package':package})
                        except ValueError as exc:
                            assert 'skin weights' in str(exc)
                        else:
                            raise AssertionError('Weighted waist control was accepted for destructive reuse')
                    finally:
                        mesh.vertex_groups.remove(weighted)
                if kind=='t_pose':
                    unknown=mesh.modifiers.new('unrelated_geometry','BEVEL')
                    try:
                        rig['plan_rig'](arm.name,options={'mmd_package':package})
                    except ValueError as exc:
                        assert 'Unsupported evaluated modifier' in str(exc)
                    else:
                        raise AssertionError('Unknown modifier was silently disabled')
                    finally:
                        mesh.modifiers.remove(unknown)
                    try:
                        rig['plan_rig'](arm.name,options={'mmd_package':package,'apose_mode':'already_verified'})
                    except ValueError as exc:
                        assert 'already_verified' in str(exc)
                    else:
                        raise AssertionError('Unmeasured bypass accepted T-pose')
                    env=rig['convert_rig'].__globals__
                    old_build=env['_build']
                    scenes_before={s.name for s in bpy.data.scenes}
                    def fail_after_cleanup(*args):
                        work_mesh=args[1][0]
                        assert all(not m.show_viewport and not m.show_render for m in work_mesh.modifiers if m.type!='ARMATURE')
                        raise RuntimeError('intentional failure after modifier cleanup')
                    env['_build']=fail_after_cleanup
                    try:
                        try:
                            rig['convert_rig'](arm.name,output/(version+'_failed_cleanup'),options={'mmd_package':package,'internal_naming':'legacy'})
                        except RuntimeError as exc:
                            assert 'intentional failure' in str(exc)
                        else:
                            raise AssertionError('Expected deliberate build failure')
                    finally:
                        env['_build']=old_build
                    assert {s.name for s in bpy.data.scenes}==scenes_before
                    assert rig['_source_state'](arm,[mesh])==source_state
                report=rig['convert_rig'](arm.name,output/(version+'_'+kind),options={'mmd_package':package,'internal_naming':'legacy'})
                assert report['status']=='rig_prepared',report
                assert report['source_unchanged']
                assert report['validation']['checks']['waist']['status']=='passed'
                assert len(report['validation']['checks']['waist']['motion']['samples'])==6
                assert report['apose']['detection_before']['classification']==kind
                assert report['apose']['detection_after']['matches_target_apose']
                assert report['apose']['action']==('kept_existing_apose' if kind=='a_pose' else 'normalized_to_apose')
                work=bpy.data.scenes[report['scene']]
                target=bpy.data.objects[report['meshes'][0]]
                assert len(report['modifier_cleanup'][0]['modifiers'])==3
                assert all(not m.show_viewport and not m.show_render for m in target.modifiers if m.type!='ARMATURE')
                assert [m.show_viewport for m in cancelled]==[True,False,True]
                assert all(m.show_render for m in cancelled)
                assert len(target.data.shape_keys.key_blocks)==len(keys)
                if kind=='a_pose':
                    assert report['bake']['status']=='not_needed'
                    assert all(v.co==co for v,co in zip(target.data.vertices,coords))
                    assert all(v.co==co for key in target.data.shape_keys.key_blocks for v,co in zip(key.data,keys[key.name]))
                assert rig['_source_state'](arm,[mesh])==source_state
                assert all(v.co==co for v,co in zip(mesh.data.vertices,coords))
                bpy.context.window.scene=work
                target_arm=bpy.data.objects[report['armature']]
                root_obj=bpy.data.objects[report['model_root']]
                displayed={i.name for f in root_obj.mmd_root.display_item_frames for i in f.data if i.type=='BONE'}
                assert {'腰','腰キャンセル左','腰キャンセル右'}<=displayed
                if kind=='asymmetric':
                    assert '腰キャンセル.L.001' not in target_arm.data.bones
                if kind=='t_pose':
                    waist_api=runpy.run_path(str(root/'scripts/waist_tools.py'))
                    cancel=target_arm.pose.bones['腰キャンセル左']
                    cancel.mmd_bone.additional_transform_influence=.5
                    assert waist_api['validate_waist'](target_arm.name,motion=False)['status']=='failed'
                    cancel.mmd_bone.additional_transform_influence=-1
                    constraint=cancel.constraints['mmd_additional_rotation']
                    original_mute=constraint.mute
                    constraint.mute=True
                    try:
                        assert waist_api['validate_waist'](target_arm.name)['status']=='failed'
                    finally:
                        constraint.mute=original_mute
                assert rig['backtest_rig'](report['armature'])['status']=='passed'
                renamed=report['naming']['renamed']
                final_mapping={semantic:renamed.get(name,name) for semantic,name in mapping.items()}
                detected=pose['detect_pose'](report['armature'],final_mapping)
                assert detected['matches_target_apose'],detected
                results.append(dict(version=version,input=kind,action=report['apose']['action'],
                                    final_error_degrees=detected['max_angular_error_degrees'],
                                    source_unchanged=True,ik='passed',known_modifiers_disabled=3,
                                    source_modifier_flags_preserved=True,waist='passed',
                                    waist_controls_reused=kind=='asymmetric',waist_cancel_tamper_detected=kind=='t_pose'))
        (output/'pose-detection-tests.json').write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf-8')
        return results
    finally:
        bpy.context.window.scene=previous
