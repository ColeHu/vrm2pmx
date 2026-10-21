"""Deterministic VRM humanoid -> MMD body rig. Run inside Blender.

plan_rig is read-only. convert_rig creates an isolated scene and single-user
copies, applies/bakes the standard A-pose, builds controls and validates them.
Materials and physics are intentionally outside this bone-only entry point.
"""
import importlib
import json
import math
import re
from pathlib import Path
import runpy
import sys
import unicodedata

ROOT = Path(__file__).resolve().parents[1]


def _control_roles():
    return ['全ての親', 'センター', 'グルーブ', '腰', '腰キャンセル左', '腰キャンセル右', '両目'] + [
        side+role for side in ('左', '右') for role in ('足IK親', '足ＩＫ', 'つま先', 'つま先ＩＫ')]


def _control_role(name):
    """Recognize explicit MMD role aliases, not arbitrary accessory names."""
    name = unicodedata.normalize('NFKC', name or '')
    name = re.sub(r'\.\d{3,}$', '', name)
    suffix = re.search(r'[._]([LR])$', name, re.IGNORECASE)
    if suffix:
        name = ('左' if suffix.group(1).upper() == 'L' else '右') + name[:suffix.start()]
    if name in ('左腰キャンセル','右腰キャンセル'):
        name='腰キャンセル'+name[0]
    return next((role for role in _control_roles() if unicodedata.normalize('NFKC', role) == name), None)


def _reconcile_controls(arm, meshes, humanoid_names):
    found = {}
    for pose in arm.pose.bones:
        if pose.name in humanoid_names or pose.is_mmd_shadow_bone:
            continue
        role = _control_role(pose.mmd_bone.name_j) or _control_role(pose.name)
        if role:
            found.setdefault(role, []).append(pose.name)
    candidates = {name for names in found.values() for name in names}
    for obj in meshes:
        groups = {g.index: g.name for g in obj.vertex_groups if g.name in candidates}
        for vertex in obj.data.vertices:
            for weight in vertex.groups:
                if weight.group in groups and weight.weight > 0:
                    raise ValueError('Existing control has skin weights; provide an adapter: '+groups[weight.group])
    reused, duplicates = {}, {}
    for role,names in found.items():
        chosen = role if role in names else sorted(names)[0]
        reused[role] = chosen
        duplicates.update({name: role for name in names if name != chosen})
    return reused, duplicates


def _api(file):
    return runpy.run_path(str(ROOT / 'scripts' / file))


def humanoid_targets(version):
    """Semantic names, independent of any model's internal names."""
    result = dict(hips='下半身', spine='上半身', chest='上半身2', neck='首', head='頭')
    for side, prefix in [('left', '左'), ('right', '右')]:
        for part, name in [('Eye', '目'), ('Shoulder', '肩'), ('UpperArm', '腕'),
                           ('LowerArm', 'ひじ'), ('Hand', '手首'), ('UpperLeg', '足'),
                           ('LowerLeg', 'ひざ'), ('Foot', '足首'), ('Toes', '足先EX')]:
            result[side + part] = prefix + name
        thumbs = ('Metacarpal', 'Proximal', 'Distal') if version == '1.0' else ('Proximal', 'Intermediate', 'Distal')
        for index, part in enumerate(thumbs):
            result[side + 'Thumb' + part] = prefix + '親指' + '０１２'[index]
        for finger, name in [('Index', '人指'), ('Middle', '中指'), ('Ring', '薬指'), ('Little', '小指')]:
            for index, part in enumerate(('Proximal', 'Intermediate', 'Distal')):
                result[side + finger + part] = prefix + name + '１２３'[index]
    return result


def _package(explicit=None):
    if explicit:
        importlib.import_module(explicit)
        return explicit
    found = [name for name in sys.modules if name.endswith('mmd_tools') and
             hasattr(sys.modules[name], '__path__') and '.extern_tools.' not in name]
    if len(found) != 1:
        raise ValueError('Specify the active mmd_tools package: ' + str(found))
    return found[0]


def _bound(arm):
    import bpy
    return [o for o in bpy.context.scene.objects if o.type == 'MESH'
            and any(m.type == 'ARMATURE' and m.object == arm for m in o.modifiers)]


def _source_state(arm, meshes):
    return {'bones': {b.name: {'parent': b.parent.name if b.parent else None,
                              'matrix': [list(row) for row in b.matrix_local],
                              'pose_basis': [list(row) for row in arm.pose.bones[b.name].matrix_basis],
                              'tail': list(b.tail_local)} for b in arm.data.bones},
            'meshes': {o.name: {'vertices': len(o.data.vertices), 'groups': [g.name for g in o.vertex_groups],
                               'matrix': [list(row) for row in o.matrix_world],
                               'modifiers': [{'name':m.name,'type':m.type,'show_viewport':bool(m.show_viewport),
                                              'show_render':bool(m.show_render)} for m in o.modifiers]} for o in meshes}}


