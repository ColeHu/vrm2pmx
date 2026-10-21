"""Restore toe-joint pivots and extend EX tails slightly past toe controls."""
import json
from pathlib import Path
import runpy


def repair(armature_name,pivots=None,*,work_copy=False):
    import bpy
    from mathutils import Matrix,Vector
    arm=bpy.data.objects[armature_name]
    if not work_copy or not arm.get('auto_rig_version') or arm.mode!='OBJECT' or arm.data.users>1:
        raise ValueError('Prepared single-user Object Mode work armature required')
    if arm.animation_data or any(max(abs(p.matrix_basis[i][j]-Matrix.Identity(4)[i][j])
        for i in range(4) for j in range(4))>1e-4 for p in arm.pose.bones):
        raise ValueError('Neutral, unanimated work pose required')
    if pivots is None:
        if not arm.get('auto_rig_toe_articulation'):
            raise ValueError('Original toe articulation is required; do not infer it from the toe marker')
        pivots=json.loads(arm['auto_rig_toe_articulation'])
    names={side:next(p.name for p in arm.pose.bones if not p.is_mmd_shadow_bone and p.mmd_bone.name_j==side+'足先EX') for side in pivots}
    geometry=runpy.run_path(str(Path(__file__).with_name('toe_geometry.py')))
    layouts={}
    for side,position in pivots.items():
        ankle=next(p.bone for p in arm.pose.bones if not p.is_mmd_shadow_bone and p.mmd_bone.name_j==side+'足首')
        marker=next(p.bone for p in arm.pose.bones if not p.is_mmd_shadow_bone and p.mmd_bone.name_j==side+'つま先')
        if (Vector(position)-ankle.head_local).length>max((marker.head_local-ankle.head_local).length*1.5,1e-6):
            raise ValueError('Toe pivot is outside the local foot; exclude display/world offsets')
        toe_ik=next(p.bone for p in arm.pose.bones if not p.is_mmd_shadow_bone and p.mmd_bone.name_j==side+'つま先ＩＫ')
        tolerance=max((marker.head_local-ankle.head_local).length*1e-4,1e-7)
        if (marker.head_local-toe_ik.head_local).length>tolerance:
            raise ValueError('Toe and toe-IK control heads must coincide')
        layouts[side]=geometry['layout'](ankle.head_local,position,marker.head_local,toe_ik.head_local)
    rest={b.name:(b.matrix_local.copy(),b.length,b.use_connect) for b in arm.data.bones}
    mirror=arm.data.use_mirror_x;arm.data.use_mirror_x=False
    bpy.context.view_layer.objects.active=arm
    bpy.ops.object.mode_set(mode='EDIT')
    try:
        for b in arm.data.edit_bones:b.use_connect=False
        for side,name in names.items():
            bone=arm.data.edit_bones[name]
            bone.head=layouts[side]['head'];bone.tail=layouts[side]['tail']
        for name,(matrix,length,connected) in rest.items():
            if name not in names.values():
                bone=arm.data.edit_bones[name];bone.matrix=matrix;bone.length=length
        for name,(_,_,connected) in rest.items():
            bone=arm.data.edit_bones[name]
            bone.use_connect=bool(name not in names.values() and connected and bone.parent and (bone.head-bone.parent.tail).length<1e-6)
    finally:
        bpy.ops.object.mode_set(mode='OBJECT');arm.data.use_mirror_x=mirror
    arm['auto_rig_toe_pivots']=json.dumps({side:list(p) for side,p in pivots.items()},ensure_ascii=False)
    arm['auto_rig_toe_articulation']=arm['auto_rig_toe_pivots']
    bpy.context.view_layer.update()
    return dict(status='passed',changes=[dict(role=side+'足先EX',before=list(rest[name][0].translation),
        after=list(arm.data.bones[name].head_local)) for side,name in names.items()],weights_edited=False)
