"""Blender automatic VRM material conversion. Agent supplies targets and verifies.
No model names, fixed paths, rendering, geometry edits or PMX export.
"""
import hashlib
import importlib
import json
import math
from pathlib import Path
import runpy
import shutil
import subprocess
import sys
import uuid

DEFAULTS = dict(diffuse=[1,1,1], specular=[0,0,0], ambient=[.5,.5,.5])


def _scripts():
    return Path(__file__).resolve().parent


def _image_info(image):
    import bpy
    if image is None:
        return None
    if image.source not in ("FILE","GENERATED") or image.type not in ("IMAGE","UV_TEST"):
        raise ValueError("Unsupported image source/type: " + image.name)
    if image.colorspace_settings.name not in ('sRGB','Non-Color','Linear','Linear Rec.709'):
        raise ValueError('Unsupported image colorspace: '+image.colorspace_settings.name)
    source = Path(bpy.path.abspath(image.filepath,library=image.library)) if image.filepath else None
    if image.is_dirty or image.source == "GENERATED":
        import numpy as np
        pixels = np.empty(len(image.pixels), dtype=np.float32)
        image.pixels.foreach_get(pixels)
        digest = hashlib.sha256(pixels.tobytes()).hexdigest()
    elif image.packed_file:
        digest = hashlib.sha256(image.packed_file.data).hexdigest()
    elif source and source.is_file():
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
    else:
        raise ValueError("Image has no readable source: " + image.name)
    return dict(name=image.name, width=int(image.size[0]),height=int(image.size[1]),
                colorspace=image.colorspace_settings.name,dirty=bool(image.is_dirty),
                source=image.source,content_sha256=digest)


def _texture(info, cache):
    image = info.index.source
    if image and image.name not in cache:
        cache[image.name] = _image_info(image)
    descriptor = cache[image.name] if image else None
    transform = info.extensions.khr_texture_transform
    sampler = info.index.sampler
    def wrap(value):
        modes={'REPEAT':10497,'CLAMP_TO_EDGE':33071,'MIRRORED_REPEAT':33648}
        return modes[value] if value in modes else int(value)
    return dict(image=image.name if image else None, descriptor=descriptor,
                encoding="linear" if image and image.colorspace_settings.name in ("Non-Color","Linear","Linear Rec.709") else "srgb",
                offset=list(transform.offset),scale=list(transform.scale),
                rotation=float(getattr(transform,"rotation",0)),wrap=[wrap(sampler.wrap_s),wrap(sampler.wrap_t)])


def _empty_texture():
    return dict(image=None,descriptor=None,encoding="srgb",offset=[0,0],scale=[1,1],rotation=0,wrap=[10497,10497])


def inspect_material(material, options=None, cache=None):
    options = dict(DEFAULTS,**(options or {}))
    cache = cache if cache is not None else {}
    mat = material
    if mat.get("pmx_prep_signature"):
        return dict(kind="already_converted",source=mat.name,signature=mat["pmx_prep_signature"])
    row = dict(source=mat.name, warnings=[], textures={k:_empty_texture() for k in ("base",)})
    ext = getattr(mat,"vrm_addon_extension",None)
    t = ext.mtoon1 if ext else None
    if t and t.enabled:
        if t.is_outline_material:
            return dict(kind="skip_outline_helper",source=mat.name)
        p, v = t.pbr_metallic_roughness, t.extensions.vrmc_materials_mtoon
        row.update(kind="mtoon",base_factor=list(p.base_color_factor),alpha_mode=t.alpha_mode,
                   alpha_cutoff=t.alpha_cutoff,double_sided=t.double_sided,receive_shadow=True,
                   source_extra=dict(emissive_factor=list(t.emissive_factor),
                       normal_image=t.normal_texture.index.source.name if t.normal_texture.index.source else None,
                       rim_factor=list(v.parametric_rim_color_factor),rim_lighting_mix=v.rim_lighting_mix_factor,
                       transparent_z_write=v.transparent_with_z_write,
                       uv_scroll=[v.uv_animation_scroll_x_speed_factor,v.uv_animation_scroll_y_speed_factor],
                       uv_rotation=v.uv_animation_rotation_speed_factor,
                       omitted=["Shade", "MatCap", "Toon", "outline"]))
        row["textures"]["base"] = _texture(p.base_color_texture,cache)
        if any(row["source_extra"]["emissive_factor"]):
            row["warnings"].append("Emission is outside the basic PMX material profile")
        if row["source_extra"]["normal_image"]:
            row["warnings"].append("Normal map recorded; PMX standard material has no normal texture slot")
        if any(row["source_extra"]["rim_factor"]) or row["source_extra"]["rim_lighting_mix"]:
            row["warnings"].append("Parametric Rim/lighting modulation not equivalent to additive Sphere")
        if row["source_extra"]["transparent_z_write"]:
            row["warnings"].append("Transparent Z-write requires target renderer configuration")
        if any(row["source_extra"]["uv_scroll"]) or row["source_extra"]["uv_rotation"]:
            row["warnings"].append("Animated UV recorded; static PMX texture conversion freezes texture transform")
        if v.shading_shift_texture.index.source:
            row["warnings"].append("Spatial shading-shift texture recorded as unsupported; not transferred")
    else:
        # Trace effective output only; unconnected Principled/image nodes are ignored.
        row.update(kind="principled",base_factor=list(mat.diffuse_color),alpha_mode="OPAQUE",
                   alpha_cutoff=.5,double_sided=not mat.use_backface_culling,
                   receive_shadow=True)
        if mat.use_nodes:
            surface_api=runpy.run_path(str(_scripts()/'blender_material_source.py'))
            row.update(surface_api['inspect_surface'](mat,_image_info,_empty_texture))
        elif row['base_factor'][3]<1:
            row['alpha_mode']='BLEND'
    row["key"] = hashlib.sha256(mat.name.encode("utf-8")).hexdigest()[:16]
    payload = json.dumps(row,sort_keys=True,ensure_ascii=False,allow_nan=False)
    row["fingerprint"] = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return row


