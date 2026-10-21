"""Read-only bpy inspection and basic validation. No automatic rig conversion.

Load with runpy.run_path inside Blender MCP. Standard Python can use
validate_plan and validation_summary without bpy. JSON snapshots are evidence,
not a replacement for a .blend rollback checkpoint.
"""
import json
import math
from pathlib import Path


def write_json(path, data):
    """Write an artifact; use a stage-specific path to keep earlier reports."""
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with Path(path).open('w', encoding='utf-8') as stream:
        json.dump(data, stream, ensure_ascii=False, indent=2, allow_nan=False)


def _matrix(value):
    return [list(row) for row in value]


def _armature(name):
    import bpy
    arm = bpy.context.scene.objects.get(name)
    if arm is None or arm.type != 'ARMATURE':
        raise ValueError(f'Expected scene armature: {name}')
    if arm.mode == 'EDIT':
        raise ValueError('Exit Edit Mode and flush edits before taking a snapshot')
    return arm


def _humanoid(arm):
    """Read supported VRM addon metadata, report absence rather than guess."""
    ext = getattr(arm.data, 'vrm_addon_extension', None)
    if ext is None:
        return {'status': 'unavailable', 'version': None, 'mapping': {}}
    version = str(ext.spec_version)
    mapping = {}
    if version == '0.0':
        pairs = [(item.bone, item.node) for item in ext.vrm0.humanoid.human_bones]
    elif version == '1.0':
        pairs = [(getattr(key, 'value', str(key)), value.node)
                 for key, value in ext.vrm1.humanoid.human_bones.human_bone_name_to_human_bone().items()]
    else:
        return {'status': 'unsupported_version', 'version': version, 'mapping': {}}
    for semantic, node in pairs:
        if node.bone_name:
            if semantic in mapping:
                raise ValueError(f'Duplicate humanoid semantic: {semantic}')
            mapping[semantic] = node.bone_name
    return {'status': 'read' if mapping else 'empty', 'version': version, 'mapping': mapping}


def inspect_rig(armature_name, include_geometry=True):
    """Extract live rest/pose/hierarchy, humanoid, weights and shape-key coordinates.

    Does not change mode, selection, pose or scene. Unsupported addon API raises.
    Supplement with task-specific full driver/constraint/MMD metadata inspection.
    """
    import bpy
    arm = _armature(armature_name)
    bones = {}
    for bone in arm.data.bones:
        pose = arm.pose.bones[bone.name]
        _, roll = bpy.types.Bone.AxisRollFromMatrix(bone.matrix_local.to_3x3())
        bones[bone.name] = {
            'parent': bone.parent.name if bone.parent else None,
            'head': list(bone.head_local), 'tail': list(bone.tail_local),
            'length': bone.length, 'rest_matrix': _matrix(bone.matrix_local),
            'axes': [list(bone.matrix_local.to_3x3().col[i]) for i in range(3)],
            'roll_radians': roll, 'use_connect': bone.use_connect,
            'use_deform': bone.use_deform, 'pose_matrix': _matrix(pose.matrix),
            'matrix_basis': _matrix(pose.matrix_basis),
            'constraints': [{'name': c.name, 'type': c.type, 'valid': c.is_valid,
                             'target': getattr(getattr(c, 'target', None), 'name', None),
                             'subtarget': getattr(c, 'subtarget', None)} for c in pose.constraints],
        }
    meshes = {}
    for obj in bpy.context.scene.objects:
        if obj.type != 'MESH' or not any(m.type == 'ARMATURE' and m.object == arm for m in obj.modifiers):
            continue
        data = {'matrix_world': _matrix(obj.matrix_world),
                'vertex_count': len(obj.data.vertices),
                'groups': {str(g.index): g.name for g in obj.vertex_groups},
                'weights': [[[g.group, g.weight] for g in v.groups] for v in obj.data.vertices],
                'modifiers': [], 'shape_keys': []}
        for mod in obj.modifiers:
            item = {'name': mod.name, 'type': mod.type, 'show_viewport': mod.show_viewport,
                    'show_render': mod.show_render}
            if mod.type == 'ARMATURE':
                item.update(target=getattr(mod.object, 'name', None))
                for key in ('use_vertex_groups', 'use_bone_envelopes', 'use_deform_preserve_volume',
                            'use_multi_modifier', 'vertex_group', 'invert_vertex_group'):
                    item[key] = getattr(mod, key)
            data['modifiers'].append(item)
        if include_geometry:
            data['vertices'] = [list(v.co) for v in obj.data.vertices]
        if obj.data.shape_keys:
            for key in obj.data.shape_keys.key_blocks:
                item = {'name': key.name, 'relative_key': key.relative_key.name,
                        'value': key.value, 'mute': key.mute, 'vertex_group': key.vertex_group}
                if include_geometry:
                    item['coordinates'] = [list(v.co) for v in key.data]
                data['shape_keys'].append(item)
        meshes[obj.name] = data
    return {'schema_version': 1, 'blender_version': bpy.app.version_string,
            'armature': arm.name, 'matrix_world': _matrix(arm.matrix_world),
            'unit_scale': bpy.context.scene.unit_settings.scale_length,
            'humanoid': _humanoid(arm), 'bones': bones, 'meshes': meshes,
            'bone_collections': [{'name': c.name,
                                  'parent': c.parent.name if c.parent else None,
                                  'bones': [b.name for b in c.bones]}
                                 for c in arm.data.collections_all],
            'coverage': 'core snapshot; supplement normals, UV, animation, drivers and full MMD properties'}


