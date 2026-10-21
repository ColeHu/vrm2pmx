"""Blender-only semantic A-pose capture/apply. No mesh baking or file saving."""
import math


def _arm(name):
    import bpy
    arm = bpy.context.scene.objects[name]
    if arm.type != "ARMATURE":
        raise ValueError("Expected armature")
    return arm


def _basis(arm, mapping, posed):
    from mathutils import Matrix
    def head(semantic):
        name = mapping[semantic]
        return (arm.pose.bones[name].head if posed else arm.data.bones[name].head_local).copy()
    right = head("leftUpperArm") - head("rightUpperArm")
    up = head("head") - head("hips")
    if right.length < 1e-8 or up.length < 1e-8:
        raise ValueError("Degenerate humanoid anchors")
    right.normalize()
    up -= right * up.dot(right)
    if up.length < 1e-8:
        raise ValueError("Degenerate character basis")
    up.normalize()
    forward = up.cross(right).normalized()
    return Matrix((right, forward, up)).transposed()


def _segment(arm, mapping, semantic, endpoint, posed):
    if posed:
        return arm.pose.bones[mapping[endpoint]].head - arm.pose.bones[mapping[semantic]].head
    return arm.data.bones[mapping[endpoint]].head_local - arm.data.bones[mapping[semantic]].head_local


def _palm(arm, mapping, side, posed):
    def head(semantic):
        return (arm.pose.bones[mapping[semantic]].head if posed else arm.data.bones[mapping[semantic]].head_local)
    root = head(side + "Hand")
    value = (head(side + "IndexProximal") - root).cross(head(side + "LittleProximal") - root)
    direction = head(side + "MiddleProximal") - root
    if direction.length < 1e-8:
        raise ValueError("Zero hand direction")
    direction.normalize()
    # Different hand anatomy need not share the same raw knuckle-plane angle.
    # Transfer roll around the hand direction rather than that intrinsic angle.
    value -= direction * value.dot(direction)
    if value.length < 1e-8:
        raise ValueError("Degenerate palm anchors")
    return value.normalized()


def capture_template(armature_name, mapping, *, posed=False, template_id="standard-apose-v1"):
    arm = _arm(armature_name)
    basis = _basis(arm, mapping, posed)
    inv = basis.transposed()
    segments = []
    for side in ("left", "right"):
        for part, end in (("UpperArm", "LowerArm"), ("LowerArm", "Hand"), ("Hand", "MiddleProximal")):
            semantic, endpoint = side + part, side + end
            direction = _segment(arm, mapping, semantic, endpoint, posed)
            if direction.length < 1e-8:
                raise ValueError("Zero segment: " + semantic)
            row = dict(semantic=semantic, endpoint=endpoint, direction=list(inv @ direction.normalized()))
            if part == "Hand":
                row["palm_normal"] = list(inv @ _palm(arm, mapping, side, posed))
            segments.append(row)
    return dict(schema_version=1, template_id=template_id, representation="semantic_directions",
                character_axes="X character left, Y up cross X, Z torso up",
                units="unit directions; retain target proportions and joint locations",
                scope="upper arms, lower arms and hands; torso/legs remain unchanged",
                segments=segments)


