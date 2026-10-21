"""Effective shader parsing and actual mmd_tools material-writer regression."""
from pathlib import Path
import importlib
import runpy


def run(output_dir, package, texture_python):
    import bpy
    root=Path(__file__).resolve().parents[1]
    api=runpy.run_path(str(root/'scripts/auto_materials.py'))
    prep=runpy.run_path(str(root/'scripts/prepare_model.py'))
    scene=bpy.data.scenes.new('effective_surface_test')
    bpy.context.window.scene=scene
    image=bpy.data.images.new('effective_base',4,4,alpha=True,float_buffer=True)
    image.colorspace_settings.name='Linear Rec.709'
    image.pixels[:]=[.25,.5,.75,.4]*16;image.update()
    mask=bpy.data.images.new('effective_mask',4,4,alpha=True,float_buffer=True)
    mask.colorspace_settings.name='Linear Rec.709'
    mask.pixels[:]=[.25,.25,.25,.8]*16;mask.update()
    data=bpy.data.meshes.new('effective_skin')
    data.from_pydata([(i*2+x,y,0) for i in range(5) for x,y in ((0,0),(1,0),(0,1))],[],
                     [(i*3,i*3+1,i*3+2) for i in range(5)])
    mesh=bpy.data.objects.new('effective_skin',data);scene.collection.objects.link(mesh)
    uv=data.uv_layers.new(name='TestUV')
    for item in uv.data:item.uv=(.5,.5)
    originals=[]
    for index,kind in enumerate(('image','principled','emission','mix','inverse_mix')):
        mat=bpy.data.materials.new('effective_'+kind);mat.use_nodes=True
        nodes=mat.node_tree.nodes;nodes.clear();links=mat.node_tree.links
        output=nodes.new('ShaderNodeOutputMaterial')
        tex=nodes.new('ShaderNodeTexImage');tex.image=image
        # A disconnected shader with a different color must never tint this image.
        unused=nodes.new('ShaderNodeBsdfPrincipled');unused.inputs['Base Color'].default_value=(.1,.1,.1,1)
        uv_node=nodes.new('ShaderNodeUVMap');uv_node.uv_map='TestUV'
        mapping=nodes.new('ShaderNodeMapping')
        links.new(uv_node.outputs['UV'],mapping.inputs['Vector'])
        links.new(mapping.outputs['Vector'],tex.inputs['Vector'])
        if kind=='principled':
            bsdf=nodes.new('ShaderNodeBsdfPrincipled')
            links.new(tex.outputs['Color'],bsdf.inputs['Base Color'])
            links.new(tex.outputs['Alpha'],bsdf.inputs['Alpha'])
            links.new(bsdf.outputs['BSDF'],output.inputs['Surface'])
        elif kind=='emission':
            emission=nodes.new('ShaderNodeEmission');emission.inputs['Strength'].default_value=1
            links.new(tex.outputs['Color'],emission.inputs['Color'])
            links.new(emission.outputs[0],output.inputs['Surface'])
        elif kind in ('mix','inverse_mix'):
            trans=nodes.new('ShaderNodeBsdfTransparent');mix=nodes.new('ShaderNodeMixShader')
            mask_tex=nodes.new('ShaderNodeTexImage');mask_tex.image=mask
            links.new(mask_tex.outputs['Color'],mix.inputs[0])
            links.new(trans.outputs[0],mix.inputs[1 if kind=='mix' else 2])
            links.new(tex.outputs['Color'],mix.inputs[2 if kind=='mix' else 1])
            links.new(mix.outputs[0],output.inputs['Surface'])
        else:
            links.new(tex.outputs['Color'],output.inputs['Surface'])
        data.materials.append(mat);data.polygons[index].material_index=index;originals.append(mat)
    plan=api['plan_materials']([mesh.name])
    assert [r['kind'] for r in plan['materials']]==['image_to_surface','principled','emission_base_color','transparent_mix','transparent_mix']
    uv.name='WrongUV'
    try:
        try:api['plan_materials']([mesh.name])
        except ValueError as exc:assert 'active UV' in str(exc)
        else:raise AssertionError('Mismatched named UV was accepted')
    finally:uv.name='TestUV'
    report=api['execute_plan'](plan,output_dir,work_copy=True,mmd_package=package,texture_python=texture_python)
    assert prep['validate_materials']([mesh.name],mmd_package=package)['status']=='passed'
    expected_alpha=[1,.4,1,.25,.75]
    Fn=importlib.import_module(package+'.core.material').FnMaterial
    for index,mat in enumerate(data.materials):
        readable=Fn(mat).get_texture()
        assert readable and readable.type=='IMAGE' and readable.image
        actual=readable.image.pixels[3]*mat.mmd_material.alpha
        assert abs(actual-expected_alpha[index])<1/255,(index,actual)
        assert max(abs(v-1) for v in mat.mmd_material.diffuse_color)<1e-6
        assert max(abs(v-.5) for v in mat.mmd_material.ambient_color)<1e-6
        assert max(abs(v) for v in mat.mmd_material.specular_color)<1e-6
    # Invoke the installed exporter's actual material writer, only in memory.
    exporter_module=importlib.import_module(package+'.core.pmx.exporter')
    writer=getattr(exporter_module,'__PmxExporter')()
    model=importlib.import_module(package+'.core.pmx').Model()
    setattr(writer,'_PmxExporter__model',model)
    for mat in data.materials:
        getattr(writer,'_PmxExporter__exportMaterial')(mat,1)
    assert len(model.materials)==5
    assert len(model.textures)==5
    assert all(0<=m.texture<len(model.textures) and Path(model.textures[m.texture].path).is_file() for m in model.materials)
    assert all(m.name==old.name for m,old in zip(model.materials,originals))
    victim=data.materials[0];path=Fn(victim).get_texture().image.filepath
    Fn(victim).remove_texture()
    broken=prep['validate_materials']([mesh.name],mmd_package=package)
    assert broken['status']=='failed'
    assert any(i['issue']=='exporter_cannot_read_base_texture' for i in broken['issues'])
    Fn(victim).create_texture(path)
    assert prep['validate_materials']([mesh.name],mmd_package=package)['status']=='passed'
    assert not list(Path(output_dir).rglob('*.pmx'))
    return dict(status='passed',surface_variants=5,opacity_modes=True,named_uv_checked=True,
                actual_exporter_material_writer='passed_in_memory',base_texture_indices=[m.texture for m in model.materials],
                fixed_colors=True,missing_exporter_texture_detected=True,no_pmx_files_written=True)
