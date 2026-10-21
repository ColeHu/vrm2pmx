"""Disk-reference policy, exporter fields, source isolation and rollback."""
from pathlib import Path
import hashlib
import importlib
import json
import runpy


def run(output_dir,mmd_package):
    import bpy
    root=Path(__file__).resolve().parents[1]
    api=runpy.run_path(str(root/'scripts/auto_materials.py'))
    prep=runpy.run_path(str(root/'scripts/prepare_model.py'))
    output=Path(output_dir);output.mkdir(parents=True,exist_ok=True)
    scene=bpy.data.scenes.new('original_file_texture_test');bpy.context.window.scene=scene
    arm,mesh,_=runpy.run_path(str(root/'tests/rig_fixture.py'))['fixture']('1.0')
    uv=mesh.data.uv_layers.new(name='UVMap')
    for item in uv.data:item.uv=(.5,.5)
    generated=bpy.data.images.new('source_file_pixels',4,4,alpha=True)
    generated.pixels[:]=[.2,.4,.6,1]*16;generated.update()
    path=output/'原始 贴图.png';generated.save_render(str(path))
    image=bpy.data.images.load(str(path),check_existing=False)
    Fn=importlib.import_module(mmd_package+'.core.material').FnMaterial
    mats=[]
    for kind in ('mtoon','principled','mmd','image'):
        mat=bpy.data.materials.new('original_source_'+kind);mat.use_nodes=True
        if kind=='mtoon':
            t=mat.vrm_addon_extension.mtoon1;t.enabled=True
            t.pbr_metallic_roughness.base_color_texture.index.source=image
        elif kind=='mmd':Fn(mat).create_texture(str(path))
        else:
            tex=mat.node_tree.nodes.new('ShaderNodeTexImage');tex.image=image
            if kind=='image':mat.node_tree.links.new(tex.outputs['Color'],mat.node_tree.nodes.get('Material Output').inputs['Surface'])
            else:mat.node_tree.links.new(tex.outputs['Color'],mat.node_tree.nodes.get('Principled BSDF').inputs['Base Color'])
        mesh.data.materials.append(mat);mats.append(mat)
    for poly in mesh.data.polygons:poly.material_index=poly.index%4
    bpy.context.view_layer.update()
    fingerprint=prep['source_fingerprint'](arm.name)
    digest=hashlib.sha256(path.read_bytes()).hexdigest();source_path=image.filepath
    # Full entry defaults to original_file, including preflight and validation.
    report=prep['prepare_model'](arm.name,output/'full',mmd_package=mmd_package)
    assert report['status']=='prepared_for_manual_edit',report.get('error')
    for filename in ('preparation-report.json','rig-report.json','pose-detection.json'):
        assert not (Path(report['output_dir'])/filename).exists(),filename
    assert report['rig']['apose']['status']=='passed' and report['rig_backtest']['status']=='passed'
    assert report['texture_policy']=='original_file' and report['source_unchanged']
    target=bpy.data.objects[report['meshes'][0]]
    assert hashlib.sha256(path.read_bytes()).hexdigest()==digest and image.filepath==source_path
    assert [p.material_index for p in target.data.polygons]==[p.material_index for p in mesh.data.polygons]
    writer=getattr(importlib.import_module(mmd_package+'.core.pmx.exporter'),'__PmxExporter')()
    model=importlib.import_module(mmd_package+'.core.pmx').Model();setattr(writer,'_PmxExporter__model',model)
    for mat in target.data.materials:
        tex=Fn(mat).get_texture()
        assert tex.type=='IMAGE' and Path(tex.image.filepath).is_absolute()
        assert Path(tex.image.filepath).resolve()==path.resolve()
        assert tex.image!=image
        getattr(writer,'_PmxExporter__exportMaterial')(mat,1)
    assert len(model.materials)==4 and all(m.texture>=0 for m in model.materials)
    assert all(tuple(m.diffuse[:3])==(1,1,1) and tuple(m.specular)==(0,0,0) and tuple(m.ambient)==(.5,.5,.5) for m in model.materials)
    assert api['convert_materials']([target.name],output/'again',work_copy=True,mmd_package=mmd_package)['status']=='already_converted'
    bpy.context.window.scene=scene
    assert prep['source_fingerprint'](arm.name)==fingerprint
    # Missing files reject before material/slot mutations.
    image.filepath=str(output/'not-found.png');slots=list(mesh.data.materials)
    try:
        api['convert_materials']([mesh.name],output/'missing',work_copy=True,mmd_package=mmd_package)
    except ValueError:pass
    else:raise AssertionError('Missing original texture file accepted')
    assert list(mesh.data.materials)==slots;image.filepath=source_path
    # Fail validation after slot assignment to test the new path's rollback.
    import types
    original_api=runpy.run_path(str(root/'scripts/original_materials.py'))
    env=original_api['bind_original_materials'].__globals__;previous=env['runpy']
    def altered(path):
        module=previous.run_path(path)
        if Path(path).name=='prepare_model.py':module['validate_materials']=lambda *a,**k:dict(status='failed',issues=['forced rollback'])
        return module
    env['runpy']=types.SimpleNamespace(run_path=altered)
    before_materials={m.as_pointer() for m in bpy.data.materials}
    try:
        try:original_api['bind_original_materials']([mesh.name],output/'rollback',work_copy=True,mmd_package=mmd_package)
        except RuntimeError:pass
        else:raise AssertionError('Expected post-assignment validation failure')
    finally:env['runpy']=previous
    assert list(mesh.data.materials)==slots
    assert {m.as_pointer() for m in bpy.data.materials}==before_materials
    assert prep['source_fingerprint'](arm.name)==fingerprint
    assert not list(output.rglob('*.pmx'))
    result=dict(status='passed',default_original_file=True,source_shader_kinds=4,
                original_file_hash_unchanged=True,source_preserved=True,exporter_fields=True,
                missing_file_rejected=True,idempotent=True,post_assignment_rollback=True)
    (output/'original-material-test.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    return result
