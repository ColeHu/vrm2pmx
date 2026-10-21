"""Check foot geometry as well as names. Run inside Blender via MCP.

Default profile: Z-up character, horizontal heelward foot IK, ankle-to-toe foot segment,
EX retains the toe articulation and its tail extends slightly beyond the markers.
Tolerances use armature units; reference translation is ignored.
"""
import math


def _roles(arm):
    rows = {}
    for pose in arm.pose.bones:
        name = getattr(getattr(pose, 'mmd_bone', None), 'name_j', '')
        if name:
            rows.setdefault(name, []).append(pose)
    return rows


def validate_foot_layout(armature_name, reference_name=None, position_tolerance=None,
                         angle_tolerance_degrees=1.0, ground_z=0.0, require_geometry=True):
    import bpy
    from mathutils import Vector
    arm = bpy.data.objects[armature_name]
    if arm.type != 'ARMATURE' or arm.mode == 'EDIT':
        raise ValueError('Read an armature outside Edit Mode')
    roles = _roles(arm)
    ref = bpy.data.objects[reference_name] if reference_name else None
    ref_roles = _roles(ref) if ref else {}
    up = Vector((0, 0, 1))
    angle_cos = math.cos(math.radians(angle_tolerance_degrees))
    issues, metrics = [], {}
    for side in ('左', '右'):
        required = [side+role for role in ('足首', 'つま先', '足IK親', '足ＩＫ', 'つま先ＩＫ')]
        bad = [name for name in required if len(roles.get(name, [])) != 1]
        if bad:
            issues.append({'side': side, 'issue': 'missing_or_duplicate_roles', 'names': bad})
            continue
        foot, tip, parent, ik, toe_ik = [roles[name][0].bone for name in required]
        scale = max(foot.length, ik.length, parent.length, 1e-6)
        tol = position_tolerance if position_tolerance is not None else scale*1e-4
        if not math.isfinite(tol) or tol <= 0:
            raise ValueError('Use a positive finite position tolerance')
        side_metrics = {
            'parent_tail_to_ik_head': (parent.tail_local-ik.head_local).length,
            'ik_head_to_ankle': (ik.head_local-foot.head_local).length,
            'foot_tail_to_tip_head': (foot.tail_local-tip.head_local).length,
            'tip_head_to_toe_ik_head': (tip.head_local-toe_ik.head_local).length,
            'parent_ground_error': abs(parent.head_local.z-ground_z),
            'ik_height_difference': abs(ik.tail_local.z-ik.head_local.z),
        }
        for key, value in side_metrics.items():
            if value > tol:
                issues.append({'side': side, 'issue': key, 'error': value, 'tolerance': tol})
        if ik.parent != parent or toe_ik.parent != ik or tip.parent != foot:
            issues.append({'side': side, 'issue': 'foot_hierarchy'})
        for label, bone, sign in [('toe_tip_direction', tip, 1), ('toe_ik_direction', toe_ik, -1)]:
            vector = bone.tail_local-bone.head_local
            if vector.length < tol or vector.normalized().dot(up)*sign < angle_cos:
                issues.append({'side': side, 'issue': label})
        if foot.length < tol or ik.length < tol:
            issues.append({'side': side, 'issue': 'zero_length_foot_or_control'})
        # Keep the IK origin at the ankle; point its display segment toward
        # the heel, opposite to the horizontal ankle-to-toe direction.
        foot_forward = tip.head_local-foot.head_local
        foot_forward.z = 0
        ik_direction = ik.tail_local-ik.head_local
        ik_direction.z = 0
        if foot_forward.length <= tol or ik_direction.length <= tol:
            issues.append({'side': side, 'issue': 'heel_direction_unverified'})
        else:
            heel_angle = math.degrees(ik_direction.angle(-foot_forward))
            side_metrics['ik_heel_direction_error_degrees'] = heel_angle
            if heel_angle > angle_tolerance_degrees:
                issues.append({'side': side, 'issue': 'ik_not_pointing_toward_heel',
                               'degrees': heel_angle})
        # EX rotates from the toe joint; its tail reaches beyond both markers.
        weighted = roles.get(side+'足先EX', [])
        if len(weighted) == 1:
            ex = weighted[0].bone
            direction = ex.tail_local-ex.head_local
            forward_hint = tip.head_local-foot.head_local
            forward_hint.z = 0
            side_metrics['toe_ex_height_difference'] = abs(direction.z)
            side_metrics['toe_ex_head_to_tip_head'] = (ex.head_local-tip.head_local).length
            side_metrics['toe_ex_head_to_toe_ik_head'] = (ex.head_local-toe_ik.head_local).length
            if forward_hint.length>tol:
                axis=forward_hint.normalized()
                span=max((b.head_local-ex.head_local).dot(axis) for b in (tip,toe_ik))
                extension=(ex.tail_local-ex.head_local).dot(axis)-span
                expected_extension=max(max(span,0.)*.05,forward_hint.length*.01)
                side_metrics['toe_ex_tail_beyond_markers']=extension
                if abs(extension-expected_extension)>tol:
                    issues.append({'side':side,'issue':'toe_ex_tail_extension',
                                   'extension':extension,'expected':expected_extension})
            checkpoint = arm.get('auto_rig_toe_pivots')
            if checkpoint:
                import json
                expected = Vector(json.loads(checkpoint)[side])
                side_metrics['toe_ex_checkpoint_error'] = (ex.head_local-expected).length
                if side_metrics['toe_ex_checkpoint_error'] > tol:
                    issues.append({'side': side, 'issue':'toe_ex_checkpoint_mismatch',
                                   'error':side_metrics['toe_ex_checkpoint_error']})
            if abs(direction.z) > tol:
                issues.append({'side': side, 'issue': 'toe_ex_not_horizontal'})
            if direction.length <= tol or forward_hint.length <= tol or direction.normalized().dot(forward_hint.normalized()) < angle_cos:
                issues.append({'side': side, 'issue': 'toe_ex_not_pointing_forward'})
        # Names and coincident endpoints alone can still describe a rig which
        # stops in the middle of the foot. Check against weighted toe geometry.
        weighted_name = weighted[0].name if len(weighted) == 1 else roles[side+'つま先'][0].name
        candidates = [weighted_name, roles[side+'足首'][0].name]
        forward = tip.head_local-foot.head_local
        forward.z = 0
        distances = []
        if forward.length > tol:
            forward.normalize()
            for group_name in candidates:
                for obj in bpy.context.scene.objects:
                    if obj.type != 'MESH' or not any(m.type == 'ARMATURE' and m.object == arm for m in obj.modifiers):
                        continue
                    group = obj.vertex_groups.get(group_name)
                    if group is None:
                        continue
                    transform = arm.matrix_world.inverted() @ obj.matrix_world
                    distances.extend((transform @ v.co-foot.head_local).dot(forward)
                                     for v in obj.data.vertices
                                     if any(g.group == group.index and g.weight > .25 for g in v.groups))
                if distances:
                    break
        side_metrics['geometry_checked'] = bool(distances)
        if distances:
            distances.sort()
            front = distances[round(.995*(len(distances)-1))]
            tip_projection = (tip.head_local-foot.head_local).dot(forward)
            side_metrics['toe_geometry_front_error'] = abs(front-tip_projection)
            if abs(front-tip_projection) > max(tol, abs(front)*.05):
                issues.append({'side': side, 'issue': 'tip_not_at_foot_front',
                               'front': front, 'tip_projection': tip_projection})
        elif require_geometry:
            issues.append({'side': side, 'issue': 'foot_geometry_unverified'})
        if ref:
            transform = arm.matrix_world.inverted().to_3x3() @ ref.matrix_world.to_3x3()
            differences = {}
            for label, bone in [('足IK親', parent), ('足ＩＫ', ik), ('つま先', tip), ('つま先ＩＫ', toe_ik)]:
                candidates = ref_roles.get(side+label, [])
                if len(candidates) != 1:
                    issues.append({'side': side, 'issue': 'reference_role_missing', 'role': label})
                    continue
                rb = candidates[0].bone
                direction = bone.tail_local-bone.head_local
                reference_direction = transform @ (rb.tail_local-rb.head_local)
                if not direction.length or not reference_direction.length:
                    issues.append({'side': side, 'issue': 'zero_reference_direction', 'role': label})
                    continue
                angle = math.degrees(direction.angle(reference_direction))
                differences[label] = angle
                if angle > angle_tolerance_degrees:
                    issues.append({'side': side, 'issue': 'reference_direction', 'role': label, 'degrees': angle})
            side_metrics['reference_direction_degrees'] = differences
        metrics[side] = side_metrics
    return {'status': 'failed' if issues else 'passed', 'scope': 'foot_positions_and_relations',
            'issues': issues, 'metrics': metrics}