def apply_template(armature_name, mapping, template, *, alpha=1.0, work_copy=False, tolerance_degrees=0.5):
    import bpy
    from mathutils import Matrix, Quaternion, Vector
    arm = _arm(armature_name)
    if not work_copy or arm.data.users != 1 or bpy.context.mode != "OBJECT":
        raise ValueError("Explicit single-user work copy in Object Mode required")
    if not math.isfinite(alpha) or not 0 <= alpha <= 1:
        raise ValueError("alpha must be in [0,1]")
    if not math.isfinite(tolerance_degrees) or tolerance_degrees <= 0:
        raise ValueError("Positive angular tolerance required")
    if arm.animation_data or arm.data.pose_position != "POSE":
        raise ValueError("Isolate animation and use POSE first")
    if any(not c.mute and c.influence > 0 for p in arm.pose.bones for c in p.constraints):
        raise ValueError("Constraint-driven rig needs an evaluated strategy")
    if any(not p.bone.use_inherit_rotation or p.bone.inherit_scale != "FULL" for p in arm.pose.bones):
        raise ValueError("Nonstandard inheritance requires a separate adapter")
    scale = arm.matrix_world.to_scale()
    if min(scale) <= 0 or max(scale) - min(scale) > max(scale) * 1e-5 or arm.matrix_world.determinant() <= 0:
        raise ValueError("Normalize nonuniform/reflected object scale in work copy first")
    if any(max(abs(v - 1) for v in p.scale) > 1e-6 for p in arm.pose.bones):
        raise ValueError("Pose scale is unsupported")
    if template.get("representation") != "semantic_directions":
        raise ValueError("Unsupported template")
    rows = template["segments"]
    expected = [side + part for side in ("left", "right") for part in ("UpperArm", "LowerArm", "Hand")]
    if [r["semantic"] for r in rows] != expected:
        raise ValueError("Require parent-first upper/lower/hand segments per side")
    for row in rows:
        for key in (row["semantic"], row["endpoint"]):
            if key not in mapping or mapping[key] not in arm.data.bones:
                raise ValueError("Missing semantic mapping: " + key)
        if arm.data.bones[mapping[row["semantic"]]] not in arm.data.bones[mapping[row["endpoint"]]].parent_recursive:
            raise ValueError("Endpoint must be a descendant of segment bone")
        v = Vector(row["direction"])
        if len(v) != 3 or not all(math.isfinite(x) for x in v) or abs(v.length - 1) > 1e-5:
            raise ValueError("Invalid template direction")
        if "palm_normal" in row:
            side = "left" if row["semantic"].startswith("left") else "right"
            _palm(arm, mapping, side, True)
            n = Vector(row["palm_normal"])
            if len(n) != 3 or not all(math.isfinite(x) for x in n) or abs(n.length - 1) > 1e-5 or n.cross(v).length < 1e-5:
                raise ValueError("Invalid palm normal")
    bpy.context.view_layer.update()
    basis = _basis(arm, mapping, False)
    before = {p.name: p.matrix_basis.copy() for p in arm.pose.bones}
    lengths = {b.name: b.length for b in arm.data.bones}
    try:
        for row in rows:
            semantic, endpoint = row["semantic"], row["endpoint"]
            p = arm.pose.bones[mapping[semantic]]
            source = _segment(arm, mapping, semantic, endpoint, True)
            if source.length < 1e-8:
                raise ValueError("Zero source segment")
            desired = (basis @ Vector(row["direction"])).normalized()
            swing = source.normalized().rotation_difference(desired)
            rotation = swing
            if "palm_normal" in row:
                side = "left" if semantic.startswith("left") else "right"
                current_normal = swing @ _palm(arm, mapping, side, True)
                target_normal = basis @ Vector(row["palm_normal"])
                current_normal = current_normal - desired * current_normal.dot(desired)
                target_normal = target_normal - desired * target_normal.dot(desired)
                if min(current_normal.length, target_normal.length) < 1e-8:
                    raise ValueError("Degenerate projected palm normal")
                current_normal.normalize()
                target_normal.normalize()
                angle = math.atan2(desired.dot(current_normal.cross(target_normal)), current_normal.dot(target_normal))
                rotation = Quaternion(desired, angle) @ swing
            step = Quaternion((1, 0, 0, 0)).slerp(rotation, alpha)
            pivot = p.head.copy()
            p.matrix = Matrix.Translation(pivot) @ step.to_matrix().to_4x4() @ Matrix.Translation(-pivot) @ p.matrix
            bpy.context.view_layer.update()
        errors = []
        for row in rows:
            desired = basis @ Vector(row["direction"])
            actual = _segment(arm, mapping, row["semantic"], row["endpoint"], True)
            error = math.degrees(actual.angle(desired))
            record = dict(semantic=row["semantic"], direction_error_degrees=error)
            if "palm_normal" in row:
                side = "left" if row["semantic"].startswith("left") else "right"
                record["palm_error_degrees"] = math.degrees(_palm(arm, mapping, side, True).angle(basis @ Vector(row["palm_normal"])))
            errors.append(record)
        if any(abs(arm.data.bones[n].length - length) > 1e-7 for n, length in lengths.items()):
            raise RuntimeError("Rest bone length changed")
        maximum = max(max(r["direction_error_degrees"], r.get("palm_error_degrees", 0)) for r in errors)
        if alpha == 1 and maximum > tolerance_degrees:
            raise RuntimeError("Pose residual exceeds tolerance: " + str(maximum))
        return dict(status="passed" if alpha == 1 else "intermediate", template_id=template["template_id"],
                    alpha=alpha, max_angular_error_degrees=maximum, errors=errors,
                    rest_unchanged=True, mesh_baked=False, next_step="bake_apose then independent validation")
    except Exception:
        for name, matrix in before.items():
            arm.pose.bones[name].matrix_basis = matrix
        bpy.context.view_layer.update()
        raise
