"""Read-only T/A-pose detection and idempotent A-pose preparation in Blender."""
import json
import math
from pathlib import Path
import runpy

ROOT = Path(__file__).resolve().parents[1]


def _api(name):
    return runpy.run_path(str(ROOT/'scripts'/name))


def _angle(a, b):
    def unit(v):
        if len(v) != 3 or not all(math.isfinite(x) for x in v):
            raise ValueError('Expected a finite direction vector')
        size = math.sqrt(sum(x*x for x in v))
        if size < 1e-8:
            raise ValueError('Zero direction vector')
        return [x/size for x in v]
    a, b = unit(a), unit(b)
    return math.degrees(math.acos(max(-1., min(1., sum(x*y for x,y in zip(a,b))))))


def classify_directions(captured, template, *, tolerance_degrees=.5):
    """Pure Python geometry classifier; thresholds label poses, not acceptance.

    X points to character left, Z up. A-pose is a broad arm-lowering family;
    matching the selected template additionally requires all segments and palms.
    """
    if not math.isfinite(tolerance_degrees) or tolerance_degrees <= 0:
        raise ValueError('Positive angular tolerance required')
    actual = {r['semantic']:r for r in captured['segments']}
    errors, sides, reasons = [], {}, []
    for row in template['segments']:
        source = actual[row['semantic']]
        if source['endpoint'] != row['endpoint']:
            raise ValueError('Template endpoint mismatch: '+row['semantic'])
        error = dict(semantic=row['semantic'],direction_error_degrees=_angle(source['direction'], row['direction']))
        if 'palm_normal' in row:
            error['palm_error_degrees'] = _angle(source['palm_normal'], row['palm_normal'])
        errors.append(error)
    for side, sign in (('left',1), ('right',-1)):
        measurements = {}
        for part in ('UpperArm','LowerArm'):
            v = actual[side+part]['direction']
            _angle(v, (sign,0,0))  # Validate before computing scalar diagnostics.
            outward = sign*v[0]
            drop = math.degrees(math.atan2(-v[2], math.hypot(v[0],v[1])))
            yaw = math.degrees(math.atan2(abs(v[1]), outward))
            measurements[part] = dict(drop_degrees=drop,out_of_plane_degrees=yaw)
            if outward <= 0 or not -15 <= drop <= 75 or yaw > 35:
                reasons.append(side+part+': outside automatic T/A-pose envelope')
        elbow = _angle(actual[side+'UpperArm']['direction'],actual[side+'LowerArm']['direction'])
        if elbow > 45:
            reasons.append(side+': strongly bent elbow requires adaptation')
        drops = [measurements[p]['drop_degrees'] for p in ('UpperArm','LowerArm')]
        label = ('t_pose' if all(abs(v)<=10 for v in drops) else
                 'a_pose' if all(15<=v<=65 for v in drops) else 'intermediate')
        sides[side] = dict(classification=label,segments=measurements,elbow_angle_degrees=elbow)
    maximum = max(max(r['direction_error_degrees'],r.get('palm_error_degrees',0)) for r in errors)
    matched = maximum <= tolerance_degrees
    asymmetric = (sides['left']['classification'] != sides['right']['classification'] or
                  any(abs(sides['left']['segments'][part]['drop_degrees']-
                          sides['right']['segments'][part]['drop_degrees'])>15 for part in ('UpperArm','LowerArm')))
    label = ('unsupported' if reasons else 'a_pose' if matched else 'asymmetric' if asymmetric else
             sides['left']['classification'])
    return dict(schema_version=1,status='needs_adaptation' if reasons else 'passed',
                classification=label,sides=sides,template_id=template['template_id'],
                matches_target_apose=matched,max_angular_error_degrees=maximum,
                tolerance_degrees=tolerance_degrees,errors=errors,reasons=reasons,
                recommended_action='adapt' if reasons else 'keep' if matched else 'normalize_to_apose')


def detect_pose(armature_name, mapping, *, template=None, posed=False, tolerance_degrees=.5):
    """Inspect Rest by default; posed=True is an explicit current-Pose diagnostic.

    Does not alter selection, Pose, Rest, meshes or scene. Mapping uses actual
    VRM Humanoid semantic names, never assumed internal bone names or tails.
    """
    if template is None:
        template = json.loads((ROOT/'references/standard-apose.json').read_text(encoding='utf-8'))
    captured = _api('apose_template.py')['capture_template'](armature_name,mapping,posed=posed)
    result = classify_directions(captured,template,tolerance_degrees=tolerance_degrees)
    result.update(armature=armature_name,space='pose' if posed else 'rest')
    return result


def normalize_apose(armature_name, mesh_names, mapping, *, work_copy=False,
                    template=None, tolerance_degrees=.5, bake_tolerance=1e-5):
    """Detect, keep canonical A unchanged, otherwise apply absolute target and bake.

    Requires neutral, unconstrained single-user work data. On a post-commit bake
    failure the caller restores/discards its isolated work-copy checkpoint.
    """
    import bpy
    arm = bpy.context.scene.objects[armature_name]
    names = list(mesh_names)
    if not work_copy or bpy.context.mode != 'OBJECT' or arm.data.users != 1:
        raise ValueError('Explicit single-user work armature in Object Mode required')
    if not names or len(names)!=len(set(names)) or any(bpy.context.scene.objects[n].data.users!=1 for n in names):
        raise ValueError('Explicit unique single-user work meshes required')
    if arm.data.pose_position != 'POSE' or arm.animation_data or any(not c.mute and c.influence>0 for p in arm.pose.bones for c in p.constraints):
        raise ValueError('Isolate animation/constraints before pose normalization')
    neutral_error = max(abs(p.matrix_basis[i][j]-(1 if i==j else 0))
                        for p in arm.pose.bones for i in range(4) for j in range(4))
    if neutral_error > 1e-5:
        raise ValueError('Non-neutral Pose is not a Rest T/A-pose; prepare an evaluated adapter first')
    if template is None:
        template = json.loads((ROOT/'references/standard-apose.json').read_text(encoding='utf-8'))
    before = detect_pose(armature_name,mapping,template=template,tolerance_degrees=tolerance_degrees)
    if before['status'] != 'passed':
        raise ValueError('Pose requires adaptation: '+json.dumps(before,ensure_ascii=False))
    if before['matches_target_apose']:
        return dict(status='passed',action='kept_existing_apose',detection_before=before,
                    detection_after=before,pose_applied=False,
                    bake=dict(status='not_needed',reason='Rest already matches target A-pose'))
    applied = _api('apose_template.py')['apply_template'](
        armature_name,mapping,template,work_copy=True,tolerance_degrees=tolerance_degrees)
    bake = _api('apose_bake.py')['bake_apose'](armature_name,names,bake_tolerance)
    after = detect_pose(armature_name,mapping,template=template,tolerance_degrees=tolerance_degrees)
    if not after['matches_target_apose']:
        raise RuntimeError('Baked Rest is not target A-pose; restore work-copy checkpoint')
    return dict(status='passed',action='normalized_to_apose',detection_before=before,
                detection_after=after,pose_applied=True,application=applied,bake=bake)