def validate_plan(plan, snapshot):
    """Check sparse mapping names and the resulting parent graph.

    This is a structural preflight only, not geometric or conversion validation.
    Omitted source bones are preserved. Skip rows describe reference-only bones.
    This helper covers reuse/generate/preserve, not source deletion or weight merges.
    """
    errors = []
    if plan.get('schema_version') != 1:
        errors.append('Unsupported plan schema')
    if plan.get('source_armature') != snapshot['armature']:
        errors.append('Source armature mismatch')
    if plan.get('apose', {}).get('mode') not in ('bake_required', 'already_verified'):
        errors.append('Missing explicit A-pose decision')
    rows = plan.get('bones', [])
    seen, targets, parents = set(), set(), {}
    for row in rows:
        action, source, target = row.get('action'), row.get('source'), row.get('target')
        if action not in ('reuse', 'generate', 'preserve', 'skip'):
            errors.append(f'Invalid action: {action}')
            continue
        if not row.get('reason'):
            errors.append(f'Missing reason: {source or target}')
        if action == 'skip':
            if source in snapshot['bones']:
                errors.append(f'Cannot skip a source bone: {source}')
            continue
        if action in ('reuse', 'preserve'):
            if source not in snapshot['bones'] or source in seen:
                errors.append(f'Missing or duplicate source: {source}')
            seen.add(source)
        if not isinstance(target, str) or not target or target in targets:
            errors.append(f'Missing or duplicate target: {target}')
            continue
        targets.add(target)
        parents[target] = row.get('parent')
        if not all(row.get(key) for key in ('position_rule', 'axis_rule', 'weight_rule')):
            errors.append(f'Missing derivation rules: {target}')
    # Merge sparse body/control edits with the unchanged source hierarchy.
    mapping = {r.get('source'): r.get('target') for r in rows
               if r.get('action') in ('reuse', 'preserve')}
    for name, data in snapshot['bones'].items():
        if name not in seen:
            if name in targets:
                errors.append(f'Target collides with preserved source: {name}')
            targets.add(name)
            parent = data.get('parent')
            parents[name] = mapping.get(parent, parent)
    for target, parent in parents.items():
        if parent is not None and parent not in targets:
            errors.append(f'Unknown parent: {target} -> {parent}')
        visited, current = set(), target
        while current in parents:
            if current in visited:
                errors.append(f'Parent cycle: {target}')
                break
            visited.add(current)
            current = parents[current]
    return {'status': 'failed' if errors else 'passed', 'scope': 'plan_structure', 'errors': errors}


def validate_structure(armature_name, minimum_length, axis_tolerance):
    """Basic rest-axis and constraint checks only; never claims full success."""
    arm = _armature(armature_name)
    if not all(math.isfinite(v) and v > 0 for v in (minimum_length, axis_tolerance)):
        raise ValueError('Supply explicit finite positive tolerances in armature units')
    errors = []
    for bone in arm.data.bones:
        matrix = bone.matrix_local
        if not all(math.isfinite(v) for row in matrix for v in row):
            errors.append({'bone': bone.name, 'issue': 'nonfinite_rest'})
            continue
        basis = matrix.to_3x3()
        gram = basis.transposed() @ basis
        deviation = max(abs(gram[i][j] - (1 if i == j else 0)) for i in range(3) for j in range(3))
        if bone.length < minimum_length or deviation > axis_tolerance or abs(basis.determinant()-1) > axis_tolerance:
            errors.append({'bone': bone.name, 'issue': 'length_or_axes'})
        for constraint in arm.pose.bones[bone.name].constraints:
            if not constraint.is_valid:
                errors.append({'bone': bone.name, 'issue': 'invalid_constraint', 'constraint': constraint.name})
    return {'status': 'failed' if errors else 'passed', 'scope': 'structure_only', 'errors': errors,
            'unverified': ['rest_geometry', 'weights', 'shape_keys', 'ik_motion', 'deformation', 'pmx_roundtrip']}