def plan_materials(mesh_names, options=None):
    import bpy
    names=list(mesh_names)
    if not names or len(set(names))!=len(names):
        raise ValueError("Explicit unique mesh names required")
    options=dict(DEFAULTS,**(options or {}))
    unknown = set(options) - set(DEFAULTS)
    if unknown:
        raise ValueError("Unsupported basic-material options: " + str(sorted(unknown)))
    for key in DEFAULTS:
        if len(options[key]) != 3 or not all(math.isfinite(x) and 0 <= x <= 1 for x in options[key]):
            raise ValueError("Expected normalized RGB: " + key)
    rows, slots, cache = {}, {}, {}
    for name in names:
        obj=bpy.context.scene.objects[name]
        if obj.type!="MESH":
            raise ValueError("Target is not mesh: "+name)
        slots[name]=[]
        for slot in obj.material_slots:
            mat=slot.material
            slots[name].append(mat.name if mat else None)
            if mat and mat.name not in rows:
                rows[mat.name]=inspect_material(mat,options,cache)
        if obj.data.polygons and obj.data.uv_layers.active is None:
            raise ValueError('Work mesh needs an active UV map: '+name)
        for source in slots[name]:
            for tex in rows.get(source,{}).get('textures',{}).values():
                named_uv=tex.get('uv_map')
                if named_uv and (obj.data.uv_layers.active is None or named_uv!=obj.data.uv_layers.active.name):
                    raise ValueError('Effective named UV differs from PMX export active UV: '+name+'/'+named_uv)
        if obj.data.uv_layers.active:
            outside = any(any(x < 0 or x > 1 for x in item.uv) for item in obj.data.uv_layers.active.data)
            if outside:
                for source in slots[name]:
                    row = rows.get(source, {})
                    for tex in row.get('textures', {}).values():
                        if tex.get('image') and (tex.get('offset') != [0,0] or tex.get('scale') != [1,1]
                                or abs(tex.get('rotation',0)) > 1e-8 or tex.get('wrap') != [10497,10497]):
                            raise ValueError('Nontrivial texture transform/sampler with UV outside the unit tile requires an adapter: '+name)
    return dict(schema_version=1,options=options,slots=slots,materials=list(rows.values()),
                profile="basic PMX authoring; base texture and fixed colors; no effects",
                assumptions=["No PMX export or roundtrip is performed",
                             "Outline, Sphere/MatCap, Toon and PBR response are omitted"])



