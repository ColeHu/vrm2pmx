"""Prepare editable mmd_tools data in Blender; never write a PMX file."""
import hashlib
import importlib
import json
from pathlib import Path
import runpy
import uuid

ROOT = Path(__file__).resolve().parents[1]


def api(name):
    return runpy.run_path(str(ROOT / 'scripts' / name))


def write(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')


def source_fingerprint(armature_name):
    """Hash rig/skin/key/UV/slot state without writing a character-sized JSON dump."""
    import bpy
    data = api('rig_audit.py')['inspect_rig'](armature_name)
    material_api = api('auto_materials.py')
    def material_state(mat):
        if mat is None:return None
        if mat.node_tree and mat.node_tree.nodes.get('mmd_shader'):
            # Source preservation must not reject an existing native MMD shader
            # just because the optional baking parser accepts other surfaces.
            mm=mat.mmd_material
            fields={key:list(getattr(mm,key)) for key in ('diffuse_color','specular_color','ambient_color','edge_color')}
            fields.update({key:getattr(mm,key) for key in ('alpha','shininess','is_double_sided',
                'enabled_toon_edge','edge_weight','sphere_texture_type','toon_texture','is_shared_toon_texture')})
            def value(socket):
                v=getattr(socket,'default_value',None)
                if v is None or isinstance(v,(str,bool,float,int)):return v
                try:return list(v)
                except TypeError:return str(v)
            return dict(native_mmd=fields,nodes=[dict(name=n.name,type=n.bl_idname,
                image=material_api['_image_info'](n.image) if n.type=='TEX_IMAGE' and n.image else None,
                image_path=n.image.filepath if n.type=='TEX_IMAGE' and n.image else None,
                group=n.node_tree.name if n.type=='GROUP' and n.node_tree else None,
                inputs=[(s.name,value(s)) for s in n.inputs]) for n in mat.node_tree.nodes],
                links=[(l.from_node.name,l.from_socket.name,l.to_node.name,l.to_socket.name) for l in mat.node_tree.links])
        return material_api['inspect_material'](mat)
    for name, row in data['meshes'].items():
        obj = bpy.data.objects[name]
        row['polygons'] = [(list(p.vertices), p.material_index) for p in obj.data.polygons]
        row['uv'] = {layer.name: [list(item.uv) for item in layer.data] for layer in obj.data.uv_layers}
        row['slots'] = [slot.material.name if slot.material else None for slot in obj.material_slots]
        row['normals'] = [list(n.vector) for n in obj.data.corner_normals]
        row['material_settings'] = [material_state(slot.material)
                                    for slot in obj.material_slots]
    return hashlib.sha256(json.dumps(data, sort_keys=True, allow_nan=False).encode('utf-8')).hexdigest()


def validate_materials(mesh_names, *, options=None, mmd_package=None):
    """Read actual PMX material fields, base image references, UVs and assignments."""
    import bpy
    materials = api('auto_materials.py')
    colors = dict(materials['DEFAULTS'], **(options or {}))
    FnMaterial = importlib.import_module((mmd_package or materials['_mmd_package']()) + '.core.material').FnMaterial
    issues, checked, slots = [], {}, {}
    for name in mesh_names:
        obj = bpy.context.scene.objects[name]
        slots[name] = [s.material.name if s.material else None for s in obj.material_slots]
        for poly in obj.data.polygons:
            if poly.material_index >= len(obj.material_slots) or not obj.material_slots[poly.material_index].material:
                issues.append({'mesh': name, 'face': poly.index, 'issue': 'missing_face_material'})
        if obj.data.uv_layers.active is None and obj.data.polygons:
            issues.append({'mesh': name, 'issue': 'missing_active_uv'})
        for slot in obj.material_slots:
            mat = slot.material
            if not mat or mat.name in checked:
                continue
            mm = mat.mmd_material
            problems = []
            if not mat.get('pmx_prep_signature'):
                problems.append('not_a_prepared_material_or_outline_helper_remaining')
            for key, attr in [('diffuse','diffuse_color'), ('ambient','ambient_color'), ('specular','specular_color')]:
                if max(abs(a-b) for a,b in zip(getattr(mm,attr), colors[key])) > 1e-5:
                    problems.append(key)
            if mm.enabled_toon_edge or abs(mm.edge_weight) > 1e-6:
                problems.append('outline_enabled')
            if int(mm.sphere_texture_type) != 0:
                problems.append('sphere_enabled')
            toon = mat.node_tree.nodes.get('mmd_toon_tex') if mat.node_tree else None
            if mm.is_shared_toon_texture or mm.toon_texture or (toon and toon.image):
                problems.append('toon_assigned')
            texture = FnMaterial(mat).get_texture()
            image = getattr(texture, 'image', None)
            if getattr(texture,'type',None)!='IMAGE' or image is None:
                problems.append('exporter_cannot_read_base_texture')
            path = Path(bpy.path.abspath(image.filepath, library=image.library)) if image else None
            if not path or not path.is_file():
                problems.append('missing_base_texture')
            expected = json.loads(mat.get('pmx_prep_expected', '{}'))
            if not expected:
                problems.append('missing_expected_material_settings')
            else:
                if abs(mm.alpha-expected['alpha']) > 1e-5:
                    problems.append('alpha')
                if mm.is_double_sided != expected['double_sided']:
                    problems.append('double_sided')
                if mat.use_backface_culling == expected['double_sided']:
                    problems.append('backface_culling')
                if path and path.resolve() != Path(expected['base']).resolve():
                    problems.append('base_texture_reference')
            checked[mat.name] = dict(alpha=mm.alpha, double_sided=mm.is_double_sided,
                                     exporter_readable_base=bool(image and getattr(texture,'type',None)=='IMAGE'),
                                     base=str(path) if path else None, issues=problems)
            issues.extend({'material':mat.name, 'issue':p} for p in problems)
    if not checked:
        issues.append({'issue':'no_materials'})
    return dict(status='failed' if issues else 'passed', scope='blender_material_data',
                materials=checked, slots=slots, issues=issues, unverified=['pmx_roundtrip', 'renderer_appearance'])


def save_work_copy(filepath):
    """Write the work scene to a NEW .blend without project-wide save handlers."""
    import bpy
    target = Path(filepath).resolve()
    if target.suffix.lower() != '.blend' or target.exists():
        raise ValueError('Choose a new .blend path')
    target.parent.mkdir(parents=True, exist_ok=True)
    # VRM save_pre handlers may migrate every source object even with copy=True.
    # A scene library is an openable .blend, containing its dependency closure.
    bpy.data.libraries.write(str(target), {bpy.context.scene}, path_remap='ABSOLUTE', compress=True)
    if not target.is_file():
        raise RuntimeError('Blender did not finish writing the work scene')
    return str(target)


def prepare_model(armature_name, output_dir, *, mapping=None, version=None,
                  rig_options=None, material_options=None, mmd_package=None,
                  texture_python=None, parts='both', save_blend=False, texture_policy='original_file'):
    """Copy source, prepare rig and optional base materials, validate, hand off.

    A failed rig backtest or material conversion leaves the isolated work scene
    for repair and returns needs_repair. The source scene is restored on failure.
    Success selects the work armature in the output scene for manual editing.
    """
    import bpy
    if parts not in ('both', 'rig'):
        raise ValueError('parts must be both or rig; use convert_materials for existing work meshes')
    if texture_policy not in ('original_file','bake'):raise ValueError('Unknown texture policy')
    rig = api('auto_rig.py')
    material = api('auto_materials.py')
    options = dict(rig_options or {}, keep_failed_for_review=True)
    if mmd_package:
        options['mmd_package'] = mmd_package
    package = rig['_package'](options.get('mmd_package'))
    plan = rig['plan_rig'](armature_name, mapping=mapping, version=version, options=options)
    if parts == 'both':
        if texture_policy=='original_file':
            api('original_materials.py')['plan_original_materials'](plan['meshes'],options=material_options,mmd_package=package)
        else:
            material['plan_materials'](plan['meshes'], material_options)
    before = source_fingerprint(armature_name) if parts == 'both' else None
    folder = Path(output_dir).resolve() / ('prep-' + uuid.uuid4().hex[:12])
    folder.mkdir(parents=True, exist_ok=False)
    old_scene = bpy.context.window.scene
    report = dict(status='needs_repair', output_dir=str(folder), source_armature=armature_name,
                  parts=parts, texture_policy=texture_policy, pmx_exported=False, unverified=['pmx_roundtrip','task_specific_motion_deformation',
                      'independent_all_key_audit','full_vrm_expression_conversion','physics','renderer_appearance'])
    try:
        rig_report = rig['convert_rig'](armature_name, folder, mapping=mapping, version=version, options=options)
        report['rig'] = rig_report
        report.update(scene=rig_report['scene'], armature=rig_report['armature'], meshes=rig_report['meshes'])
        bpy.context.window.scene = bpy.data.scenes[report['scene']]
        backtest = rig['backtest_rig'](report['armature'], folder)
        report['rig_backtest'] = backtest
        if backtest['status'] != 'passed':
            report['repair_queue'] = backtest['repair_queue']
            return report
        if parts == 'both':
            report['materials'] = material['convert_materials'](
                report['meshes'], folder / 'materials', options=material_options,
                work_copy=True, mmd_package=package, texture_python=texture_python,texture_policy=texture_policy)
            report['material_validation'] = validate_materials(report['meshes'], options=material_options, mmd_package=package)
            if report['material_validation']['status'] != 'passed':
                report['repair_queue'] = report['material_validation']['issues']
                return report
        bpy.context.window.scene = old_scene
        report['source_unchanged'] = (source_fingerprint(armature_name) == before) if before else rig_report['source_unchanged']
        if not report['source_unchanged']:
            raise RuntimeError('Source invariants changed; inspect before continuing')
        bpy.context.window.scene = bpy.data.scenes[report['scene']]
        bpy.ops.object.select_all(action='DESELECT')
        arm = bpy.data.objects[report['armature']]
        arm.select_set(True)
        bpy.context.view_layer.objects.active = arm
        report['status'] = 'prepared_for_manual_edit' if parts=='both' else 'rig_prepared_materials_pending'
        report['material_stage']='validated' if parts=='both' else 'not_requested'
        report['ready_scopes']=['rig','base_materials'] if parts=='both' else ['rig']
        if parts=='rig':
            report['unverified'].append('base_materials_and_exporter_texture_slots')
            report['handoff_warning']='Rig only: source shader nodes were retained; material conversion is still required before textured PMX export'
        if save_blend:
            report['blend_copy'] = save_work_copy(folder / 'prepared.blend')
        return report
    except Exception as exc:
        report['status'] = 'needs_repair' if report.get('scene') else 'failed'
        report['error'] = str(exc)
        return report
    finally:
        if report['status'] not in ('prepared_for_manual_edit','rig_prepared_materials_pending'):
            bpy.context.window.scene = old_scene
