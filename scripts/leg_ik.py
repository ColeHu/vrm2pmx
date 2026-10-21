"""Blender MCP helpers: inspect actual knee limits and scan foot IK motion.

All names and test offsets come from the caller's scene inspection/plan.
Sweeps restore pose channels on exit; no bone data or constraints are changed.
"""
import math


def knee_settings(armature_name, knee_names):
    import bpy
    arm = bpy.data.objects[armature_name]
    rows = {}
    for name in knee_names:
        pose = arm.pose.bones[name]
        rows[name] = {
            'rest_matrix': [list(row) for row in pose.bone.matrix_local],
            'ik_limits': {axis: {'enabled': getattr(pose, 'use_ik_limit_' + axis),
                                'locked': getattr(pose, 'lock_ik_' + axis),
                                'min': getattr(pose, 'ik_min_' + axis),
                                'max': getattr(pose, 'ik_max_' + axis)} for axis in 'xyz'},
            'fixed_axis_enabled': getattr(getattr(pose, 'mmd_bone', None), 'enabled_fixed_axis', None),
            'constraints': []}
        for constraint in pose.constraints:
            item = {'name': constraint.name, 'type': constraint.type,
                    'mute': constraint.mute, 'influence': constraint.influence}
            if constraint.type == 'IK':
                item.update(target=getattr(constraint.target, 'name', None),
                            subtarget=constraint.subtarget, chain_count=constraint.chain_count,
                            pole_target=getattr(constraint.pole_target, 'name', None),
                            pole_subtarget=constraint.pole_subtarget,
                            use_stretch=constraint.use_stretch)
            elif constraint.type == 'LIMIT_ROTATION':
                item['owner_space'] = constraint.owner_space
                item['limits'] = {a: {'enabled': getattr(constraint, 'use_limit_'+a),
                                      'min': getattr(constraint, 'min_'+a),
                                      'max': getattr(constraint, 'max_'+a)} for a in 'xyz'}
            rows[name]['constraints'].append(item)
    return rows


def _off_hinge_degrees(quaternion, axis):
    """Rotation distance from a rotation purely about the given local axis."""
    from mathutils import Quaternion
    q = quaternion.normalized()
    values = [q.w, 0, 0, 0]
    index = 'XYZ'.index(axis.upper()) + 1
    values[index] = q[index]
    magnitude = math.sqrt(sum(v*v for v in values))
    if magnitude < 1e-12:
        return 180.0
    twist = Quaternion([v/magnitude for v in values])
    return math.degrees(2*math.acos(min(1.0, abs(q.dot(twist)))))