def plan_rig(armature_name, *, mapping=None, version=None, options=None):
    import bpy
    from mathutils import Matrix
    arm = bpy.context.scene.objects.get(armature_name)
    if arm is None or arm.type != 'ARMATURE' or bpy.context.mode != 'OBJECT':
        raise ValueError('Choose a scene armature in Object Mode')
    if arm.get('auto_rig_version'):
        raise ValueError('This rig is already converted; use validate_rig instead')
    audit = _api('rig_audit.py')
    human = audit['_humanoid'](arm)
    mapping = dict(mapping if mapping is not None else human['mapping'])
    version = version or human['version']
    if version not in ('0.0', '1.0'):
        raise ValueError('Supply VRM version 0.0 or 1.0')
    options = dict(options or {})
    if options.get('internal_naming','suffix') not in ('suffix','legacy'):
        raise ValueError('internal_naming must be suffix or legacy')
    skip_eyes = options.get('eye_mode', 'bones') == 'morph_only'
    if options.get('eye_mode', 'bones') not in ('bones', 'morph_only'):
        raise ValueError('eye_mode must be bones or morph_only')
    if skip_eyes and any(mapping.get(side+'Eye') in arm.data.bones for side in ('left', 'right') if mapping.get(side+'Eye')):
        raise ValueError('morph_only is for sources without mapped eye bones')
    targets = humanoid_targets(version)
    if skip_eyes:
        targets = {key: value for key, value in targets.items() if key not in ('leftEye', 'rightEye')}
    missing = [key for key in targets if key not in mapping or mapping[key] not in arm.data.bones]
    if missing:
        raise ValueError('Humanoid mapping needs completion before conversion: ' + ', '.join(missing))
    selected = [mapping[key] for key in targets]
    options = dict(options or {})
    roundoff_tolerance = options.get('pose_roundoff_tolerance', 1e-5)
    if not math.isfinite(roundoff_tolerance) or not 0 < roundoff_tolerance <= 1e-4:
        raise ValueError('pose_roundoff_tolerance must be positive and at most 1e-4')
    pose_roundoff = {}
    if len(set(selected)) != len(selected):
        raise ValueError('Humanoid semantics must resolve to distinct bones')
    if arm.animation_data or any(p.constraints for p in arm.pose.bones):
        raise ValueError('Animated/constrained source requires an explicit adapter before automatic conversion')
    for p in arm.pose.bones:
        error = max(abs(p.matrix_basis[i][j] - Matrix.Identity(4)[i][j]) for i in range(4) for j in range(4))
        if error > roundoff_tolerance:
            raise ValueError('Start from neutral source pose: ' + p.name)
        if error > 1e-12:
            pose_roundoff[p.name] = error
    scale = arm.matrix_world.to_scale()
    if min(scale) <= 0 or max(scale)-min(scale) > max(scale)*1e-5 or arm.matrix_world.determinant() <= 0:
        raise ValueError('Normalize reflected/nonuniform armature transforms first')
    meshes = _bound(arm)
    if not meshes:
        raise ValueError('No bound meshes; provide a skinned source for foot endpoint detection')
    modifier_plans = {}
    for obj in meshes:
        mods = [m for m in obj.modifiers if m.type == 'ARMATURE']
        if len(mods) != 1 or mods[0].object != arm or not mods[0].show_viewport:
            raise ValueError('Expected one enabled Armature modifier: ' + obj.name)
        if obj.animation_data or obj.parent_type != 'OBJECT':
            raise ValueError('Animated or bone-parented mesh requires an adapter: ' + obj.name)
        if obj.data.shape_keys and obj.data.shape_keys.animation_data:
            raise ValueError('Animated shape keys require an adapter: ' + obj.name)
        modifier_plans[obj.name] = _api('modifier_policy.py')['plan_mesh_modifiers'](obj)
        unsupported = modifier_plans[obj.name]['unsupported']
        if unsupported:
            raise ValueError('Unsupported evaluated modifier: ' + obj.name + '/' + unsupported[0]['name'])
    options = dict(options or {})
    if options.get('apose_mode', 'auto') not in ('auto', 'template', 'already_verified'):
        raise ValueError('apose_mode must be auto, template or already_verified')
    pose_detection = _api('pose_detection.py')['detect_pose'](arm.name, mapping)
    if pose_detection['status'] != 'passed':
        raise ValueError('Rest pose requires adaptation: '+json.dumps(pose_detection,ensure_ascii=False))
    if options.get('apose_mode') == 'already_verified' and not pose_detection['matches_target_apose']:
        raise ValueError('already_verified requires measured target A-pose; use auto to normalize')
    _package(options.get('mmd_package'))
    _api('arm_twist.py')['check_conflicts'](arm)
    reused_controls, duplicate_controls = _reconcile_controls(arm, meshes, set(selected))
    waist_layout=_api('waist_tools.py')['compute_layout'](
        arm.data.bones[mapping['spine']].head_local,
        arm.data.bones[mapping['leftUpperLeg']].head_local,
        arm.data.bones[mapping['rightUpperLeg']].head_local)
    if skip_eyes and '両目' in reused_controls:
        raise ValueError('Existing eye controls require an explicit eye adapter')
    root_names = []
    if options.get('remove_source_roots', True):
        aliases = set(options.get('source_root_names', ['root']))
        for bone in arm.data.bones:
            if bone.name not in aliases and bone.name.casefold() != 'root':
                continue
            if bone.name in selected or any(parent.name in selected for parent in bone.parent_recursive):
                continue  # An accessory root under a body joint is not the model's structural root.
            for obj in meshes:
                group = obj.vertex_groups.get(bone.name)
                if group and any(any(g.group == group.index and g.weight > 0 for g in v.groups)
                                 for v in obj.data.vertices):
                    raise ValueError('Source root carries skin weights; provide a weight-transfer adapter: ' + bone.name)
            root_names.append(bone.name)
    return {'schema_version': 1, 'source_armature': arm.name, 'vrm_version': version,
            'humanoid': mapping, 'targets': targets, 'meshes': [o.name for o in meshes],
            'options': options, 'source_state': _source_state(arm, meshes),
            'remove_source_roots': root_names,
            'reused_controls': reused_controls, 'duplicate_controls': duplicate_controls,
            'pose_roundoff': pose_roundoff, 'pose_detection': pose_detection,
            'modifier_plans': modifier_plans,
            'waist_layout': waist_layout,
            'generated_roles': [role for role in _control_roles() if role not in reused_controls and not (skip_eyes and role == '両目')]}


def _group(role):
    if role == '全ての親':
        return 'Root'
    if role in ('全ての親', 'センター', 'グルーブ'):
        return 'センター'
    if 'ＩＫ' in role or 'IK親' in role:
        return 'ＩＫ'
    if any(finger in role for finger in ('親指', '人指', '中指', '薬指', '小指')):
        return '指'
    if any(part in role for part in ('肩', '腕', 'ひじ', '手首', '手捩')):
        return '腕'
    if role in ('下半身','腰キャンセル左','腰キャンセル右'):
        return '体(下)'
    if any(part in role for part in ('足', 'ひざ', 'つま先')):
        return '足'
    return '体(上)'


