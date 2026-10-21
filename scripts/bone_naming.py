"""Blender body names use .L/.R; PMX Japanese semantics stay independent."""
import json


def internal_name(role):
    for side,suffix in (('左','.L'),('右','.R')):
        if role.startswith(side):return role[1:]+suffix
        if role.endswith(side):return role[:-1]+suffix
    return role


def resolve(arm,role):
    matches=[p.name for p in arm.pose.bones if not p.is_mmd_shadow_bone and p.mmd_bone.name_j==role]
    if len(matches)==1:return matches[0]
    if not matches and role in arm.data.bones:return role
    raise ValueError('Missing or duplicate semantic bone: '+role)


def apply(armature_name,*,work_copy=False):
    import bpy
    arm=bpy.data.objects[armature_name]
    if not work_copy or arm.type!='ARMATURE' or arm.mode=='EDIT' or arm.data.users>1:
        raise ValueError('Single-user work armature outside Edit Mode required')
    roles=json.loads(arm.get('auto_rig_mapping','{}'))
    if not roles:raise ValueError('Prepared body mapping required')
    rename={old:internal_name(role) for role,old in roles.items() if old!=internal_name(role)}
    if len(set(rename.values()))!=len(rename):raise ValueError('Body naming targets collide')
    occupied=set(arm.data.bones.keys())-set(rename)
    if occupied & set(rename.values()):raise ValueError('Suffix target occupied: '+str(occupied & set(rename.values())))
    meshes=[o for o in bpy.data.objects if o.type=='MESH' and any(m.type=='ARMATURE' and m.object==arm for m in o.modifiers)]
    groups=[];refs=[];masks=[]
    for obj in meshes:
        for old,new in rename.items():
            group=obj.vertex_groups.get(old)
            if group:
                if obj.vertex_groups.get(new) and new not in rename:raise ValueError('Skin group collision: '+obj.name+'/'+new)
                groups.append((group,new))
        if obj.data.shape_keys:
            masks.extend((k,rename[k.vertex_group]) for k in obj.data.shape_keys.key_blocks if k.vertex_group in rename)
    for obj in bpy.data.objects:
        owners=list(obj.constraints)
        if obj.type=='ARMATURE':owners.extend(c for p in obj.pose.bones for c in p.constraints)
        for c in owners:
            for target_field,bone_field in (('target','subtarget'),('pole_target','pole_subtarget')):
                if getattr(c,target_field,None)==arm and getattr(c,bone_field,'') in rename:
                    refs.append((c,bone_field,rename[getattr(c,bone_field)]))
    appends=[(p.mmd_bone,rename[p.mmd_bone.additional_transform_bone]) for p in arm.pose.bones
             if p.mmd_bone.additional_transform_bone in rename]
    root=arm.parent
    frames=[(item,rename[item.name]) for f in root.mmd_root.display_item_frames for item in f.data
            if item.type=='BONE' and item.name in rename] if root and root.mmd_type=='ROOT' else []
    pending=[]
    for index,(old,new) in enumerate(rename.items()):
        bone=arm.data.bones[old];temporary='__body_suffix_'+str(index)
        while temporary in arm.data.bones:temporary+='x'
        bone.name=temporary;pending.append((bone,new))
    for bone,new in pending:
        bone.name=new
        if bone.name!=new:raise RuntimeError('Unexpected bone name collision')
    for group,new in groups:group.name=new
    for owner,field,value in refs:setattr(owner,field,value)
    for key,new in masks:key.vertex_group=new
    for mm,new in appends:mm.additional_transform_bone=new
    for item,new in frames:item.name=new
    roles={role:rename.get(name,name) for role,name in roles.items()}
    arm['auto_rig_mapping']=json.dumps(roles,ensure_ascii=False)
    if arm.get('auto_rig_collection_plan'):
        plan=json.loads(arm['auto_rig_collection_plan'])
        plan['assignments']={rename.get(n,n):v for n,v in plan['assignments'].items()}
        arm['auto_rig_collection_plan']=json.dumps(plan,ensure_ascii=False)
    arm['auto_rig_internal_naming']='suffix'
    bpy.context.view_layer.update()
    return dict(status='passed',renamed=rename,roles=roles)
