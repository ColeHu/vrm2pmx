"""Reference-relative waist plus bilateral MMD inverse-rotation controls."""
import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WAIST_ROLES = ('腰', '腰キャンセル左', '腰キャンセル右')


def load_profile():
    return json.loads((ROOT/'references/waist-profile.json').read_text(encoding='utf-8'))


def compute_layout(upper_head, left_hip, right_hip, profile=None):
    """Pure geometry: transport normalized offsets, never reference body sizes."""
    profile = profile or load_profile()
    values = [upper_head, left_hip, right_hip]
    if any(len(v)!=3 or not all(math.isfinite(x) for x in v) for v in values):
        raise ValueError('Expected finite three-dimensional torso and hip anchors')
    difference = [a-b for a,b in zip(left_hip,right_hip)]
    width = math.sqrt(sum(v*v for v in difference))
    planar = math.hypot(difference[0],difference[1])
    if width < 1e-6 or planar < width*.5:
        raise ValueError('Waist layout requires distinct, mostly lateral hip sockets')
    x = [difference[0]/planar,difference[1]/planar,0]
    back = [-x[1],x[0],0]
    up = [0,0,1]
    def position(offset):
        return [upper_head[i]+width*(offset[0]*x[i]+offset[1]*back[i]+offset[2]*up[i]) for i in range(3)]
    length = width*profile['cancel_length']
    return dict(profile_id=profile['profile_id'],hip_width=width,
                waist_head=position(profile['waist_head']),waist_tail=position(profile['waist_tail']),
                cancel_left_head=list(left_hip),cancel_right_head=list(right_hip),
                cancel_left_tail=[v+length*up[i] for i,v in enumerate(left_hip)],
                cancel_right_tail=[v+length*up[i] for i,v in enumerate(right_hip)])


def build_edit_bones(edit_bones, roles, generate):
    """Called inside the rig builder's Edit Mode; retain all body joint pivots."""
    from mathutils import Vector
    layout = compute_layout(edit_bones[roles['上半身']].head,
                            edit_bones[roles['左足']].head,edit_bones[roles['右足']].head)
    waist = generate('腰',layout['waist_head'],layout['waist_tail'],'グルーブ')
    left=(edit_bones[roles['左足']].head-edit_bones[roles['右足']].head).copy()
    left.z=0;left.normalize()
    waist.align_roll(left.cross(waist.tail-waist.head))
    for role in ('上半身','下半身'):
        edit_bones[roles[role]].parent=waist
    for side,key in (('左','left'),('右','right')):
        cancel=generate('腰キャンセル'+side,layout['cancel_'+key+'_head'],layout['cancel_'+key+'_tail'],'下半身')
        cancel.align_roll(left.cross(Vector((0,0,1))))
        edit_bones[roles[side+'足']].parent=cancel
    return layout


def configure_controls(arm, roles):
    for role in WAIST_ROLES:
        bone=arm.pose.bones[roles[role]]
        bone.lock_location=(True,True,True)
        bone.lock_rotation=(False,False,False)
        bone.mmd_bone.is_controllable=True
        bone.mmd_bone.transform_order=0
    for side in ('左','右'):
        mm=arm.pose.bones[roles['腰キャンセル'+side]].mmd_bone
        mm.has_additional_rotation=True
        mm.has_additional_location=False
        mm.additional_transform_bone=roles['腰']
        mm.additional_transform_influence=-1.0


