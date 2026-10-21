"""SemiStandardBones-style arm/wrist twist, with conservative skin transfer.

Local plugin evidence and deliberate differences are in references/arm-twist.md.
No plugin binaries are loaded by this portable implementation.
"""
import json
import math
import re
import unicodedata
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]


def _name(arm, role):
    if role in arm.data.bones:return role
    matches=[p.name for p in arm.pose.bones if not p.is_mmd_shadow_bone and p.mmd_bone.name_j==role]
    return matches[0] if len(matches)==1 else role


def display_plan(segments):
    """Uniform Z-up helpers; main tails coincide with downstream joint heads."""
    profile=json.loads((ROOT/'references/arm-twist-display.json').read_text(encoding='utf-8'))
    upper=[s['layout']['length'] for s in segments if s['name'].endswith('腕捩')]
    if len(upper)!=2:
        raise ValueError('Display layout requires both upper-arm segments')
    helper_length=sum(upper)/2*profile['helper_length_to_mean_upper_arm']
    for spec in segments:
        layout=spec['layout']
        layout['main_tail']=list(spec['distal_head'])
        layout['helper_tails']=[[h[0],h[1],h[2]+helper_length] for h in layout['helpers']]
    return dict(profile_id=profile['profile_id'],helper_length=helper_length,
                helper_direction_character=profile['helper_direction_character'])


def configure_display(arm):
    """PMX BONE links for mains, OFFSET links for upright hidden helpers."""
    for spec in specifications():
        for suffix in ('', '1', '2', '3'):
            p = arm.pose.bones[_name(arm, spec['name'] + suffix)]
            mm = p.mmd_bone
            mm.enabled_local_axes = not bool(suffix)
            if not suffix:
                mm.local_axis_x = p.bone.matrix_local.col[0].to_3d().xzy
                mm.local_axis_z = p.bone.matrix_local.col[2].to_3d().xzy
            p.lock_rotation = (False, False, False) if suffix else (True, False, True)
            mm.display_connection_type = 'OFFSET' if suffix else 'BONE'
            mm.display_connection_bone_id = -1 if suffix else arm.pose.bones[_name(arm, spec['distal'])].mmd_bone.bone_id


def configure_preview(arm):
    """Fractional native rotation via quaternion constraint influence.

    Scaling Euler channels in an upright helper's different rest basis skews
    axial twist. Keep full native rotation mapping, then interpolate its result.
    Actual PMX append flags/source/fraction remain the export source of truth.
    Resync after native rebuilds or editing append fractions.
    """
    for spec in specifications():
        for i in (1, 2, 3):
            p = arm.pose.bones[_name(arm, spec['name'] + str(i))]
            c = p.constraints.get('mmd_additional_rotation')
            if c and c.type == 'TRANSFORM':
                for prefix in ('from', 'to'):
                    for (bound, sign) in (('min', -1), ('max', 1)):
                        for axis in 'xyz':
                            setattr(c, f'{prefix}_{bound}_{axis}_rot', sign * math.pi)
                c.influence = p.mmd_bone.additional_transform_influence


def specifications():
    return [dict(name=side+kind, parent=side+parent, distal=side+distal,
                 english=english+suffix)
            for side,suffix in (('左','_L'),('右','_R'))
            for kind,parent,distal,english in (
                ('腕捩','腕','ひじ','arm twist'), ('手捩','ひじ','手首','wrist twist'))]


def required_names():
    return [s['name']+suffix for s in specifications() for suffix in ('','1','2','3')]


def check_conflicts(arm):
    """Existing deform twist systems need an adapter, never duplicate or erase."""
    expected=set(required_names())
    conflicts=[]
    for p in arm.pose.bones:
        for name in (p.name,p.mmd_bone.name_j):
            normalized=re.sub(r'\.\d+$','',unicodedata.normalize('NFKC',name))
            if normalized in expected:
                conflicts.append(p.name)
                break
    if conflicts:
        raise ValueError('Existing arm twist bones require a reuse/weight adapter: '+', '.join(conflicts))


