"""Run in an isolated Blender process with VRM and mmd_tools registered."""
from pathlib import Path
import json
import runpy


def run(output_dir, mmd_package, texture_python):
    import bpy
    root = Path(__file__).resolve().parents[1]
    prep = runpy.run_path(str(root/'scripts/prepare_model.py'))
    materials = runpy.run_path(str(root/'scripts/auto_materials.py'))
    fixture = runpy.run_path(str(root/'tests/rig_fixture.py'))['fixture']
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    results = []
    for version in ('0.0','1.0'):
        source = bpy.data.scenes.new('prep_test_'+version)
        bpy.context.window.scene = source
        arm, mesh, mapping = fixture(version)
        old_empty=arm.data.collections.new('old_empty')
        old_parent=arm.data.collections.new('old_empty_parent')
        arm.data.collections.new('old_empty_child',parent=old_parent)
        old_used=arm.data.collections.new('old_humanoid_group')
        old_used.assign(arm.data.bones[mapping['head']])
        uv = mesh.data.uv_layers.new()
        for item in uv.data:
            item.uv = (.5,.5)
        image = bpy.data.images.new('source_pixels_'+version, 4,4, alpha=True,float_buffer=True)
        image.colorspace_settings.name = 'Linear Rec.709'
        image.pixels[:] = [.25,.5,.75,.4]*16
        image.update()
        mats = []
        for mode in ('OPAQUE','MASK','BLEND'):
            mat = bpy.data.materials.new('source_'+version+'_'+mode)
            mat.use_nodes = True
            t = mat.vrm_addon_extension.mtoon1
            t.enabled = True
            t.alpha_mode = mode
            t.alpha_cutoff = .5
            t.double_sided = True
            t.pbr_metallic_roughness.base_color_texture.index.source = image
            t.pbr_metallic_roughness.base_color_factor = (.8,.6,.4,.4)
            v = t.extensions.vrmc_materials_mtoon
            v.matcap_texture.index.source = image
            v.matcap_factor = (.8,.6,.4)
            v.outline_width_mode = 'worldCoordinates'
            v.outline_width_factor = .03
            v.outline_width_multiply_texture.index.source = image
            mesh.data.materials.append(mat)
            mats.append(mat)
        for poly in mesh.data.polygons:
            poly.material_index = poly.index%3
        group = mesh.vertex_groups.new(name='mmd_edge_scale')
        group.add(list(range(len(mesh.data.vertices))), .77, 'REPLACE')
        before = prep['source_fingerprint'](arm.name)
        report = prep['prepare_model'](arm.name, output/version,texture_policy='bake', mmd_package=mmd_package,
                                       texture_python=texture_python, save_blend=True)
        assert report['status']=='prepared_for_manual_edit', report
        assert report['source_unchanged'] and report['pmx_exported'] is False
        assert report['rig_backtest']['status']=='passed'
        for filename in ('preparation-report.json','rig-report.json','pose-detection.json'):
            assert not (Path(report['output_dir'])/filename).exists(),filename
        assert report['material_validation']['status']=='passed'
        target = bpy.data.objects[report['meshes'][0]]
        work_arm=bpy.data.objects[report['armature']]
        removed_names={'old_empty','old_empty_parent','old_empty_child','old_humanoid_group'}
        assert not removed_names & {c.name for c in work_arm.data.collections_all}
        assert removed_names <= {c.name for c in arm.data.collections_all}
        assert 'Root' in work_arm.data.collections_all and '指' in work_arm.data.collections_all
        collection_api=runpy.run_path(str(root/'scripts/bone_collections.py'))
        retained_parent=work_arm.data.collections.new('organization_parent')
        retained_child=work_arm.data.collections.new('occupied_child',parent=retained_parent)
        retained_child.assign(work_arm.data.bones['頭'])
        check=collection_api['prune_empty_collections'](work_arm.name,[retained_parent.name,retained_child.name])
        assert check['count']==0
        # Temporary hierarchy does not alter the actual rig's managed plan.
        work_arm.data.collections.remove(retained_child);work_arm.data.collections.remove(retained_parent)
        assert target.data != mesh.data
        assert len(target.data.shape_keys.key_blocks) == len(mesh.data.shape_keys.key_blocks)
        assert [p.material_index for p in target.data.polygons] == [p.material_index for p in mesh.data.polygons]
        assert len(target.data.uv_layers.active.data)==len(uv.data)
        assert all(abs(target.vertex_groups['mmd_edge_scale'].weight(i)-.77)<1e-5 for i in range(len(mesh.data.vertices)))
        for row in report['materials']['materials']:
            mm = bpy.data.materials[row['target']].mmd_material
            assert not mm.enabled_toon_edge and mm.edge_weight==0
            assert int(mm.sphere_texture_type)==0 and not mm.toon_texture and not mm.is_shared_toon_texture
            assert all(abs(a-b)<1e-5 for a,b in zip(mm.ambient_color,(.5,.5,.5)))
            assert row['textures']['sphere'] is None
        assert not list(Path(report['output_dir']).rglob('*_sphere.png'))
        assert not list(Path(report['output_dir']).rglob('*_edge_mask.npy'))
        assert not list(Path(report['output_dir']).rglob('*.pmx'))
        again = materials['convert_materials'](report['meshes'], output/version, work_copy=True,texture_policy='bake',
                                                mmd_package=mmd_package, texture_python=texture_python)
        assert again['status']=='already_converted'
        # Changing a prepared material must be detected on repeated validation.
        mm = target.data.materials[0].mmd_material
        mm.enabled_toon_edge = True
        assert prep['validate_materials'](report['meshes'],mmd_package=mmd_package)['status']=='failed'
        mm.enabled_toon_edge = False
        # A failed texture worker must restore material slots without touching source.
        bpy.context.window.scene = source
        assert before==prep['source_fingerprint'](arm.name)
        if version=='0.0':
            rig_only=prep['prepare_model'](arm.name,output/'rig-only-status',mmd_package=mmd_package,parts='rig')
            assert rig_only['status']=='rig_prepared_materials_pending',rig_only.get('error')
            for filename in ('preparation-report.json','rig-report.json','pose-detection.json'):
                assert not (Path(rig_only['output_dir'])/filename).exists(),filename
            assert rig_only['material_stage']=='not_requested'
            assert rig_only['ready_scopes']==['rig']
            assert 'base_materials_and_exporter_texture_slots' in rig_only['unverified']
            bpy.context.window.scene=source
        rollback_mesh = mesh.copy()
        rollback_mesh.data = mesh.data.copy()
        source.collection.objects.link(rollback_mesh)
        slots = list(rollback_mesh.data.materials)
        try:
            materials['convert_materials']([rollback_mesh.name], output/'failed-worker',work_copy=True,texture_policy='bake',
                                           mmd_package=mmd_package,texture_python='missing-python-executable')
        except OSError:
            pass
        else:
            raise AssertionError('Expected missing worker failure')
        assert list(rollback_mesh.data.materials)==slots
        # Force a failure after material assignment to exercise the actual rollback.
        from types import SimpleNamespace
        global_env = materials['execute_plan'].__globals__
        previous_runpy = global_env['runpy']
        before_mats = {m.as_pointer() for m in bpy.data.materials}
        def altered_helpers(path):
            helpers = previous_runpy.run_path(path)
            if Path(path).name == 'material_tools.py':
                create = helpers['create_material']
                def wrong_alpha(*args, **kwargs):
                    result = create(*args, **kwargs)
                    result.mmd_material.alpha = .123
                    return result
                helpers['create_material'] = wrong_alpha
            return helpers
        global_env['runpy'] = SimpleNamespace(run_path=altered_helpers)
        try:
            try:
                materials['convert_materials']([rollback_mesh.name], output/'assignment-rollback',work_copy=True,texture_policy='bake',
                                               mmd_package=mmd_package,texture_python=texture_python)
            except AssertionError:
                pass
            else:
                raise AssertionError('Expected assigned material verification failure')
        finally:
            global_env['runpy'] = previous_runpy
        assert list(rollback_mesh.data.materials)==slots
        assert {m.as_pointer() for m in bpy.data.materials}==before_mats
        bpy.data.objects.remove(rollback_mesh,do_unlink=True)
        assert before==prep['source_fingerprint'](arm.name)
        results.append(dict(version=version,status='passed',source_unchanged=True,rig_motion=True,
                            alpha_modes=True,base_materials=True,no_outline_sphere_toon=True,
                            idempotent=True,material_tamper_detected=True,worker_failure_preserves_slots=True,
                            post_assignment_rollback=True,
                            no_pmx_files=True,blend=report['blend_copy']))
    pose_tests=runpy.run_path(str(root/'tests/pose_detection_blender.py'))['run'](output/'pose-detection',mmd_package)
    surface_tests=runpy.run_path(str(root/'tests/material_surface_blender.py'))['run'](output/'surface-materials',mmd_package,texture_python)
    twist_tests=runpy.run_path(str(root/'tests/arm_twist_blender.py'))['run'](output/'arm-twist',mmd_package)
    original_tests=runpy.run_path(str(root/'tests/original_materials_blender.py'))['run'](output/'original-materials',mmd_package)
    toe_names=runpy.run_path(str(root/'tests/toe_pivot_names_blender.py'))['run'](output/'toe-pivot-names',mmd_package)
    result = dict(toe_pivot_names=toe_names,original_materials=original_tests,status='passed',blender=bpy.app.version_string,tests=results,pose_tests=pose_tests,surface_materials=surface_tests,arm_twist=twist_tests,rig_only_status='materials_pending')
    (output/'blender-smoke.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    return result