def motion_probe(armature_name, names):
    """With IK temporarily muted, test waist pivots and canceled leg rotation.

    Ordinary foot IK is tested separately with its original constraints active.
    Restore exact channels/constraint mutes even when a probe raises.
    """
    import bpy
    from mathutils import Matrix, Quaternion, Vector
    reference=json.loads((ROOT/'references/waist-motion-reference.json').read_text(encoding='utf-8'))
    reference_rows={(r['axis'],r['degrees']):r for r in reference['samples']}
    def rotation_error(a,b):
        q=a.normalized().rotation_difference(b.normalized())
        return math.degrees(2*math.atan2(Vector((q.x,q.y,q.z)).length,abs(q.w)))
    arm=bpy.data.objects[armature_name]
    saved={p.name:{key:(getattr(p,key).copy() if hasattr(getattr(p,key),'copy') else tuple(getattr(p,key)))
                   for key in ('location','scale','rotation_euler','rotation_quaternion','rotation_axis_angle')}
           for p in arm.pose.bones}
    ik=[(c,c.mute) for p in arm.pose.bones for c in p.constraints if c.type=='IK']
    watched=('上半身','下半身','腰キャンセル左','腰キャンセル右','左足','右足')
    def evaluated():
        arm.update_tag()
        bpy.context.view_layer.update()
        obj=arm.evaluated_get(bpy.context.evaluated_depsgraph_get())
        return {r:obj.pose.bones[names[r]].matrix.copy() for r in watched}
    rows=[]
    try:
        for p in arm.pose.bones:
            p.matrix_basis=Matrix.Identity(4)
        for c,_ in ik:
            c.mute=True
        baseline=evaluated()
        waist=arm.pose.bones[names['腰']]
        pivot=waist.bone.head_local.copy()
        basis=waist.bone.matrix_local.to_3x3()
        for axis in range(3):
            for degrees in (-10,10):
                local=Quaternion(Vector(tuple(1 if i==axis else 0 for i in range(3))),math.radians(degrees))
                waist.matrix_basis=local.to_matrix().to_4x4()
                delta=(basis@local.to_matrix()@basis.inverted()).to_4x4()
                transform=Matrix.Translation(pivot)@delta@Matrix.Translation(-pivot)
                current=evaluated()
                torso_angle=max(rotation_error((transform@baseline[r]).to_quaternion(),
                    current[r].to_quaternion()) for r in ('上半身','下半身'))
                head_error=max((current[r].translation-transform@baseline[r].translation).length for r in watched)
                canceled_angle=max(rotation_error(baseline[r].to_quaternion(),current[r].to_quaternion())
                                   for r in ('腰キャンセル左','腰キャンセル右','左足','右足'))
                char_x=arm.data.bones[names['左足']].head_local-arm.data.bones[names['右足']].head_local
                char_x.z=0;char_x.normalize()
                char_z=Vector((0,0,1));char_y=char_z.cross(char_x)
                char_basis=Matrix((char_x,char_y,char_z)).transposed().to_quaternion()
                deltas={r:list(char_basis.conjugated()@(current[r].to_quaternion()@baseline[r].to_quaternion().conjugated())@char_basis)
                        for r in ('腰キャンセル左','腰キャンセル右','左足','右足')}
                expected=reference_rows[('XYZ'[axis],degrees)]['cancel_rotation_delta_character']
                reference_error=max(rotation_error(Quaternion(deltas[r]),Quaternion(expected[r])) for r in deltas)
                rows.append(dict(axis='XYZ'[axis],degrees=degrees,torso_rotation_error_degrees=torso_angle,
                                 pivot_position_error=head_error,canceled_rotation_error_degrees=canceled_angle,
                                 cancel_rotation_delta_character=deltas,reference_response_error_degrees=reference_error))
        waist.matrix_basis=Matrix.Identity(4)
        lower=arm.pose.bones[names['下半身']]
        local=Quaternion((1,0,0),math.radians(10))
        lower.matrix_basis=local.to_matrix().to_4x4()
        lower_basis=lower.bone.matrix_local.to_3x3()
        lower_delta=(lower_basis@local.to_matrix()@lower_basis.inverted()).to_4x4()
        lower_transform=Matrix.Translation(lower.bone.head_local)@lower_delta@Matrix.Translation(-lower.bone.head_local)
        independent=evaluated()
        independent_error=max(rotation_error((lower_transform@baseline[r]).to_quaternion(),independent[r].to_quaternion())
                              for r in ('腰キャンセル左','腰キャンセル右','左足','右足'))
        upper_error=rotation_error(baseline['上半身'].to_quaternion(),independent['上半身'].to_quaternion())
    finally:
        for p in arm.pose.bones:
            for key,value in saved[p.name].items():
                setattr(p,key,value)
        for c,mute in ik:
            c.mute=mute
        arm.update_tag();bpy.context.view_layer.update()
    scale=(arm.data.bones[names['左足']].head_local-arm.data.bones[names['右足']].head_local).length
    passed=all(max(r['torso_rotation_error_degrees'],r['reference_response_error_degrees'])<.1
               and r['pivot_position_error']<scale*1e-4 for r in rows) and max(independent_error,upper_error)<.1
    return dict(status='passed' if passed else 'failed',scope='waist_motion_with_ik_temporarily_muted',samples=rows,
                lower_body_independence=dict(leg_follow_error_degrees=independent_error,upper_body_error_degrees=upper_error),
                reference_note='Native MMD -1 additional rotation is compared to measured reference response; not a quaternion-perfect inverse')