def validation_summary(report, required_checks=None):
    """Summarize measured results without treating an incomplete run as a pass."""
    expected = tuple(required_checks) if required_checks is not None else (
        'rest_geometry', 'weights', 'shape_keys', 'structure', 'ik_motion',
        'deformation', 'bone_collections', 'pmx_roundtrip')
    if not expected or any(not isinstance(key, str) or not key for key in expected):
        raise ValueError('Supply nonempty named validation scopes')
    checks = report.get('checks', {})
    states = {key: checks.get(key, {}).get('status', 'unverified') for key in expected}
    return {'fully_passed': all(v == 'passed' for v in states.values()), 'checks': states}


def mmd_body_names(include_toe_ex=False, extra_required=(), *, include_eyes=True):
    """Pipeline humanoid profile, not a claim that PMX mandates these names."""
    names = ['全ての親', 'センター', 'グルーブ', '下半身', '上半身', '上半身2', '首', '頭', '両目']
    for side in ('左', '右'):
        names.extend(side + role for role in ('目', '肩', '腕', 'ひじ', '手首', '足', 'ひざ',
                                             '足首', 'つま先', '足IK親', '足ＩＫ', 'つま先ＩＫ'))
        for finger, digits in [('親指', '０１２'), ('人指', '１２３'), ('中指', '１２３'),
                               ('薬指', '１２３'), ('小指', '１２３')]:
            names.extend(side + finger + digit for digit in digits)
        if include_toe_ex:
            names.append(side + '足先EX')
    if not include_eyes:
        names = [name for name in names if name not in ('左目', '右目', '両目')]
    return list(dict.fromkeys(names + list(extra_required)))


def validate_mmd_body(armature_name, include_toe_ex=False, extra_required=(), *, include_eyes=True):
    """Check exported Japanese-name coverage and separate foot-chain roles.

    Baseline body/control profile plus task-selected extras. Reference costumes
    and physics are outside this profile. PMX roundtrip remains a separate check.
    """
    arm = _armature(armature_name)
    by_name = {}
    for pose in arm.pose.bones:
        japanese = getattr(getattr(pose, 'mmd_bone', None), 'name_j', '')
        if japanese:
            by_name.setdefault(japanese, []).append(pose.name)
    expected = mmd_body_names(include_toe_ex, extra_required, include_eyes=include_eyes)
    missing = [name for name in expected if name not in by_name]
    duplicates = {name: by_name[name] for name in expected if len(by_name.get(name, [])) > 1}
    issues = []
    for side in ('左', '右'):
        roles = [side + role for role in ('ひざ', '足首', 'つま先', '足ＩＫ', 'つま先ＩＫ')]
        if any(len(by_name.get(name, [])) != 1 for name in roles):
            continue
        knee, foot, tip, leg_control, toe_control = [arm.pose.bones[by_name[name][0]] for name in roles]
        for owner, target, count in ((knee, leg_control, 2), (foot, toe_control, 1)):
            found = [c for c in owner.constraints if c.type == 'IK' and not c.mute and c.influence > 0
                     and c.target == arm and c.subtarget == target.name and c.chain_count == count and c.use_tail]
            if len(found) != 1:
                issues.append({'side': side, 'issue': 'missing_or_duplicate_ik', 'bone': owner.name})
        if tip.parent != foot:
            issues.append({'side': side, 'issue': 'toe_endpoint_parent', 'bone': tip.name})
        if toe_control.parent != leg_control:
            issues.append({'side': side, 'issue': 'toe_control_parent', 'bone': toe_control.name})
        if include_toe_ex and len(by_name.get(side+'足先EX', [])) == 1:
            weighted_toe = arm.pose.bones[by_name[side+'足先EX'][0]]
            if weighted_toe.name in (tip.name, toe_control.name):
                issues.append({'side': side, 'issue': 'toe_roles_not_separate'})
    return {'status': 'failed' if missing or duplicates or issues else 'passed',
            'scope': 'mmd_humanoid_profile', 'required': expected,
            'missing': missing, 'duplicates': duplicates, 'foot_chain_issues': issues}