def _foot_points(arm, meshes, mapping):
    from mathutils import Vector
    result = {}
    for side in ('left', 'right'):
        ankle = arm.data.bones[mapping[side+'Foot']].head_local.copy()
        toe = arm.data.bones[mapping[side+'Toes']].head_local.copy()
        forward = toe-ankle
        forward.z = 0
        if forward.length < 1e-6:
            raise ValueError('Indeterminate foot forward direction: ' + side)
        forward.normalize()
        distances, heights = [], []
        # Same weighted geometry convention used by foot_layout.py.
        for semantic in (side+'Toes', side+'Foot'):
            for obj in meshes:
                group = obj.vertex_groups.get(mapping[semantic])
                if group is None:
                    continue
                transform = arm.matrix_world.inverted() @ obj.matrix_world
                positions = [transform @ v.co for v in obj.data.vertices
                             if any(g.group == group.index and g.weight > .25 for g in v.groups)]
                distances.extend((v-ankle).dot(forward) for v in positions)
                heights.extend(v.z for v in positions)
            if distances:
                break
        if not distances:
            raise ValueError('No weighted foot geometry: ' + side)
        distances.sort()
        front = distances[round(.995*(len(distances)-1))]
        base = (toe-ankle).dot(forward)
        if front <= base + 1e-5:
            raise ValueError('Foot geometry does not extend beyond toe joint: ' + side)
        # Keep source toe height; endpoint projection comes from real geometry.
        tip = ankle+forward*front
        tip.z = toe.z
        result[side] = {'ankle': ankle, 'toe': toe, 'tip': tip, 'forward': forward,
                        'floor': min(heights)}
    return result


def _forward_prebend(thigh, shin, forward):
    """Reverse a backward rest prebend within its measured hip/knee/ankle plane.

    Reflection preserves both segment lengths and endpoints. Mesh/weights stay
    unchanged; this is a rest pivot correction, never a Pose rotation.
    """
    axis = shin.tail-thigh.head
    if axis.length < 1e-8:
        raise ValueError('Degenerate leg')
    axis.normalize()
    front = forward-axis*forward.dot(axis)
    if front.length < 1e-8:
        raise ValueError('Cannot determine anatomical knee forward plane')
    front.normalize()
    old = shin.head.copy()
    offset = (old-thigh.head).dot(front)
    if offset < -1e-6:
        projected = thigh.head+axis*((old-thigh.head).dot(axis))
        radial = old-projected
        if -offset < radial.length*.25:
            return {'changed': False, 'needs_review': 'mostly_lateral_rest_knee'}
        new = projected-radial
        thigh.tail = new; shin.head = new
        return {'changed': True, 'old': list(old), 'new': list(new),
                'backward_offset': offset}
    return {'changed': False}


