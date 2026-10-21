"""Bake an already posed work-copy armature using verified ordinary LBS.

Blender + NumPy. Does not choose a pose, duplicate a model, or reorient bones.
Caller saves a checkpoint first. A failed post-commit check requires rollback.
"""


def bake_apose(armature_name, mesh_names, tolerance, require_all_meshes=True):
    import bpy
    import numpy as np
    from mathutils import Matrix
    if not np.isfinite(tolerance) or tolerance <= 0:
        raise ValueError('Supply a positive tolerance in object-space units')
    arm = bpy.context.scene.objects[armature_name]
    if arm.type != 'ARMATURE' or arm.mode != 'OBJECT' or arm.data.users != 1:
        raise ValueError('Expected single-user work-copy armature in Object Mode')
    if arm.data.pose_position != 'POSE' or arm.animation_data:
        raise ValueError('Isolate armature animation and use POSE before baking')
    if any(not c.mute and c.influence > 0 for p in arm.pose.bones for c in p.constraints):
        raise ValueError('Constraint-driven rest baking needs a separate evaluated strategy')
    bound = [o for o in bpy.context.scene.objects if o.type == 'MESH'
             and any(m.type == 'ARMATURE' and m.object == arm for m in o.modifiers)]
    names = list(mesh_names)
    if len(names) != len(set(names)) or not names:
        raise ValueError('Expected unique nonempty mesh names')
    if require_all_meshes and set(names) != {o.name for o in bound}:
        raise ValueError('Plan must include all bound meshes or explicitly waive coverage')
    objects = [bpy.context.scene.objects[name] for name in names]
    for obj in objects:
        if obj.type != 'MESH' or obj.data.users != 1 or obj.parent_type != 'OBJECT' or obj.animation_data:
            raise ValueError('Expected single-user static object-parented mesh: ' + obj.name)
        active = [m for m in obj.modifiers if m.show_viewport]
        if len(active) != 1 or active[0].type != 'ARMATURE' or active[0].object != arm:
            raise ValueError('Disable unrelated evaluated modifiers before baking: ' + obj.name)
        mod = active[0]
        if (mod.use_deform_preserve_volume or mod.use_bone_envelopes or not mod.use_vertex_groups
                or mod.use_multi_modifier or mod.vertex_group):
            raise ValueError('Only ordinary unrestricted vertex-group LBS is supported')
        keys = obj.data.shape_keys
        if keys and (keys.animation_data or not keys.use_relative):
            raise ValueError('Isolate animated keys; absolute shape keys need another strategy')
    # All unsupported inputs are rejected before mutation.
    bpy.context.view_layer.update()
    deps = bpy.context.evaluated_depsgraph_get()
    skin = {p.name: p.matrix @ p.bone.matrix_local.inverted() for p in arm.pose.bones}
    pending = []
    for obj in objects:
        n = len(obj.data.vertices)
        if n == 0:
            raise ValueError('Empty mesh: ' + obj.name)
        to_arm = arm.matrix_world.inverted() @ obj.matrix_world
        to_obj = to_arm.inverted()
        matrices = {g.index: np.asarray(to_obj @ skin[g.name] @ to_arm, dtype=np.float64)
                    for g in obj.vertex_groups if g.name in skin and arm.data.bones[g.name].use_deform}
        transforms = np.zeros((n, 4, 4), dtype=np.float64)
        for vertex in obj.data.vertices:
            groups = [(g.group, g.weight) for g in vertex.groups if g.group in matrices and g.weight > 0]
            total = sum(w for _, w in groups)
            if total:
                for index, weight in groups:
                    transforms[vertex.index] += matrices[index] * (weight / total)
            else:
                transforms[vertex.index] = np.eye(4)
        keys = list(obj.data.shape_keys.key_blocks) if obj.data.shape_keys else []
        blocks = [k.data for k in keys] if keys else [obj.data.vertices]
        coordinates = []
        for block in blocks:
            co = np.empty(n * 3, dtype=np.float32)
            block.foreach_get('co', co)
            co = co.reshape(n, 3)
            coordinates.append((np.einsum('nij,nj->ni', transforms[:, :3, :3], co)
                                + transforms[:, :3, 3]).astype(np.float32))
        evaluated = obj.evaluated_get(deps)
        me = evaluated.to_mesh(preserve_all_data_layers=True, depsgraph=deps)
        try:
            if len(me.vertices) != n or len(me.loops) != len(obj.data.loops):
                raise ValueError('Evaluated topology mismatch: ' + obj.name)
            target = np.asarray([v.co[:] for v in me.vertices])
            normals = [v.vector[:] for v in me.corner_normals]
        finally:
            evaluated.to_mesh_clear()
        pending.append((obj, blocks, coordinates, target, normals))
    selected = list(bpy.context.selected_objects)
    active_object = bpy.context.view_layer.objects.active
    committed = False
    try:
        for obj, blocks, coordinates, _, _ in pending:
            for block, co in zip(blocks, coordinates):
                block.foreach_set('co', co.ravel())
            if obj.data.shape_keys:
                obj.data.vertices.foreach_set('co', coordinates[0].ravel())
            obj.data.update()
        bpy.ops.object.select_all(action='DESELECT')
        arm.select_set(True)
        bpy.context.view_layer.objects.active = arm
        bpy.ops.object.mode_set(mode='POSE')
        bpy.ops.pose.armature_apply(selected=False)
        bpy.ops.object.mode_set(mode='OBJECT')
        committed = True
        bpy.context.view_layer.update()
        deps = bpy.context.evaluated_depsgraph_get()
        rows = []
        for obj, blocks, coordinates, target, normals in pending:
            obj.data.normals_split_custom_set(normals)
            obj.data.update()
            evaluated = obj.evaluated_get(deps)
            me = evaluated.to_mesh()
            try:
                actual = np.asarray([v.co[:] for v in me.vertices])
            finally:
                evaluated.to_mesh_clear()
            error = float(np.max(np.linalg.norm(actual - target, axis=1)))
            rows.append({'mesh': obj.name, 'blocks_baked': len(blocks), 'max_error': error})
        report = {'status': 'passed' if all(r['max_error'] <= tolerance for r in rows) else 'failed',
                  'scope': 'evaluated_pose_bake', 'tolerance': tolerance, 'meshes': rows,
                  'unverified': ['independent_all_key_audit', 'pmx_roundtrip']}
        if report['status'] != 'passed':
            raise RuntimeError('Post-bake geometry failed; restore checkpoint: ' + str(report))
        return report
    except Exception as exc:
        raise RuntimeError('Bake failed; restore the work-copy checkpoint. Rest committed=' + str(committed)) from exc
    finally:
        if arm.mode != 'OBJECT':
            bpy.context.view_layer.objects.active = arm
            bpy.ops.object.mode_set(mode='OBJECT')
        bpy.ops.object.select_all(action='DESELECT')
        for obj in selected:
            obj.select_set(True)
        bpy.context.view_layer.objects.active = active_object