def validate_waist(armature_name, *, motion=True):
    import bpy
    arm=bpy.data.objects[armature_name]
    by_role={}
    for p in arm.pose.bones:
        if not p.is_mmd_shadow_bone:
            by_role.setdefault(p.mmd_bone.name_j,[]).append(p.name)
    required=WAIST_ROLES+('グルーブ','上半身','下半身','左足','右足')
    issues=[{'role':r,'issue':'missing_or_duplicate'} for r in required if len(by_role.get(r,[]))!=1]
    if issues:
        return dict(status='failed',scope='reference_waist',issues=issues)
    names={r:by_role[r][0] for r in required}
    bones=arm.data.bones
    profile=load_profile()
    layout=compute_layout(bones[names['上半身']].head_local,bones[names['左足']].head_local,bones[names['右足']].head_local)
    from mathutils import Vector
    tolerance=layout['hip_width']*1e-4
    left=(bones[names['左足']].head_local-bones[names['右足']].head_local).copy()
    left.z=0;left.normalize()
    for role,parent in profile['parenting'].items():
        bone=bones[names[role]]
        if bone.parent is None or bone.parent.name!=names[parent]:
            issues.append({'role':role,'issue':'wrong_parent','expected':parent})
    for role,key in (('腰','waist'),('腰キャンセル左','cancel_left'),('腰キャンセル右','cancel_right')):
        bone=bones[names[role]]
        for attr,end in (('head_local','head'),('tail_local','tail')):
            if (getattr(bone,attr)-Vector(layout[key+'_'+end])).length>tolerance:
                issues.append({'role':role,'issue':'relative_'+end})
        if bone.use_deform:
            issues.append({'role':role,'issue':'control_must_not_deform'})
        if bone.matrix_local.to_3x3().col[0].normalized().dot(left)<.99999:
            issues.append({'role':role,'issue':'control_left_axis'})
        pose=arm.pose.bones[names[role]]
        if tuple(pose.lock_location)!=(True,True,True) or any(pose.lock_rotation):
            issues.append({'role':role,'issue':'control_locks'})
        for obj in bpy.context.scene.objects:
            if obj.type!='MESH' or not any(m.type=='ARMATURE' and m.object==arm for m in obj.modifiers):
                continue
            group=obj.vertex_groups.get(names[role])
            if group and any(w.group==group.index and w.weight>0 for v in obj.data.vertices for w in v.groups):
                issues.append({'role':role,'issue':'unexpected_skin_weights','mesh':obj.name})
    for side in ('左','右'):
        mm=arm.pose.bones[names['腰キャンセル'+side]].mmd_bone
        if not mm.has_additional_rotation or mm.has_additional_location or mm.additional_transform_bone!=names['腰'] or abs(mm.additional_transform_influence+1)>1e-6:
            issues.append({'role':'腰キャンセル'+side,'issue':'inverse_rotation_metadata'})
    result=dict(status='failed' if issues else 'passed',scope='reference_waist',profile_id=profile['profile_id'],issues=issues,layout=layout)
    if motion and not issues:
        result['motion']=motion_probe(armature_name,names)
        if result['motion']['status']!='passed':
            result['status']='failed'
    return result
