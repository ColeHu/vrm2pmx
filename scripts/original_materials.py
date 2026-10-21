"""Bind existing disk images directly to formal MMD base texture slots."""
import hashlib
import importlib
import json
from pathlib import Path
import runpy
import uuid

ROOT=Path(__file__).resolve().parent


def plan_original_materials(mesh_names, *, options=None, mmd_package=None):
    import bpy
    api=runpy.run_path(str(ROOT/'auto_materials.py'))
    package=mmd_package or api['_mmd_package']()
    Fn=importlib.import_module(package+'.core.material').FnMaterial
    colors=dict(api['DEFAULTS'],**(options or {}))
    if set(colors)!=set(api['DEFAULTS']):raise ValueError('Unknown material color option')
    import math
    if any(len(v)!=3 or not all(math.isfinite(x) and 0<=x<=1 for x in v) for v in colors.values()):
        raise ValueError('Expected normalized RGB material colors')
    names=list(mesh_names)
    if not names or len(set(names))!=len(names):raise ValueError('Choose unique explicit mesh names')
    rows={};slots={}
    for name in names:
        obj=bpy.context.scene.objects[name]
        if obj.type!='MESH':raise ValueError('Target must be mesh: '+name)
        if obj.data.polygons and not obj.data.uv_layers.active:raise ValueError('Missing active UV: '+name)
        slots[name]=[s.material.name if s.material else None for s in obj.material_slots]
        for mat in obj.data.materials:
            if not mat or mat.name in rows:continue
            mm=mat.mmd_material;ext=getattr(mat,'vrm_addon_extension',None)
            t=ext.mtoon1 if ext else None
            warnings=[];uv_name=None
            if t and t.enabled:
                if t.is_outline_material:raise ValueError('Resolve outline helper material: '+mat.name)
                image=t.pbr_metallic_roughness.base_color_texture.index.source
                alpha=t.pbr_metallic_roughness.base_color_factor[3] if t.alpha_mode=='BLEND' else 1
                double=t.double_sided
                if t.alpha_mode=='MASK':warnings.append('Original image alpha retained; cutoff is not baked')
            else:
                image=getattr(Fn(mat).get_texture(),'image',None)
                if mat.get('pmx_prep_source_settings') and mat.get('pmx_prep_source_policy')!='original_file_reference':
                    old=json.loads(mat['pmx_prep_source_settings'])
                    source_name=old.get('textures',{}).get('base',{}).get('image')
                    if source_name:
                        image=bpy.data.images.get(source_name)
                if image is not None:
                    alpha=mm.alpha;double=mm.is_double_sided
                else:
                    settings=api['inspect_material'](mat,options=colors)
                    base=settings.get('textures',{}).get('base',{})
                    image=bpy.data.images.get(base.get('image',''))
                    uv_name=base.get('uv_map')
                    alpha=settings.get('base_factor',[1,1,1,1])[3] if settings.get('alpha_mode')=='BLEND' else 1
                    double=settings.get('double_sided',False)
                    if settings.get('textures',{}).get('alpha',{}).get('image'):
                        warnings.append('Separate opacity texture is not merged under original-file policy')
            if image is None or image.source!='FILE' or not image.filepath:
                raise ValueError('Original texture file unavailable for '+mat.name+'; select texture_policy=bake explicitly for generated/solid materials')
            path=Path(bpy.path.abspath(image.filepath,library=image.library)).resolve()
            if not path.is_file():raise ValueError('Original texture file missing: '+str(path))
            if image.is_dirty:warnings.append('Original disk file selected; unsaved image edits are not exported')
            rows[mat.name]=dict(source=mat.name,image=image.name,path=str(path),uv_map=uv_name,
                sha256=hashlib.sha256(path.read_bytes()).hexdigest(),alpha=alpha,double_sided=double,
                warnings=warnings,source_policy='original_file_reference')
        for source in slots[name]:
            row=rows.get(source,{})
            if row.get('uv_map') and row['uv_map']!=obj.data.uv_layers.active.name:
                raise ValueError('Named UV differs from active PMX UV: '+name)
    if not rows:raise ValueError('No materials found')
    return dict(policy='original_file',package=package,options=colors,slots=slots,materials=list(rows.values()))