def _build(arm, meshes, plan):
    import bpy
    from mathutils import Vector
    mapping, targets = plan['humanoid'], plan['targets']
    roles = {role: mapping[key] for key, role in targets.items()}
    roles.update(plan['reused_controls'])
    reused_names = set(plan['reused_controls'].values())
    for name in plan['generated_roles']:
        if name in arm.data.bones:
            raise ValueError('Generated control name collides with preserved bone: ' + name)
    points = _foot_points(arm, meshes, mapping)
    arm['auto_rig_toe_pivots'] = json.dumps({prefix:list(points[side]['toe'])
        for side,prefix in [('left','左'),('right','右')]},ensure_ascii=False)
    arm['auto_rig_toe_articulation'] = arm['auto_rig_toe_pivots']
    character_forward = (points['left']['forward']+points['right']['forward']).normalized()
    hip = arm.data.bones[mapping['hips']].head_local.copy()
    up = Vector((0, 0, 1))
    height = arm.data.bones[mapping['head']].head_local.z - min(p['toe'].z for p in points.values())
    if height <= 1e-5:
        raise ValueError('Expected Z-up source armature coordinates')
    length = height*.04
    mid_x = (arm.data.bones[mapping['leftUpperLeg']].head_local.x +
             arm.data.bones[mapping['rightUpperLeg']].head_local.x)*.5
    center = hip.copy(); center.x = mid_x
    ground = min(p['floor'] for p in points.values())
    source_rest = {b.name: (b.matrix_local.copy(), b.length, b.use_connect) for b in arm.data.bones}
    bpy.ops.object.select_all(action='DESELECT')
    arm.select_set(True)
    bpy.context.view_layer.objects.active = arm
    bpy.ops.object.mode_set(mode='EDIT')
    try:
        eb = arm.data.edit_bones
        # Disconnect temporarily so parent edits cannot pull attached children.
        for b in eb:
            b.use_connect = False
        def generate(role, head, tail, parent):
            bone = eb[roles[role]] if role in plan['reused_controls'] else eb.new(role)
            bone.use_connect = False; bone.head = head; bone.tail = tail; bone.roll = 0
            bone.use_deform = False
            bone.parent = eb[roles[parent]] if parent else None
            roles[role] = bone.name
            return bone
        root_head = Vector((mid_x, center.y, ground))
        generate('全ての親', root_head, root_head+up*length, None)
        generate('センター', center, center+up*length, '全ての親')
        generate('グルーブ', center, center+up*length, 'センター')
        for name in source_rest:
            if name not in reused_names and eb[name].parent is None:
                eb[name].parent = eb[roles['全ての親']]
        eb[roles['下半身']].parent = eb[roles['グルーブ']]
        eb[roles['上半身']].parent = eb[roles['グルーブ']]
        # Restore all source world-in-armature rest matrices after hierarchy edits.
        for name, (matrix, bone_length, connected) in source_rest.items():
            if name not in reused_names:
                eb[name].matrix = matrix; eb[name].length = bone_length
        lower = eb[roles['下半身']]
        lower.head = eb[roles['上半身']].head
        lower.tail = lower.head-up*max((hip.z-ground)*.10, length)
        lower.align_roll(Vector((0, 1, 0)))
        waist_layout=_api('waist_tools.py')['build_edit_bones'](eb,roles,generate)
        prebend_repairs = {}
        for side, prefix in [('left', '左'), ('right', '右')]:
            p = points[side]
            foot = eb[roles[prefix+'足首']]
            foot.tail = p['tip']
            ex = eb[roles[prefix+'足先EX']]
            toe_layout = _api('toe_geometry.py')['layout'](p['ankle'],p['toe'],p['tip'])
            ex.head = toe_layout['head']; ex.tail = toe_layout['tail']
            generate(prefix+'つま先', p['tip'], p['tip']+up*length, prefix+'足首')
            ground_head = p['ankle'].copy(); ground_head.z = ground
            generate(prefix+'足IK親', ground_head, p['ankle'], '全ての親')
            generate(prefix+'足ＩＫ', p['ankle'], p['ankle']-p['forward']*length*2, prefix+'足IK親')
            generate(prefix+'つま先ＩＫ', p['tip'], p['tip']-up*length, prefix+'足ＩＫ')
            thigh_bone = eb[roles[prefix+'足']]
            knee_bone = eb[roles[prefix+'ひざ']]
            prebend_repairs[prefix] = _forward_prebend(thigh_bone, knee_bone, character_forward)
            thigh_direction = thigh_bone.tail-thigh_bone.head
            shin_direction = knee_bone.tail-knee_bone.head
            plane = -thigh_direction.cross(shin_direction)
            expected_hinge = shin_direction.cross(character_forward).normalized()
            if plane.length > thigh_direction.length*shin_direction.length*1e-4:
                hinge = plane.normalized()
            else:
                # Only a degenerate straight chain needs a foot-based hint.
                # The rest prebend has already been checked against body forward.
                # Preserve this corrected plane when aligning the signed hinge.
                hinge = expected_hinge
            for role in (prefix+'足', prefix+'ひざ'):
                bone = eb[roles[role]]
                direction = (bone.tail-bone.head).normalized()
                # Use the actual hip/knee/ankle plane, not a toe-out foot yaw.
                bone.align_roll(hinge.cross(direction))
        if '左目' in roles and '右目' in roles:
            eyes = (eb[roles['左目']].head+eb[roles['右目']].head)*.5
            generate('両目', eyes, eyes+up*length, '頭')
        # Preserve unrelated source chains exactly, including their connected flags.
        changed = reused_names | {roles[r] for r in ['下半身'] + [s+r for s in ('左', '右')
                   for r in ('足', 'ひざ', '足首', '足先EX')]}
        for name, (matrix, bone_length, connected) in source_rest.items():
            if name not in changed:
                eb[name].matrix = matrix; eb[name].length = bone_length
        for name, (_, _, connected) in source_rest.items():
            b = eb[name]
            b.use_connect = bool(name not in reused_names and connected and b.parent and (b.head-b.parent.tail).length < height*1e-7)
    finally:
        bpy.ops.object.mode_set(mode='OBJECT')
    arm['auto_rig_prebend_repairs'] = json.dumps(prebend_repairs, ensure_ascii=False)
    arm['auto_rig_waist_layout'] = json.dumps(waist_layout,ensure_ascii=False)
    for index, bone in enumerate(arm.pose.bones):
        bone.mmd_bone.name_j = bone.name
        bone.mmd_bone.name_e = bone.name
        bone.mmd_bone.bone_id = index
    for role, name in roles.items():
        p = arm.pose.bones[name]; p.mmd_bone.name_j = role
        # mmd_tools axis properties use its XZY convention (see FnBone loaders).
        p.mmd_bone.enabled_local_axes = True
        p.mmd_bone.local_axis_x = p.bone.matrix_local.to_3x3().col[0].xzy
        p.mmd_bone.local_axis_z = p.bone.matrix_local.to_3x3().col[2].xzy
    if len({p.mmd_bone.name_j for p in arm.pose.bones}) != len(arm.pose.bones):
        raise ValueError('Preserved source bone conflicts with target Japanese name')
    for side in ('左', '右'):
        knee = arm.pose.bones[roles[side+'ひざ']]
        for axis in 'xyz':
            setattr(knee, 'use_ik_limit_'+axis, True)
            setattr(knee, 'ik_min_'+axis, -math.pi+.01 if axis == 'x' else 0)
            setattr(knee, 'ik_max_'+axis, 0)
            setattr(knee, 'lock_ik_'+axis, axis != 'x')
        knee.mmd_bone.enabled_fixed_axis = True
        knee.mmd_bone.fixed_axis = knee.bone.matrix_local.to_3x3().col[0].xzy
        for owner_role, target_role, count in [(side+'ひざ', side+'足ＩＫ', 2),
                                                (side+'足首', side+'つま先ＩＫ', 1)]:
            owner = arm.pose.bones[roles[owner_role]]
            c = owner.constraints.new('IK'); c.name = 'auto_rig_'+target_role
            c.target = arm; c.subtarget = roles[target_role]
            c.chain_count = count; c.use_tail = True; c.use_stretch = False
            c.iterations = 128
            owner.mmd_bone.ik_rotation_constraint = math.pi/4
            arm.pose.bones[roles[target_role]].mmd_bone.transform_order = 1
        # Explicitly use the toe marker as PMX effector, never the weighted EX.
        marker = arm.pose.bones[roles[side+'つま先']]
        hint = marker.constraints.new('DAMPED_TRACK')
        hint.name = 'mmd_ik_target_custom'; hint.mute = True; hint.influence = 0
        hint.target = arm; hint.subtarget = roles[side+'つま先ＩＫ']
        arm.pose.bones[roles[side+'つま先ＩＫ']].mmd_bone.ik_rotation_constraint = math.pi/4
    # Use the native additional-transform builder, with no extra COPY layer.
    _api('waist_tools.py')['configure_controls'](arm,roles)
    for side in ('左', '右'):
        if side+'目' not in roles:
            continue
        eye = arm.pose.bones[roles[side+'目']]
        eye.mmd_bone.has_additional_rotation = True
        eye.mmd_bone.additional_transform_bone = roles['両目']
        eye.mmd_bone.additional_transform_influence = 1.0
    bpy.context.view_layer.update()
    return roles, points, height