def compute_layout(start, end, dominated_points=()):
    """Plugin's 60% pivot, initial 12%..92% span expanded by dominant skin."""
    if any(len(v)!=3 or not all(math.isfinite(x) for x in v) for v in (start,end)):
        raise ValueError('Twist segment needs finite three-dimensional joint positions')
    delta=[b-a for a,b in zip(start,end)]
    length=math.sqrt(sum(v*v for v in delta))
    if length<1e-6:
        raise ValueError('Collapsed arm twist segment')
    axis=[v/length for v in delta]
    pivot=[a+.6*d for a,d in zip(start,delta)]
    lo,hi=-.48*length,.32*length
    for point in dominated_points:
        if len(point)!=3 or not all(math.isfinite(x) for x in point):
            raise ValueError('Nonfinite dominant skin point')
        t=sum((v-p)*a for v,p,a in zip(point,pivot,axis))
        lo=min(lo,t);hi=max(hi,t)
    return dict(pivot=pivot,axis=axis,length=length,minimum=lo,maximum=hi,
                helpers=[[p+a*(lo+(hi-lo)*fraction) for p,a in zip(pivot,axis)]
                         for fraction in (.25,.5,.75)])


def transfer_weights(weights, point, spec, layout):
    """Only redistribute the source segment's share; retain other influences."""
    source,distal,name=spec['parent'],spec['distal'],spec['name']
    weight=weights.get(source,0)
    if weight<=0:
        return dict(weights)
    total=sum(weights.values())
    projection=sum((v-p)*a for v,p,a in zip(point,layout['pivot'],layout['axis']))
    result=dict(weights)
    if weight/total>.97:
        t=max(0,min(4,4*(projection-layout['minimum'])/(layout['maximum']-layout['minimum'])))
        index=min(3,int(t));fraction=t-index
        chain=[source,name+'1',name+'2',name+'3',name]
        del result[source]
        for target,value in ((chain[index],weight*(1-fraction)),(chain[index+1],weight*fraction)):
            if value>1e-8:
                result[target]=result.get(target,0)+value
    elif weights.get(distal,0)>0 and projection>0:
        del result[source]
        result[name]=result.get(name,0)+weight
    result={n:w for n,w in result.items() if w>0}
    if len(result)>4 and len(result)>len(weights):
        raise ValueError('Twist split would exceed four PMX skin influences; adapt this vertex first')
    return result


def _skin(obj, deform_names):
    names={g.index:g.name for g in obj.vertex_groups if g.name in deform_names}
    return [{names[g.group]:g.weight for g in v.groups if g.group in names and g.weight>0}
            for v in obj.data.vertices]


def order_bones(arm):
    """Put parent/append dependencies first, remapping ID-backed references."""
    poses=list(arm.pose.bones)
    by_id={p.mmd_bone.bone_id:p.name for p in poses}
    append={p.name:p.mmd_bone.additional_transform_bone for p in poses}
    display={p.name:by_id.get(p.mmd_bone.display_connection_bone_id) for p in poses}
    ordered=[];visiting=set();visited=set()
    def visit(name):
        if name in visited:return
        if name in visiting:raise ValueError('Cyclic twist/parent/append dependency: '+name)
        visiting.add(name);p=arm.pose.bones[name]
        if p.parent:visit(p.parent.name)
        mm=p.mmd_bone
        if (mm.has_additional_rotation or mm.has_additional_location) and append[name]:
            visit(append[name])
        visiting.remove(name);visited.add(name);ordered.append(name)
    for p in sorted(poses,key=lambda p:p.mmd_bone.bone_id):visit(p.name)
    for index,name in enumerate(ordered):arm.pose.bones[name].mmd_bone.bone_id=index
    for p in poses:
        if append[p.name]:p.mmd_bone.additional_transform_bone=append[p.name]
        if display[p.name]:
            p.mmd_bone.display_connection_bone_id=arm.pose.bones[display[p.name]].mmd_bone.bone_id