def bind_original_materials(mesh_names, output_dir, *, options=None, mmd_package=None, work_copy=False):
    import bpy
    if not work_copy or bpy.context.mode!='OBJECT':raise ValueError('Explicit work copy in Object Mode required')
    plan=plan_original_materials(mesh_names,options=options,mmd_package=mmd_package)
    objects=[bpy.context.scene.objects[n] for n in plan['slots']]
    if any(o.data.users!=1 or any(s.link!='DATA' for s in o.material_slots) for o in objects):
        raise ValueError('Single-user work meshes with DATA material slots required')
    prep=runpy.run_path(str(ROOT/'prepare_model.py'))
    if all(bpy.data.materials[r['source']].get('pmx_prep_source_policy')=='original_file_reference' for r in plan['materials']):
        validation=prep['validate_materials']([o.name for o in objects],options=options,mmd_package=plan['package'])
        if validation['status']=='passed':return dict(status='already_converted',converted=0,texture_policy='original_file',validation=validation)
    # Preserve Material Morph pointers and refuse partial-root replacement.
    roots={}
    for obj in objects:
        parent=obj
        while parent:
            if getattr(parent,'mmd_type',None)=='ROOT':roots[parent.name]=parent;break
            parent=parent.parent
    sources={r['source'] for r in plan['materials']};morphs=[]
    for root in roots.values():
        for morph in root.mmd_root.material_morphs:
            for offset in morph.data:
                old=offset.material_data
                if old and old.name in sources:
                    affected={o.name for o in root.children_recursive if o.type=='MESH' and old in o.data.materials[:]}
                    if not affected.issubset(plan['slots']):raise ValueError('Include all meshes affected by Material Morph')
                    morphs.append((offset,old,offset.material_id))
    output=Path(output_dir)/('original-'+uuid.uuid4().hex[:12]);output.mkdir(parents=True,exist_ok=False)
    Fn=importlib.import_module(plan['package']+'.core.material').FnMaterial
    old_slots={o.name:list(o.data.materials) for o in objects};created={};images={};rows=[]
    try:
        for row in plan['materials']:
            old=bpy.data.materials[row['source']];mat=old.copy();mat.name=old.name+'_PMX'
            created[old.name]=mat;mm=mat.mmd_material
            ext=getattr(mat,'vrm_addon_extension',None)
            if ext and ext.mtoon1.enabled:ext.mtoon1.enabled=False
            mm.name_j=old.mmd_material.name_j or old.name
            mm.diffuse_color=plan['options']['diffuse'];mm.specular_color=plan['options']['specular'];mm.ambient_color=plan['options']['ambient']
            mm.alpha=row['alpha'];mm.is_double_sided=row['double_sided']
            mm.enabled_toon_edge=False;mm.edge_weight=0;mm.sphere_texture_type='0';mm.is_shared_toon_texture=False;mm.toon_texture=''
            fn=Fn(mat);fn.remove_sphere_texture();fn.remove_toon_texture();fn.create_texture(row['path'])
            # Independent image blocks make the export path absolute without
            # changing shared source images or writing any source texture file.
            if row['path'] not in images:
                image=bpy.data.images.load(row['path'],check_existing=False);image.filepath=row['path'];images[row['path']]=image
            mat.node_tree.nodes['mmd_base_tex'].image=images[row['path']]
            readable=fn.get_texture()
            if not readable or readable.type!='IMAGE' or readable.image!=images[row['path']]:raise RuntimeError('Exporter cannot read original image')
            mat.use_backface_culling=not mm.is_double_sided
            mat['pmx_prep_signature']=hashlib.sha256(json.dumps(row,sort_keys=True).encode()).hexdigest()
            mat['pmx_prep_source_policy']='original_file_reference'
            mat['pmx_prep_expected']=json.dumps(dict(alpha=mm.alpha,double_sided=mm.is_double_sided,base=row['path']))
            rows.append(dict(source=old.name,target=mat.name,textures={'base':row['path'],'sphere':None},warnings=row['warnings']))
        for obj in objects:
            for i,old in enumerate(old_slots[obj.name]):
                if old and old.name in created:obj.data.materials[i]=created[old.name]
        for offset,old,_ in morphs:offset.material=created[old.name].name
        validation=prep['validate_materials']([o.name for o in objects],options=options,mmd_package=plan['package'])
        if validation['status']!='passed':raise RuntimeError(str(validation['issues']))
        report=dict(status='converted_pending_validation',stage='materials_prepared',texture_policy='original_file',
                    materials=rows,validation=validation,source_to_target={n:m.name for n,m in created.items()},
                    unverified=['pmx_roundtrip','renderer_appearance','shader_factors_and_separate_opacity_not_baked'])
        path=output/'material-conversion-report.json';path.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        report['report_path']=str(path);return report
    except Exception:
        for offset,old,old_id in morphs:offset.material_data=old;offset.material_id=old_id
        for obj in objects:
            for i,mat in enumerate(old_slots[obj.name]):obj.data.materials[i]=mat
        for mat in created.values():bpy.data.materials.remove(mat)
        for image in images.values():
            if not image.users:bpy.data.images.remove(image)
        raise