def _standardize_names(arm, meshes, roles, remove_roots, duplicate_controls=None):
    """Rename body bones and skin references; remove verified unweighted source roots."""
    import bpy
    rename = {old: role for role,old in roles.items() if old != role}
    duplicate_controls = dict(duplicate_controls or {})
    reference_map = dict(rename, **duplicate_controls)
    occupied = set(arm.data.bones.keys()) - set(rename)
    if occupied & set(rename.values()):
        raise ValueError('Standard bone name conflicts with preserved bone: ' + str(occupied & set(rename.values())))
    groups = []
    for obj in meshes:
        for old,new in rename.items():
            group = obj.vertex_groups.get(old)
            other = obj.vertex_groups.get(new)
            if group is not None and other is not None and new not in rename:
                raise ValueError('Standard skin group already exists: '+obj.name+'/'+new)
            if group is not None:
                groups.append((group, new))
    # Preserve references whose RNA properties are not automatically renamed by Blender.
    constraint_refs = []
    for pose in arm.pose.bones:
        for c in pose.constraints:
            if getattr(c, 'target', None) == arm and getattr(c, 'subtarget', '') in reference_map:
                constraint_refs.append((c, 'subtarget', reference_map[c.subtarget]))
            if getattr(c, 'pole_target', None) == arm and getattr(c, 'pole_subtarget', '') in reference_map:
                constraint_refs.append((c, 'pole_subtarget', reference_map[c.pole_subtarget]))
    key_refs = [(key, reference_map[key.vertex_group]) for obj in meshes if obj.data.shape_keys
                for key in obj.data.shape_keys.key_blocks if key.vertex_group in reference_map]
    # Two phases prevent swaps and Blender's implicit .001 suffixing.
    pending = []
    for index,(old,new) in enumerate(rename.items()):
        bone = arm.data.bones[old]
        temporary = '__pmx_body_'+str(index)
        while temporary in arm.data.bones or any(o.vertex_groups.get(temporary) for o in meshes):
            temporary += '_'
        bone.name = temporary
        pending.append((bone, new))
    for bone,new in pending:
        bone.name = new
        if bone.name != new:
            raise RuntimeError('Bone rename was normalized unexpectedly: ' + new)
    for group,new in groups:
        group.name = new
        if group.name != new:
            raise RuntimeError('Skin group rename collided: '+new)
    for owner,attribute,value in constraint_refs:
        setattr(owner,attribute,value)
    for key,value in key_refs:
        key.vertex_group = value
    roles = {role: role for role in roles}
    bpy.ops.object.select_all(action='DESELECT')
    arm.select_set(True);bpy.context.view_layer.objects.active = arm
    removals = {name: None for name in remove_roots}
    removals.update(duplicate_controls)
    if removals:
        bpy.ops.object.mode_set(mode='EDIT')
        try:
            bones = arm.data.edit_bones
            for name, replacement in removals.items():
                bone = bones.get(name)
                if bone is None:
                    raise ValueError('Planned source root disappeared: ' + name)
                # Rest matrices are in armature space; deleting the parent must
                # not move any branch or attach it to the source scene.
                for child in list(bone.children):
                    matrix,length = child.matrix.copy(),child.length
                    child.use_connect = False
                    child.parent = bones[replacement] if replacement else (bone.parent or bones[roles['全ての親']])
                    child.matrix = matrix;child.length = length
                bones.remove(bone)
        finally:
            bpy.ops.object.mode_set(mode='OBJECT')
        for obj in meshes:
            for name in removals:
                group = obj.vertex_groups.get(name)
                if group is not None:
                    obj.vertex_groups.remove(group)
    bpy.context.view_layer.update()
    return roles, {'renamed': rename, 'removed_roots': list(remove_roots),
                   'removed_duplicate_controls': duplicate_controls}


def _collections(arm, roles):
    role_for = {name: role for role, name in roles.items()}
    names = ['Root', 'センター', 'ＩＫ', '体(上)', '腕', '指', '体(下)', '足', 'その他']
    old_names = {c.name for c in arm.data.collections_all} - set(names)
    assignments = {}
    for b in arm.data.bones:
        if arm.pose.bones[b.name].is_mmd_shadow_bone and b.collections:
            assignments[b.name] = [c.name for c in b.collections]
            continue
        if b.name in role_for:
            group = _group(role_for[b.name])
        else:
            # Finger end bones inherit finger classification, not arbitrary head accessories.
            ancestors = [a.name for a in b.parent_recursive]
            group = next((_group(role_for[n]) for n in ancestors if n in role_for and _group(role_for[n]) == '指'), None)
            group = group or 'その他'
        assignments[b.name] = [group]
    plan = {'collections': [{'name': n, 'parent': None} for n in names],
            'assignments': assignments, 'unresolved': []}
    # Include existing plugin helper collections as managed only for actual helpers.
    for c in arm.data.collections_all:
        if c.name not in names:
            plan['collections'].append({'name': c.name, 'parent': c.parent.name if c.parent else None})
    report = _api('bone_collections.py')['apply_collections'](arm.name, plan)
    report['empty_old_collections_cleanup'] = _api('bone_collections.py')['prune_empty_collections'](
        arm.name, old_names, plan=plan)
    return plan, report


def _with_runtime_helpers(arm, plan):
    plan = json.loads(json.dumps(plan))
    known = {s['name'] for s in plan['collections']}
    for pose in arm.pose.bones:
        if pose.name in plan['assignments']:
            continue
        if not pose.is_mmd_shadow_bone or not pose.bone.collections:
            continue  # ordinary missing bones remain a validation failure
        plan['assignments'][pose.name] = [c.name for c in pose.bone.collections]
        for c in pose.bone.collections:
            if c.name not in known:
                plan['collections'].append({'name': c.name, 'parent': c.parent.name if c.parent else None})
                known.add(c.name)
    return plan