def build(arm, meshes, *, work_copy=False):
    """Run after standard naming, before native additional-transform generation."""
    import bpy
    from mathutils import Vector
    if not work_copy:
        raise ValueError('Twist preparation requires an isolated single-user work copy')
    check_conflicts(arm)
    deform_names={b.name for b in arm.data.bones if b.use_deform}
    before={o.name:_skin(o,deform_names) for o in meshes}
    skins={n:[dict(w) for w in rows] for n,rows in before.items()}
    points={o.name:[tuple(arm.matrix_world.inverted()@o.matrix_world@v.co) for v in o.data.vertices]
            for o in meshes}
    specs=specifications()
    for spec in specs:
        parent=arm.data.bones[spec['parent']];distal=arm.data.bones[spec['distal']]
        dominant=[co for o in meshes for co,w in zip(points[o.name],skins[o.name])
                  if w.get(spec['parent'],0)>0 and w[spec['parent']]/sum(w.values())>.97]
        spec['layout']=compute_layout(parent.head_local,distal.head_local,dominant)
        spec['distal_head']=list(distal.head_local)
        changed=0
        for obj in meshes:
            for i,(co,w) in enumerate(zip(points[obj.name],skins[obj.name])):
                try:
                    new=transfer_weights(w,co,spec,spec['layout'])
                except ValueError as exc:
                    raise ValueError(f'{obj.name} vertex {i}: {exc}') from exc
                changed+=new!=w
                skins[obj.name][i]=new
        spec['changed_vertices']=changed
    display=display_plan(specs)
    # Plan all skin changes before mutating any data.
    rest={b.name:(b.matrix_local.copy(),b.length) for b in arm.data.bones}
    bpy.context.view_layer.objects.active=arm
    bpy.ops.object.mode_set(mode='EDIT')
    try:
        eb=arm.data.edit_bones
        for spec in specs:
            layout=spec['layout'];axis=Vector(layout['axis'])
            main=eb.new(spec['name']);main.head=layout['pivot']
            main.tail=layout['main_tail']
            main.parent=eb[spec['parent']];main.use_deform=True
            for i,head in enumerate(layout['helpers'],1):
                helper=eb.new(spec['name']+str(i));helper.head=head
                helper.tail=layout['helper_tails'][i-1]
                helper.roll=main.roll;helper.parent=eb[spec['parent']];helper.use_deform=True
            child=eb[spec['distal']];child.use_connect=False;child.parent=main
        for name,(matrix,length) in rest.items():
            eb[name].matrix=matrix;eb[name].length=length
    finally:
        bpy.ops.object.mode_set(mode='OBJECT')
    next_id=max((p.mmd_bone.bone_id for p in arm.pose.bones),default=-1)+1
    roles={}
    for spec in specs:
        for i,suffix in enumerate(('','1','2','3')):
            name=spec['name']+suffix;roles[name]=name
            p=arm.pose.bones[name];mm=p.mmd_bone
            mm.bone_id=next_id;next_id+=1
            mm.name_j=name;mm.name_e=spec['english']+suffix
            mm.is_controllable=not bool(suffix)
            mm.transform_order=arm.pose.bones[spec['parent']].mmd_bone.transform_order+(1 if suffix else 0)
            p.lock_location=(True,True,True);p.lock_rotation=(True,False,True)
            p.rotation_mode='XYZ';p.bone.hide=bool(suffix)
            mm.enabled_local_axes=True
            mm.local_axis_x=p.bone.matrix_local.col[0].to_3d().xzy
            mm.local_axis_z=p.bone.matrix_local.col[2].to_3d().xzy
            if not suffix:
                mm.enabled_fixed_axis=True;mm.fixed_axis=Vector(spec['layout']['axis']).xzy
            else:
                mm.has_additional_rotation=True;mm.has_additional_location=False
                mm.additional_transform_bone=spec['name'];mm.additional_transform_influence=i/4
    all_names=deform_names|set(roles)
    summaries=[]
    for obj in meshes:
        changed=[i for i,(old,new) in enumerate(zip(before[obj.name],skins[obj.name])) if old!=new]
        # Bone groups can also be relative-key masks. Keep the mask semantics
        # before changing those skin weights, without changing key coordinates.
        affected={n for i in changed for n in before[obj.name][i]
                  if before[obj.name][i].get(n)!=skins[obj.name][i].get(n)}
        masks={}
        if obj.data.shape_keys:
            for key in obj.data.shape_keys.key_blocks:
                name=key.vertex_group
                if name not in affected:
                    continue
                if name not in masks:
                    mask=obj.vertex_groups.new(name='prep_shape_mask_'+name)
                    for i,w in enumerate(before[obj.name]):
                        if w.get(name,0)>0:mask.add([i],w[name],'REPLACE')
                    masks[name]=mask.name
                key.vertex_group=masks[name]
        used={n for i in changed for n in skins[obj.name][i]}
        groups={n:obj.vertex_groups.get(n) or obj.vertex_groups.new(name=n) for n in used}
        for i in changed:
            for name in before[obj.name][i]:
                obj.vertex_groups[name].remove([i])
            for name,weight in skins[obj.name][i].items():
                groups[name].add([i],weight,'REPLACE')
        actual=_skin(obj,all_names)
        error=max((abs(a.get(n,0)-b.get(n,0)) for a,b in zip(actual,skins[obj.name])
                   for n in a.keys()|b.keys()),default=0)
        mass=max((abs(sum(a.values())-sum(b.values())) for a,b in zip(actual,before[obj.name])),default=0)
        if max(error,mass)>1e-6:
            raise RuntimeError('Twist skin transfer did not preserve planned weights: '+obj.name)
        summaries.append(dict(mesh=obj.name,changed_vertices=len(changed),weight_error=error,weight_sum_error=mass,
                              preserved_shape_masks=masks))
    report=dict(version=1,profile='semi-standard-arm-twist',segments=specs,skin=summaries,
                axis_correction='actual_normalized_apose_joints',weight_policy='preserve_other_influences',display=display)
    order_bones(arm)
    configure_display(arm)
    arm['auto_rig_arm_twist']=json.dumps(report,ensure_ascii=False)
    bpy.context.view_layer.update()
    return roles,report