def sweep_leg(armature_name, thigh_name, knee_name, control_name, offsets, hinge_axis='X',
              foot_name=None, toe_control_name=None, bend_forward=None):
    """Scan explicit armature-space XYZ translation offsets from zero-basis pose.

    Include a continuous ascending AND descending sequence and forward/side paths.
    Values are measured in armature units. Off-hinge angle measures knee local
    motion, not skin quality or the thigh's allowed orientation. No pass threshold
    is imposed here. Animated rigs require isolation of animation/drivers first.
    """
    import bpy
    from mathutils import Matrix, Vector
    arm = bpy.data.objects[armature_name]
    if arm.type != 'ARMATURE' or arm.mode == 'EDIT':
        raise ValueError('Select an armature outside Edit Mode')
    offsets = [tuple(v) for v in offsets]
    if any(len(v) != 3 or not all(math.isfinite(x) for x in v) for v in offsets):
        raise ValueError('Offsets must be finite armature-space XYZ vectors')
    saved = {p.name: {'location': p.location.copy(), 'scale': p.scale.copy(),
                     'rotation_euler': p.rotation_euler.copy(),
                     'rotation_quaternion': p.rotation_quaternion.copy(),
                     'rotation_axis_angle': tuple(p.rotation_axis_angle)} for p in arm.pose.bones}
    rows = []
    bend_forward = Vector(bend_forward).normalized() if bend_forward is not None else None

    def update():
        arm.update_tag()
        bpy.context.view_layer.update()
        return arm.evaluated_get(bpy.context.evaluated_depsgraph_get())

    try:
        for pose in arm.pose.bones:
            pose.matrix_basis = Matrix.Identity(4)
        baseline = update()
        baseline_foot = baseline.pose.bones[foot_name].matrix.copy() if foot_name else None
        baseline_axis = ((baseline.pose.bones[foot_name].tail-baseline.pose.bones[foot_name].head).normalized()
                         if foot_name else None)
        control = arm.pose.bones[control_name]
        baseline_head = control.head.copy()
        # convert_local_to_pose also handles nonstandard inherit-scale options.
        for offset in offsets:
            desired = control.bone.matrix_local.copy()
            desired.translation += Vector(offset)
            kwargs = {}
            if control.parent:
                kwargs = {'parent_matrix': control.parent.matrix,
                          'parent_matrix_local': control.parent.bone.matrix_local}
            control.matrix_basis = control.bone.convert_local_to_pose(
                desired, control.bone.matrix_local, invert=True, **kwargs)
            evaluated = update()
            knee = evaluated.pose.bones[knee_name]
            thigh = evaluated.pose.bones[thigh_name]
            goal = evaluated.pose.bones[control_name]
            kwargs = {}
            if knee.parent:
                kwargs = {'parent_matrix': knee.parent.matrix,
                          'parent_matrix_local': knee.parent.bone.matrix_local}
            local = knee.bone.convert_local_to_pose(
                knee.matrix, knee.bone.matrix_local, invert=True, **kwargs).to_quaternion()
            axis = goal.head-thigh.head
            projected = thigh.head if axis.length_squared < 1e-12 else (
                thigh.head + axis*((knee.head-thigh.head).dot(axis)/axis.length_squared))
            foot_metrics = {}
            if bend_forward is not None:
                bend = knee.head-projected
                foot_metrics['knee_forward_offset'] = bend.dot(bend_forward)
                foot_metrics['knee_forward_fraction'] = bend.dot(bend_forward)/bend.length if bend.length > 1e-8 else 0
            if foot_name:
                foot = evaluated.pose.bones[foot_name]
                current_axis = (foot.tail-foot.head).normalized()
                swing = baseline_axis.rotation_difference(current_axis)
                rotation = foot.matrix.to_quaternion() @ baseline_foot.to_quaternion().conjugated()
                residual = swing.conjugated() @ rotation
                foot_metrics['foot_twist_degrees'] = math.degrees(residual.angle)
                if foot_metrics['foot_twist_degrees'] > 180:
                    foot_metrics['foot_twist_degrees'] = 360-foot_metrics['foot_twist_degrees']
                signed_twist = math.degrees(2*math.atan2(
                    Vector((residual.x, residual.y, residual.z)).dot(baseline_axis), residual.w))
                foot_metrics['signed_foot_twist_degrees'] = (signed_twist+180)%360-180
                foot_metrics['foot_direction'] = list(current_axis)
                foot_metrics['foot_rotation_wxyz'] = list(rotation)
                if toe_control_name:
                    foot_metrics['toe_target_error'] = (foot.tail-evaluated.pose.bones[toe_control_name].head).length
            rows.append({'offset': list(offset), 'actual_control_displacement': list(goal.head-baseline_head),
                         'ankle_error': (knee.tail-goal.head).length,
                         'off_hinge_degrees': _off_hinge_degrees(local, hinge_axis),
                         'knee_rotation_wxyz': list(local),
                         'knee_projection_offset': list(knee.head-projected),
                         'knee_position': list(knee.head), **foot_metrics})
    finally:
        for name, channels in saved.items():
            pose = arm.pose.bones[name]
            for key, value in channels.items():
                setattr(pose, key, value)
        update()
    return {'armature': armature_name, 'knee': knee_name, 'control': control_name,
            'hinge_axis': hinge_axis, 'units': 'armature_local', 'samples': rows,
            'max_off_hinge_degrees': max((r['off_hinge_degrees'] for r in rows), default=0),
            'max_ankle_error': max((r['ankle_error'] for r in rows), default=0),
            'max_foot_twist_degrees': max((r.get('foot_twist_degrees', 0) for r in rows), default=0),
            'foot_twist_definition': 'axial rotation beyond shortest swing from evaluated neutral foot'}