def validate_rig(armature_name, *, collection_plan=None, scan=True):
    import bpy
    arm = bpy.data.objects[armature_name]
    if collection_plan is None and arm.get('auto_rig_collection_plan'):
        collection_plan = json.loads(arm['auto_rig_collection_plan'])
    audit = _api('rig_audit.py')
    roles = {p.mmd_bone.name_j: p.name for p in arm.pose.bones}
    height = arm.data.bones[roles['頭']].head_local.z-arm.data.bones[roles['全ての親']].head_local.z
    include_eyes = arm.get('auto_rig_eye_mode', 'bones') != 'morph_only'
    waist_roles=('腰','腰キャンセル左','腰キャンセル右')
    checks = {'body': audit['validate_mmd_body'](arm.name, include_toe_ex=True, include_eyes=include_eyes,extra_required=waist_roles),
              'structure': audit['validate_structure'](arm.name, height*1e-6, 1e-3),
              'feet': _api('foot_layout.py')['validate_foot_layout'](
                  arm.name, ground_z=arm.data.bones[roles['全ての親']].head_local.z)}
    checks['foot_order'] = _api('foot_order.py')['validate'](arm.name)
    suffix_names = arm.get('auto_rig_internal_naming') == 'suffix'
    internal_name = _api('bone_naming.py')['internal_name'] if suffix_names else (lambda role:role)
    naming_errors = [role for role in audit['mmd_body_names'](include_toe_ex=True, include_eyes=include_eyes,extra_required=waist_roles)
                     if roles.get(role) != internal_name(role)]
    deleted_roots = json.loads(arm.get('auto_rig_removed_roots', '[]'))
    controls = {}
    for pose in arm.pose.bones:
        if pose.is_mmd_shadow_bone:
            continue
        role = _control_role(pose.mmd_bone.name_j) or _control_role(pose.name)
        if role:
            controls.setdefault(role, []).append(pose.name)
    duplicate_controls = {role:names for role,names in controls.items() if len(names)>1}
    checks['standard_names'] = {'status': 'failed' if naming_errors or
        duplicate_controls or any(name in arm.data.bones for name in deleted_roots) else 'passed',
        'nonstandard_names': naming_errors, 'removed_roots': deleted_roots,
        'duplicate_controls': duplicate_controls}
    if collection_plan:
        collection_plan = _with_runtime_helpers(arm, collection_plan)
        checks['collections'] = _api('bone_collections.py')['validate_collections'](arm.name, collection_plan)
    lower = arm.data.bones[roles['下半身']]
    upper = arm.data.bones[roles['上半身']]
    checks['torso'] = {'status': 'passed' if (lower.head_local-upper.head_local).length < height*1e-5
                      and lower.tail_local.z < lower.head_local.z else 'failed',
                      'scope': 'upper_lower_torso_junction'}
    checks['waist']=_api('waist_tools.py')['validate_waist'](arm.name,motion=scan)
    checks['arm_twist']=_api('arm_twist.py')['validate'](arm,motion=scan)
    if scan:
        legs = {}
        sweep = _api('leg_ik.py')['sweep_leg']
        for side in ('左', '右'):
            ankle = arm.data.bones[roles[side+'足首']].head_local
            tip = arm.data.bones[roles[side+'つま先']].head_local
            forward = tip-ankle; forward.z = 0; forward.normalize()
            leg_scale = sum(arm.data.bones[roles[side+role]].length for role in ('足','ひざ'))
            offsets = []
            for amount in [0, .05, .15, .3, .5, .65, .5, .3, .15, .05, 0]:
                v = forward*leg_scale*amount*.20
                v.z = leg_scale*amount
                offsets.append(list(v))
            lateral = forward.cross(__import__('mathutils').Vector((0, 0, 1)))
            for along, across in [(-.06, 0), (.06, 0), (0, -.05), (0, .05), (0, 0)]:
                v = forward*leg_scale*along+lateral*leg_scale*across
                v.z = leg_scale*.12
                offsets.append(list(v))
            knee = arm.pose.bones[roles[side+'ひざ']]
            free_axes = [axis for axis in 'xyz' if not getattr(knee, 'lock_ik_'+axis)
                         and getattr(knee, 'use_ik_limit_'+axis)
                         and abs(getattr(knee, 'ik_max_'+axis)-getattr(knee, 'ik_min_'+axis)) > 1e-5]
            if len(free_axes) != 1:
                raise ValueError('Expected a single measured knee hinge: ' + knee.name)
            result = sweep(arm.name, roles[side+'足'], roles[side+'ひざ'], roles[side+'足ＩＫ'], offsets,
                           hinge_axis=free_axes[0].upper(), foot_name=roles[side+'足首'],
                           toe_control_name=roles[side+'つま先ＩＫ'], bend_forward=forward)
            # Lift/return is the first continuous path; later probes are separate paths.
            samples = result['samples']
            angles = [sample['signed_foot_twist_degrees'] for sample in samples]
            return_error = max((abs(angles[i]-angles[10-i]) for i in range(5)), default=0)
            lift_jump = max((abs(angles[i]-angles[i-1]) for i in range(1,11)), default=0)
            limits = json.loads(arm.get('auto_rig_motion_limits', '{}'))
            twist_limit = limits.get('foot_twist_degrees', 15.0)
            issues = []
            wrong_bend = [i for i,sample in enumerate(samples)
                          if sample['offset'][2] > leg_scale*.03
                          and sample['knee_forward_offset'] < -leg_scale*.005]
            if wrong_bend:
                issues.append('knee_bends_backward')
            result['wrong_knee_bend_samples'] = wrong_bend
            if result['max_foot_twist_degrees'] > twist_limit:
                issues.append('foot_axial_twist')
            if return_error > 1 or lift_jump > twist_limit:
                issues.append('foot_discontinuity_or_hysteresis')
            result['review_issues'] = issues
            result['foot_twist_review_limit_degrees'] = twist_limit
            result['lift_return_twist_error_degrees'] = return_error
            result['max_lift_twist_step_degrees'] = lift_jump
            result['status'] = ('passed' if result['max_ankle_error'] < height*.003 and
                                result['max_off_hinge_degrees'] < 1 and not issues else 'failed')
            legs[side] = result
        checks['leg_motion'] = {'status': 'passed' if all(r['status']=='passed' for r in legs.values()) else 'failed', 'legs': legs}
    return {'status': 'passed' if all(v['status']=='passed' for v in checks.values()) else 'failed',
            'scope': 'blender_rig', 'checks': checks, 'unverified': ['pmx_roundtrip', 'task_specific_motion_deformation']}


def restore_rest_checkpoint(armature_name, checkpoint):
    import bpy
    from mathutils import Matrix
    arm=bpy.data.objects[armature_name]
    snapshot=json.loads(Path(checkpoint).read_text(encoding='utf-8'))
    bpy.context.view_layer.objects.active=arm;arm.select_set(True)
    if arm.mode!='OBJECT': bpy.ops.object.mode_set(mode='OBJECT')
    bpy.ops.object.mode_set(mode='EDIT')
    try:
        for b in arm.data.edit_bones: b.use_connect=False
        for name,state in snapshot.items():
            bone=arm.data.edit_bones[name]
            bone.matrix=Matrix(state['matrix']);bone.length=state['length']
        for name,state in snapshot.items():
            bone=arm.data.edit_bones[name]
            bone.use_connect=bool(state['connected'] and bone.parent and (bone.head-bone.parent.tail).length<1e-7)
    finally:
        bpy.ops.object.mode_set(mode='OBJECT')
    return {'status':'restored'}