def align_display(arm, *, mmd_package, work_copy=False):
    """Repair existing prepared neutral rig in place, without redistributing skin.

    Caller saves a work-scene checkpoint. Rebuild native append helpers after
    changing rest bases; a failed post-check requires restoring that checkpoint.
    """
    import bpy
    import importlib
    from mathutils import Matrix
    if not work_copy or not arm.get('auto_rig_version') or arm.mode != 'OBJECT':
        raise ValueError('Select a prepared work-copy armature in Object Mode')
    if arm.animation_data or any((max((abs(p.matrix_basis[i][j] - Matrix.Identity(4)[i][j]) for i in range(4) for j in range(4))) > 1e-05 for p in arm.pose.bones)):
        raise ValueError('Display adjustment requires neutral pose without animation')
    report = json.loads(arm['auto_rig_arm_twist'])
    for spec in report['segments']:
        layout = spec['layout']
        start = arm.data.bones[_name(arm, spec['parent'])].head_local
        end = arm.data.bones[_name(arm, spec['distal'])].head_local
        layout['length'] = (end - start).length
        layout['axis'] = list((end - start).normalized())
        layout['helpers'] = [list(arm.data.bones[_name(arm, spec['name'] + str(i))].head_local) for i in (1, 2, 3)]
        spec['distal_head'] = list(end)
    report['display'] = display_plan(report['segments'])
    FnBone = importlib.import_module(mmd_package + '.core.bone').FnBone
    mirror = arm.data.use_mirror_x
    arm.data.use_mirror_x = False
    bpy.context.view_layer.objects.active = arm
    try:
        FnBone.clean_additional_transformation(arm)
        heads = {b.name: b.head_local.copy() for b in arm.data.bones}
        bpy.ops.object.mode_set(mode='EDIT')
        try:
            for spec in report['segments']:
                eb = arm.data.edit_bones
                layout = spec['layout']
                child = eb[_name(arm, spec['distal'])]
                child.use_connect = False
                eb[_name(arm, spec['name'])].tail = layout['main_tail']
                for (i, tail) in enumerate(layout['helper_tails'], 1):
                    helper = eb[_name(arm, spec['name'] + str(i))]
                    helper.tail = tail
                    helper.roll = 0
        finally:
            bpy.ops.object.mode_set(mode='OBJECT')
        configure_display(arm)
        FnBone.apply_additional_transformation(arm)
        configure_preview(arm)
        bpy.context.view_layer.update()
        if any(((arm.data.bones[_name(arm, n)].head_local - head).length > 1e-06 for (n, head) in heads.items())):
            raise RuntimeError('Display adjustment moved a joint pivot; restore checkpoint')
        arm['auto_rig_arm_twist'] = json.dumps(report, ensure_ascii=False)
    finally:
        arm.data.use_mirror_x = mirror
    return report


def motion_probe(arm):
    """Native append fractions, downstream inheritance and restored neutral pose."""
    import bpy
    from mathutils import Matrix
    names = required_names()
    configure_preview(arm)
    saved = {_name(arm, n): arm.pose.bones[_name(arm, n)].matrix_basis.copy() for n in names}
    rows = []
    try:
        for spec in specifications():
            main = arm.pose.bones[_name(arm, spec['name'])]
            for degrees in (-60, -30, 30, 60):
                main.matrix_basis = Matrix.Rotation(math.radians(degrees), 4, 'Y')
                arm.update_tag()
                bpy.context.view_layer.update()
                evaluated = arm.evaluated_get(bpy.context.evaluated_depsgraph_get())
                errors = []
                for (name, fraction) in [(spec['name'] + str(i), i / 4) for i in (1, 2, 3)] + [(spec['distal'], 1)]:
                    p = evaluated.pose.bones[_name(arm, name)]
                    parent = evaluated.pose.bones[_name(arm, spec['parent'])]
                    delta = p.matrix.to_3x3() @ p.bone.matrix_local.to_3x3().inverted()
                    parent_delta = parent.matrix.to_3x3() @ parent.bone.matrix_local.to_3x3().inverted()
                    angle = math.degrees((parent_delta.inverted() @ delta).to_quaternion().angle)
                    errors.append(abs(angle - abs(degrees * fraction)))
                rows.append(dict(bone=main.name, degrees=degrees, max_angle_error=max(errors)))
            main.matrix_basis = saved[main.name]
    finally:
        for (name, matrix) in saved.items():
            arm.pose.bones[_name(arm, name)].matrix_basis = matrix
        arm.update_tag()
        bpy.context.view_layer.update()
    worst = max((r['max_angle_error'] for r in rows))
    return dict(status='passed' if worst < 0.1 else 'failed', max_angle_error=worst, samples=rows)