def validate_lower_body_direction(armature_name, reference_name, angle_tolerance_degrees=1.0):
    """Compare lower-body head-to-tail direction and transported roll with reference."""
    import bpy
    arm = _armature(armature_name)
    # The reference can belong to the original scene during isolated reimport.
    reference = bpy.data.objects.get(reference_name)
    if reference is None or reference.type != 'ARMATURE' or reference.mode == 'EDIT':
        raise ValueError('Expected reference armature outside Edit Mode: ' + reference_name)
    def resolve(obj):
        found = [p.bone for p in obj.pose.bones
                 if getattr(getattr(p, 'mmd_bone', None), 'name_j', '') == '下半身']
        if len(found) != 1:
            raise ValueError('Expected one 下半身 in ' + obj.name)
        return found[0]
    bone, rb = resolve(arm), resolve(reference)
    transform = arm.matrix_world.inverted().to_3x3() @ reference.matrix_world.to_3x3()
    direction = bone.tail_local-bone.head_local
    ref_direction = transform @ (rb.tail_local-rb.head_local)
    if direction.length < 1e-8 or ref_direction.length < 1e-8:
        return {'status': 'failed', 'issue': 'zero_lower_body_length'}
    direction.normalize()
    ref_direction.normalize()
    z_hint = ref_direction.rotation_difference(direction) @ (transform @ rb.matrix_local.to_3x3().col[2])
    z_hint -= direction*z_hint.dot(direction)
    z_hint.normalize()
    angle = math.degrees(direction.angle(ref_direction))
    roll = math.degrees(z_hint.angle(bone.matrix_local.to_3x3().col[2].normalized()))
    return {'status': 'passed' if max(angle, roll) <= angle_tolerance_degrees else 'failed',
            'scope': 'lower_body_direction', 'direction_error_degrees': angle,
            'roll_error_degrees': roll, 'head': list(bone.head_local), 'tail': list(bone.tail_local)}


def _torso_bones(armature_name):
    arm = _armature(armature_name)
    resolved = {}
    for name in ('上半身', '下半身'):
        found = [p.bone for p in arm.pose.bones
                 if getattr(getattr(p, 'mmd_bone', None), 'name_j', '') == name]
        if len(found) != 1:
            raise ValueError('Expected one ' + name + ' in ' + armature_name)
        resolved[name] = found[0]
    if min(b.length for b in resolved.values()) <= 1e-8:
        raise ValueError('Expected nonzero torso bone lengths')
    return resolved['上半身'], resolved['下半身']


def capture_torso_layout(armature_name):
    """Read an approved rest layout; positions are relative to upper-body rest and length."""
    upper, lower = _torso_bones(armature_name)
    inverse = upper.matrix_local.inverted()
    relative_axes = upper.matrix_local.to_3x3().inverted() @ lower.matrix_local.to_3x3()
    return {'schema_version': 1,
            'lower_head': list((inverse @ lower.head_local) / upper.length),
            'lower_tail': list((inverse @ lower.tail_local) / upper.length),
            'lower_axes': [list(row) for row in relative_axes]}


def validate_torso_layout(armature_name, layout, position_tolerance=None,
                          angle_tolerance_degrees=1.0):
    """Compare to a previously approved layout, without changing the rig or its parenting."""
    from mathutils import Vector, Matrix
    upper, lower = _torso_bones(armature_name)
    tolerance = upper.length * 1e-4 if position_tolerance is None else position_tolerance
    if not math.isfinite(tolerance) or tolerance < 0:
        raise ValueError('Expected finite nonnegative position tolerance')
    if not math.isfinite(angle_tolerance_degrees) or angle_tolerance_degrees < 0:
        raise ValueError('Expected finite nonnegative angle tolerance')
    expected_head = upper.matrix_local @ (Vector(layout['lower_head']) * upper.length)
    expected_tail = upper.matrix_local @ (Vector(layout['lower_tail']) * upper.length)
    expected_axes = upper.matrix_local.to_3x3() @ Matrix(layout['lower_axes'])
    actual_axes = lower.matrix_local.to_3x3()
    errors = {'head': (lower.head_local - expected_head).length,
              'tail': (lower.tail_local - expected_tail).length}
    angles = [math.degrees(expected_axes.col[i].angle(actual_axes.col[i])) for i in range(3)]
    passed = max(errors.values()) <= tolerance and max(angles) <= angle_tolerance_degrees
    return {'status': 'passed' if passed else 'failed', 'scope': 'approved_torso_layout',
            'position_errors': errors, 'axis_errors_degrees': angles,
            'position_tolerance': tolerance, 'angle_tolerance_degrees': angle_tolerance_degrees}