def repair_knee_bend(armature_name, output_dir):
    """Fix a backward rest prebend without posing/baking the skin a second time."""
    import bpy
    from mathutils import Vector
    arm = bpy.data.objects[armature_name]
    output = Path(output_dir); output.mkdir(parents=True, exist_ok=True)
    snapshot = {b.name: {'matrix': [list(row) for row in b.matrix_local], 'length': b.length,
                         'connected': b.use_connect} for b in arm.data.bones}
    (output/'knee-repair-checkpoint.json').write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding='utf-8')
    bpy.context.view_layer.objects.active = arm
    arm.select_set(True)
    if arm.mode != 'OBJECT': bpy.ops.object.mode_set(mode='OBJECT')
    forward = Vector((0,0,0))
    for side in ('左','右'):
        direction = arm.data.bones[side+'つま先'].head_local-arm.data.bones[side+'足首'].head_local
        direction.z=0; forward+=direction.normalized()
    forward.normalize()
    bpy.ops.object.mode_set(mode='EDIT')
    report = {}
    try:
        bones=arm.data.edit_bones
        for bone in bones: bone.use_connect=False
        for side in ('左','右'):
            thigh,shin=bones[side+'足'],bones[side+'ひざ']
            report[side]=_forward_prebend(thigh,shin,forward)
            a,b=thigh.tail-thigh.head,shin.tail-shin.head
            hinge=-a.cross(b)
            if hinge.length < 1e-8: hinge=b.cross(forward)
            hinge.normalize()
            for bone in (thigh,shin):
                bone.align_roll(hinge.cross((bone.tail-bone.head).normalized()))
        for bone in bones:
            state=snapshot[bone.name]
            bone.use_connect=bool(state['connected'] and bone.parent and (bone.head-bone.parent.tail).length<1e-7)
    finally:
        bpy.ops.object.mode_set(mode='OBJECT')
    for side in ('左','右'):
        knee=arm.pose.bones[side+'ひざ']
        for axis in 'xyz':
            setattr(knee,'use_ik_limit_'+axis,True)
            setattr(knee,'ik_min_'+axis,-math.pi+.01 if axis=='x' else 0)
            setattr(knee,'ik_max_'+axis,0)
            setattr(knee,'lock_ik_'+axis,axis!='x')
        knee.mmd_bone.enabled_fixed_axis=True
    repair_export_axes(arm.name,output_dir)
    result={'status':'applied_pending_backtest','method':'reflect_backward_rest_knee','legs':report}
    (output/'knee-repair.json').write_text(json.dumps(result, ensure_ascii=False, indent=2),encoding='utf-8')
    return result