def validate(arm, *, motion=True):
    errors = []
    metadata = json.loads(arm.get('auto_rig_arm_twist', '{}'))
    for spec in specifications():
        segment = next((s for s in metadata.get('segments', []) if s['name'] == spec['name']), None)
        for suffix in ('', '1', '2', '3'):
            name = spec['name'] + suffix
            p = arm.pose.bones.get(_name(arm, name))
            if p is None or p.mmd_bone.name_j != name:
                errors.append(name + ': missing or nonstandard name')
                continue
            mm = p.mmd_bone
            if p.parent is None or p.parent.name != _name(arm, spec['parent']) or (not p.bone.use_deform):
                errors.append(name + ': parent or deform flag')
            elif p.parent.mmd_bone.bone_id >= mm.bone_id:
                errors.append(name + ': parent must precede child in PMX order')
            locks = (False, False, False) if suffix else (True, False, True)
            if not all(p.lock_location) or tuple(p.lock_rotation) != locks:
                errors.append(name + ': channel locks')
            if suffix:
                if not mm.has_additional_rotation or mm.has_additional_location or mm.additional_transform_bone != _name(arm, spec['name']) or (abs(mm.additional_transform_influence - int(suffix) / 4) > 1e-06):
                    errors.append(name + ': append rotation')
                if not p.bone.hide or mm.is_controllable:
                    errors.append(name + ': helper visibility/control')
                constraint = p.constraints.get('mmd_additional_rotation')
                if not constraint or constraint.mute or (not constraint.is_valid):
                    errors.append(name + ': missing active native append constraint')
                if segment and (p.bone.head_local - __import__('mathutils').Vector(segment['layout']['helpers'][int(suffix) - 1])).length > segment['layout']['length'] * 1e-05:
                    errors.append(name + ': helper layout')
                expected = __import__('mathutils').Vector((0, 0, metadata.get('display', {}).get('helper_length', 0)))
                if expected.z <= 0 or (p.bone.tail_local - p.bone.head_local - expected).length > expected.z * 1e-05:
                    errors.append(name + ': helper must point vertically upward at uniform length')
                if mm.display_connection_type != 'OFFSET' or mm.enabled_local_axes:
                    errors.append(name + ': helper display metadata')
            else:
                start = arm.data.bones[_name(arm, spec['parent'])].head_local
                end = arm.data.bones[_name(arm, spec['distal'])].head_local
                axis = (end - start).normalized()
                if not mm.enabled_fixed_axis or (mm.fixed_axis.xzy - axis).length > 1e-05:
                    errors.append(name + ': fixed axis')
                if (p.bone.head_local - (start + (end - start) * 0.6)).length > (end - start).length * 1e-05:
                    errors.append(name + ': pivot')
                if (p.bone.tail_local - end).length > (end - start).length * 1e-05:
                    errors.append(name + ': main tail must coincide with downstream head')
                if mm.display_connection_type != 'BONE' or mm.display_connection_bone_id != arm.pose.bones[_name(arm, spec['distal'])].mmd_bone.bone_id:
                    errors.append(name + ': main display must target downstream bone')
        distal = arm.data.bones.get(_name(arm, spec['distal']))
        if not distal or not distal.parent or distal.parent.name != _name(arm, spec['name']):
            errors.append(spec['name'] + ': downstream hierarchy')
        elif arm.pose.bones[_name(arm, spec['name'])].mmd_bone.bone_id >= arm.pose.bones[_name(arm, distal.name)].mmd_bone.bone_id:
            errors.append(spec['name'] + ': twist must precede downstream bone in PMX order')
    if not metadata:
        errors.append('missing skin transfer report')
    probe = motion_probe(arm) if motion and (not errors) else None
    return dict(status='failed' if errors or (probe and probe['status'] != 'passed') else 'passed', errors=errors, motion=probe, skin=metadata.get('skin', []))
