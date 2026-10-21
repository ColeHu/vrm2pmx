"""Schedule toe deformation after the leg and toe IK solvers in PMX."""


def _plan(arm):
    roles = {p.mmd_bone.name_j: p for p in arm.pose.bones
             if not p.is_mmd_shadow_bone}
    planned = {}
    for side in ('左', '右'):
        ex, ankle, leg_ik, toe_ik = [roles[side + role] for role in
                                    ('足先EX', '足首', '足ＩＫ', 'つま先ＩＫ')]
        if ex.parent != ankle:
            raise ValueError('Toe EX must be parented to its ankle: ' + ex.name)
        dependencies = (ankle, leg_ik, toe_ik)
        # Strictly later layers avoid depending on exported bone indices.
        order = max(ex.mmd_bone.transform_order,
                    max(p.mmd_bone.transform_order for p in dependencies) + 1)
        after = ex.mmd_bone.transform_after_dynamics or any(
            p.mmd_bone.transform_after_dynamics for p in dependencies)
        planned[ex.name] = (order, after)
        pending = list(ex.children)
        while pending:
            child = pending.pop(0)
            parent_order, parent_after = planned[child.parent.name]
            planned[child.name] = (
                max(child.mmd_bone.transform_order, parent_order + 1),
                child.mmd_bone.transform_after_dynamics or parent_after)
            pending.extend(child.children)
    return planned


def validate(armature_name):
    import bpy
    arm = bpy.data.objects[armature_name]
    try:
        plan = _plan(arm)
    except (KeyError, ValueError) as exc:
        return dict(status='failed', errors=[str(exc)])
    errors = []
    for name, (order, after) in plan.items():
        bone = arm.pose.bones[name].mmd_bone
        if bone.transform_order != order or bone.transform_after_dynamics != after:
            errors.append(dict(bone=name, actual_order=bone.transform_order,
                               required_order=order, required_after_physics=after))
    return dict(status='failed' if errors else 'passed', errors=errors,
                schedule={name:dict(order=order, after_physics=after)
                          for name, (order, after) in plan.items()})


def repair(armature_name, *, work_copy=False):
    import bpy
    arm = bpy.data.objects[armature_name]
    if not work_copy or not arm.get('auto_rig_version'):
        raise ValueError('Use an existing prepared work copy')
    plan = _plan(arm)
    changed = []
    for name, (order, after) in plan.items():
        bone = arm.pose.bones[name].mmd_bone
        if (bone.transform_order, bone.transform_after_dynamics) != (order, after):
            changed.append(dict(bone=name, previous_order=bone.transform_order,
                                order=order, after_physics=after))
            bone.transform_order = order
            bone.transform_after_dynamics = after
    # The exporter reads evaluated pose properties; ID properties alone may
    # leave an existing evaluated armature cached with the previous schedule.
    arm.update_tag()
    bpy.context.view_layer.update()
    return dict(validate(armature_name), changed=changed)