def repair_export_axes(armature_name, output_dir):
    """Repair only MMD axis metadata from actual Rest matrices, leaving bind data intact."""
    import bpy
    arm = bpy.data.objects[armature_name]
    roles = json.loads(arm['auto_rig_mapping'])
    snapshots = {}
    for name in roles.values():
        p = arm.pose.bones[name]; m = p.mmd_bone
        snapshots[name] = {'local_axis_x': list(m.local_axis_x), 'local_axis_z': list(m.local_axis_z),
                           'fixed_axis': list(m.fixed_axis)}
    output = Path(output_dir); output.mkdir(parents=True, exist_ok=True)
    (output/'axis-repair-checkpoint.json').write_text(json.dumps(snapshots, ensure_ascii=False, indent=2), encoding='utf-8')
    try:
        for name in roles.values():
            p = arm.pose.bones[name]; m = p.mmd_bone
            axes = p.bone.matrix_local.to_3x3()
            if m.enabled_local_axes:
                m.local_axis_x = axes.col[0].xzy
                m.local_axis_z = axes.col[2].xzy
            if m.enabled_fixed_axis and p.mmd_bone.name_j in ('左ひざ','右ひざ'):
                m.fixed_axis = axes.col[0].xzy
        report = {'status': 'applied_pending_roundtrip', 'scope': 'MMD local/fixed axis metadata only',
                  'reason': 'Use mmd_tools XZY axis property convention', 'bones': list(snapshots)}
        (output/'axis-repair.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        return report
    except Exception:
        for name, fields in snapshots.items():
            for field, value in fields.items():
                setattr(arm.pose.bones[name].mmd_bone, field, value)
        raise


def backtest_rig(armature_name, output_dir=None):
    """Run post-conversion diagnostics. Failed metrics are an LLM repair queue."""
    report = validate_rig(armature_name, scan=True)
    report['scope'] = 'post_conversion_motion_backtest'
    report['repair_queue'] = [name for name, check in report['checks'].items()
                              if check['status'] != 'passed']
    if output_dir is not None:
        output = Path(output_dir); output.mkdir(parents=True, exist_ok=True)
        (output/'rig-backtest.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    return report


def convert_rig(armature_name, output_dir, *, mapping=None, version=None, options=None):
    """Create a fresh work scene. On failure, remove only the new output scene/data."""
    import bpy
    from mathutils import Matrix
    plan = plan_rig(armature_name, mapping=mapping, version=version, options=options)
    output = Path(output_dir); output.mkdir(parents=True, exist_ok=True)
    (output/'rig-plan.json').write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding='utf-8')
    source = bpy.data.objects[armature_name]
    source_meshes = [bpy.data.objects[n] for n in plan['meshes']]
    window = bpy.context.window
    old_scene = window.scene
    scene = bpy.data.scenes.new(source.name+'_MMD_work')
    owned_objects, owned_data = [], []
    report = None
    try:
        arm = source.copy(); arm.data = source.data.copy(); arm.name = source.name+'_MMD'
        arm.data.use_mirror_x = False  # Scripted bilateral edits must not mirror each other.
        arm.parent = None; arm.matrix_world = source.matrix_world.copy()
        scene.collection.objects.link(arm); owned_objects.append(arm); owned_data.append(arm.data)
        meshes = []
        modifier_cleanup = []
        for original in source_meshes:
            obj = original.copy(); obj.data = original.data.copy(); obj.parent = arm
            obj.matrix_world = original.matrix_world.copy()
            scene.collection.objects.link(obj); owned_objects.append(obj); owned_data.append(obj.data)
            for m in obj.modifiers:
                if m.type == 'ARMATURE':
                    m.object = arm
            changes = _api('modifier_policy.py')['disable_on_copy'](
                obj, plan['modifier_plans'][original.name]['disable_on_copy'], work_copy=True)
            modifier_cleanup.append(dict(source_mesh=original.name,work_mesh=obj.name,modifiers=changes))
            meshes.append(obj)
        window.scene = scene
        bpy.context.view_layer.objects.active = arm
        arm.select_set(True)
        # Normalize only preflight-measured identity residuals on the work copy;
        # real poses outside the explicit tolerance are still rejected.
        for name in plan['pose_roundoff']:
            arm.pose.bones[name].matrix_basis = Matrix.Identity(4)
        bpy.context.view_layer.update()
        pose_report = _api('pose_detection.py')['normalize_apose'](
            arm.name, [o.name for o in meshes], plan['humanoid'], work_copy=True)
        bake_report = pose_report['bake']
        geometry = {o.name: [v.co.copy() for v in o.data.vertices] for o in meshes}
        roles, points, height = _build(arm, meshes, plan)
        roles, naming_report = _standardize_names(arm, meshes, roles, plan['remove_source_roots'], plan['duplicate_controls'])
        twist_roles, twist_report = _api('arm_twist.py')['build'](arm,meshes,work_copy=True)
        roles.update(twist_roles)
        naming_report['reused_controls'] = plan['reused_controls']
        module = _package(plan['options'].get('mmd_package'))
        model_module = importlib.import_module(module+'.core.model')
        model = model_module.Model.create(arm.name, armature_object=arm, add_root_bone=False)
        root = model.rootObject(); owned_objects.append(root)
        importlib.import_module(module+'.core.bone').FnBone.apply_additional_transformation(arm)
        _api('arm_twist.py')['configure_preview'](arm)
        for obj in meshes:
            world = obj.matrix_world.copy(); obj.parent = arm; obj.matrix_world = world
        collection_plan, collection_report = _collections(arm, roles)
        # Build actual PMX display frames, separate from Blender collections.
        frames = root.mmd_root.display_item_frames
        if frames.get('表情') is None:
            f = frames.add(); f.name = '表情'; f.is_special = True
        for spec in collection_plan['collections']:
            name = spec['name']
            members = [b for b, groups in collection_plan['assignments'].items() if name in groups]
            if not members or name.startswith('mmd_'):
                continue
            frame = frames.get(name) or frames.add(); frame.name = frame.name_e = name
            frame.is_special = name == 'Root'
            for bone in members:
                if arm.data.bones[bone].hide:
                    continue  # Twist distribution bones deform skin but are not UI controls.
                item = frame.data.add(); item.type = 'BONE'; item.name = bone
        for obj in meshes:
            if any((v.co-original).length > height*1e-7 for v, original in zip(obj.data.vertices, geometry[obj.name])):
                raise RuntimeError('Rest-axis construction modified bind mesh')
        arm['auto_rig_eye_mode'] = plan['options'].get('eye_mode', 'bones')
        arm['auto_rig_version'] = 1
        arm['auto_rig_mapping'] = json.dumps(roles, ensure_ascii=False)
        arm['auto_rig_collection_plan'] = json.dumps(collection_plan, ensure_ascii=False)
        arm['auto_rig_removed_roots'] = json.dumps(plan['remove_source_roots'], ensure_ascii=False)
        if plan['options'].get('internal_naming','suffix') == 'suffix':
            suffix_report = _api('bone_naming.py')['apply'](arm.name,work_copy=True)
            roles = suffix_report['roles']
            naming_report['renamed'] = {old:suffix_report['renamed'].get(new,new)
                                       for old,new in naming_report['renamed'].items()}
            naming_report['suffix_names'] = suffix_report['renamed']
            collection_plan = json.loads(arm['auto_rig_collection_plan'])
        foot_order = _api('foot_order.py')['repair'](arm.name, work_copy=True)
        validation = validate_rig(arm.name, collection_plan=collection_plan)
        report = {'status': 'rig_prepared' if validation['status']=='passed' else 'failed',
                  'scene': scene.name, 'armature': arm.name, 'model_root': root.name,
                  'meshes': [o.name for o in meshes], 'roles': roles, 'naming': naming_report,
                  'apose': pose_report, 'bake': bake_report, 'validation': validation,
                  'modifier_cleanup': modifier_cleanup,
                  'waist_layout': json.loads(arm['auto_rig_waist_layout']),
                  'arm_twist': twist_report, 'foot_order': foot_order,
                  'prebend_repairs': json.loads(arm.get('auto_rig_prebend_repairs','{}')),
                  'eye_mode': arm['auto_rig_eye_mode'],
                  'normalized_pose_roundoff': plan['pose_roundoff'],
                  'source_unchanged': _source_state(source, source_meshes)==plan['source_state'],
                  'scope': 'body rig, A-pose, IK, collections and display frames; materials/physics excluded'}
        if validation['status'] != 'passed' and report['source_unchanged'] and plan['options'].get('keep_failed_for_review', False):
            report['status'] = 'needs_repair'
            return report
        if validation['status'] != 'passed' or not report['source_unchanged']:
            raise RuntimeError('Rig validation failed: '+json.dumps(validation,ensure_ascii=False))
        return report
    except Exception as exc:
        if bpy.context.object and bpy.context.object.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
        window.scene = old_scene
        for obj in list(scene.objects):
            bpy.data.objects.remove(obj, do_unlink=True)
        for data in owned_data:
            if data.users == 0:
                collection = bpy.data.armatures if isinstance(data, bpy.types.Armature) else bpy.data.meshes
                collection.remove(data)
        bpy.data.scenes.remove(scene)
        if report is not None:
            report['output_rolled_back'] = True
        raise
    finally:
        window.scene = old_scene