def _extract_image(image, path):
    import bpy
    path=Path(path)
    if image.is_dirty or image.source=='GENERATED' or image.is_float:
        # Image.copy()/save can reload or color-transform buffers. Stage actual
        # current pixels directly, preserving float values and alpha instead.
        import numpy as np
        w,h=image.size
        values=np.empty(len(image.pixels),dtype=np.float32)
        image.pixels.foreach_get(values)
        if values.size!=w*h*4:
            raise ValueError('Expected four-channel current image buffer')
        path=path.with_suffix('.npy')
        np.save(path,values.reshape(h,w,4)[::-1].copy())
    elif image.packed_file:
        path.write_bytes(image.packed_file.data)
    elif image.source=="FILE" and image.filepath:
        source=Path(bpy.path.abspath(image.filepath,library=image.library))
        if not source.is_file():
            raise ValueError("Missing source image: "+image.name)
        shutil.copyfile(source,path)
    else:
        raise ValueError('Image has no extractable source')
    return str(path.resolve())


def _mmd_package():
    import bpy
    candidates=[name.rsplit(".core.material",1)[0] for name in sys.modules
                if name.endswith(".core.material") and "mmd_tools" in name]
    active=[n for n in candidates if n.startswith("bl_ext.") and n in bpy.context.preferences.addons]
    if len(active)==1:
        return active[0]
    if len(candidates)==1:
        return candidates[0]
    raise ValueError("Supply actual mmd_package; multiple or missing loaded implementations")


def execute_plan(plan, output_dir, *, work_copy=False, mmd_package=None, texture_python=None):
    import bpy
    import numpy as np
    if not work_copy or bpy.context.mode!="OBJECT":
        raise ValueError("Explicit work copy in Object Mode required")
    fresh=plan_materials(plan["slots"].keys(),plan["options"])
    if fresh!=plan:
        raise ValueError("Source materials/settings/slots changed; regenerate plan")
    # Texture staging paths belong to this execution, not the caller's read-only plan.
    plan=json.loads(json.dumps(plan))
    for name in plan["slots"]:
        obj=bpy.context.scene.objects[name]
        if obj.data.users!=1:
            raise ValueError("Mesh data must be single-user work copy: "+name)
        if any(slot.link!="DATA" for slot in obj.material_slots):
            raise ValueError("Object-linked material slots require an explicit adapter")
    rows=[r for r in plan["materials"] if r["kind"] not in ("already_converted","skip_outline_helper")]
    if not rows:
        return dict(status="already_converted",converted=0,unverified=["pmx_roundtrip"])
    source_names={r['source'] for r in rows}
    roots={}
    for name in plan['slots']:
        obj=bpy.context.scene.objects[name]
        while obj:
            if getattr(obj,'mmd_type',None)=='ROOT':
                roots[obj.name]=obj
                break
            obj=obj.parent
    morph_links=[]
    for root in roots.values():
        for morph in root.mmd_root.material_morphs:
            for offset in morph.data:
                old=offset.material_data
                if old and old.name in source_names:
                    affected={o.name for o in root.children_recursive if o.type=='MESH' and old in o.data.materials[:]}
                    if not affected.issubset(plan['slots']):
                        raise ValueError('Include all root meshes using a material referenced by a Material Morph')
                    morph_links.append((offset,old,offset.material_id))
    target=Path(output_dir).resolve()/("run-"+uuid.uuid4().hex[:12])
    target.mkdir(parents=True,exist_ok=False)
    raw=target/"source_textures";raw.mkdir()
    extracted={}
    for row in rows:
        for tex in row["textures"].values():
            image_name=tex["image"]
            if image_name:
                image=bpy.data.images[image_name]
                token=hashlib.sha256(image_name.encode("utf-8")).hexdigest()[:16]
                if image_name not in extracted:
                    extracted[image_name]=_extract_image(image,raw/(token+".image"))
                tex["path"]=extracted[image_name]
    request=dict(output_dir=str(target/"textures"),materials=rows)
    request_path=target/"texture-request.json"
    result_path=target/"texture-results.json"
    request_path.write_text(json.dumps(request,ensure_ascii=False,indent=2),encoding="utf-8")
    python=texture_python or shutil.which("python")
    if not python:
        raise ValueError("Supply host texture_python with NumPy and Pillow")
    cmd=[python,"-B","-X","utf8",str(_scripts()/"material_auto_pixels.py"),str(request_path),str(result_path)]
    import os
    environment=dict(os.environ)
    environment.pop("PYTHONHOME",None);environment.pop("PYTHONPATH",None)
    proc=subprocess.run(cmd,capture_output=True,text=True,encoding="utf-8",env=environment,
                        creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0))
    if proc.returncode:
        raise RuntimeError("Texture worker failed: "+proc.stderr[-4000:])
    results=json.loads(result_path.read_text(encoding="utf-8"))
    helper=runpy.run_path(str(_scripts()/"material_tools.py"))
    package=mmd_package or _mmd_package()
    created={}
    previous_slots={name:[s.material for s in bpy.context.scene.objects[name].material_slots] for name in plan["slots"]}
    original_images={image.as_pointer() for image in bpy.data.images}
    report=dict(status="converted_pending_validation",converted=len(rows),output_dir=str(target),
                assumptions=plan["assumptions"],materials=[],unverified=["pmx_roundtrip"])
    report['skipped_materials']=[r for r in plan['materials'] if r['kind'] in ('already_converted','skip_outline_helper')]
    report['source_slots']=plan['slots']
    report['warnings'] = ['Original MToon outline helpers are not converted; inspect any remaining helper slots']
    try:
        for row in rows:
            result=results[row["key"]]
            settings=dict(alpha=result["material_alpha"],double_sided=row["double_sided"],
                          cast_shadow=True,receive_shadow=row["receive_shadow"],ground_shadow=True,
                          outline_enabled=False,outline_color=[0,0,0,0],
                          outline_weight=0,sphere_mode=0,
                          diffuse=plan["options"]["diffuse"],specular=plan["options"]["specular"],
                          ambient=plan["options"]["ambient"])
            name="pmx_"+row["key"]
            if bpy.data.materials.get(name):
                name += "_"+uuid.uuid4().hex[:8]
            settings["name_j"]=row['source']
            mat=helper["create_material"](name,settings,{k:result[k] for k in ("base","sphere")},package)
            mat["pmx_prep_signature"]=row["fingerprint"]
            mat["pmx_prep_source_settings"]=json.dumps(row,ensure_ascii=False)
            mat["pmx_prep_alpha_mode"]=row["alpha_mode"]
            mat['pmx_prep_expected'] = json.dumps(dict(alpha=settings['alpha'],double_sided=settings['double_sided'],base=result['base']))
            if hasattr(mat,"surface_render_method"):
                values={v.identifier for v in mat.bl_rna.properties["surface_render_method"].enum_items}
                mat.surface_render_method="BLENDED" if row["alpha_mode"]=="BLEND" and "BLENDED" in values else "DITHERED"
            created[row["source"]]=mat
            report["materials"].append(dict(source=row["source"],target=mat.name,
                    textures={k:result[k] for k in ("base","sphere")},settings=settings,
                    warnings=result["warnings"],fingerprint=row["fingerprint"]))
        for name, source_slots in plan["slots"].items():
            obj=bpy.context.scene.objects[name]
            for index,source in enumerate(source_slots):
                if source in created:
                    obj.data.materials[index]=created[source]
        for row in report["materials"]:
            mat=created[row["source"]]
            assert abs(mat.mmd_material.alpha-row["settings"]["alpha"])<1e-5
            assert int(mat.mmd_material.sphere_texture_type)==row["settings"]["sphere_mode"]
            assert mat.mmd_material.is_double_sided==row["settings"]["double_sided"]
        for offset,old,_ in morph_links:
            offset.material=created[old.name].name
        report['material_morph_links_updated']=len(morph_links)
        if morph_links:
            report['unverified'].append('material_morph_effects')
        report['source_to_target']={name:mat.name for name,mat in created.items()}
        report['exporter_readable_base_textures']=len(created)
        report['stage']='materials_prepared'
        report_path=target/"material-conversion-report.json"
        report_path.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
        report["report_path"]=str(report_path)
        return report
    except Exception:
        for offset,old,old_id in morph_links:
            offset.material_data=old
            offset.material_id=old_id
        for name,slots in previous_slots.items():
            obj=bpy.context.scene.objects[name]
            for index,mat in enumerate(slots):
                obj.data.materials[index]=mat
        for mat in created.values():
            bpy.data.materials.remove(mat)
        for image in list(bpy.data.images):
            if image.as_pointer() not in original_images and image.users==0:
                bpy.data.images.remove(image)
        raise


def convert_materials(mesh_names, output_dir, *, options=None, work_copy=False,
                      mmd_package=None, texture_python=None, texture_policy='original_file'):
    if texture_policy=='original_file':
        return runpy.run_path(str(_scripts()/'original_materials.py'))['bind_original_materials'](
            mesh_names,output_dir,options=options,work_copy=work_copy,mmd_package=mmd_package)
    if texture_policy!='bake':raise ValueError('texture_policy must be original_file or bake')
    plan=plan_materials(mesh_names,options)
    return execute_plan(plan,output_dir,work_copy=work_copy,mmd_package=mmd_package,texture_python=texture_python)
