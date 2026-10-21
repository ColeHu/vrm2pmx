"""Full synthetic humanoid + mesh tests; no character names or input assets."""
import json
from pathlib import Path
import runpy


def fixture(version, knee_outward=False, knee_backward=False):
    import bpy
    from mathutils import Matrix
    data = bpy.data.armatures.new('synthetic_source')
    arm = bpy.data.objects.new('synthetic_source', data)
    bpy.context.scene.collection.objects.link(arm)
    bpy.context.view_layer.objects.active = arm
    arm.select_set(True)
    mapping = {}
    bpy.ops.object.mode_set(mode='EDIT')
    def bone(role, head, tail, parent=None):
        # Deliberately unrelated internal names test semantic mapping.
        name = 'node_'+str(len(mapping))
        b = data.edit_bones.new(name); b.head = head; b.tail = tail
        if parent:
            b.parent = data.edit_bones[mapping[parent]]
        mapping[role] = b.name
    structural_root=data.edit_bones.new('root');structural_root.head=(0,0,0);structural_root.tail=(0,0,.1)
    bone('hips', (0,0,.9), (0,0,1.0))
    data.edit_bones[mapping['hips']].parent=structural_root
    branch=data.edit_bones.new('root_accessory_branch');branch.head=(0,.1,.2);branch.tail=(0,.1,.3);branch.parent=structural_root
    bone('spine', (0,0,.94), (0,0,1.12), 'hips')
    bone('chest', (0,0,1.12), (0,0,1.35), 'spine')
    bone('neck', (0,0,1.35), (0,0,1.45), 'chest')
    bone('head', (0,0,1.45), (0,0,1.65), 'neck')
    for side, sign in [('left', 1), ('right', -1)]:
        bone(side+'Eye', (sign*.035,-.07,1.55), (sign*.035,-.1,1.55), 'head')
        bone(side+'Shoulder', (sign*.04,0,1.3), (sign*.18,0,1.3), 'chest')
        bone(side+'UpperArm', (sign*.18,0,1.3), (sign*.48,0,1.3), side+'Shoulder')
        bone(side+'LowerArm', (sign*.48,0,1.3), (sign*.73,0,1.3), side+'UpperArm')
        bone(side+'Hand', (sign*.73,0,1.3), (sign*.82,0,1.3), side+'LowerArm')
        for finger, y in [('Thumb',-.035), ('Index',-.025), ('Middle',0), ('Ring',.015), ('Little',.03)]:
            parts = (('Metacarpal','Proximal','Distal') if version=='1.0' else ('Proximal','Intermediate','Distal')) if finger=='Thumb' else ('Proximal','Intermediate','Distal')
            parent = side+'Hand'
            for index, part in enumerate(parts):
                role = side+finger+part
                bone(role, (sign*(.82+index*.035),y,1.3), (sign*(.855+index*.035),y,1.3), parent)
                parent = role
        bone(side+'UpperLeg', (sign*.10,0,.9), (sign*.10,-.035,.50), 'hips')
        bone(side+'LowerLeg', (sign*.10,-.035,.50), (sign*.10,0,.10), side+'UpperLeg')
        bone(side+'Foot', (sign*.10,0,.10), (sign*.10,-.12,.04), side+'LowerLeg')
        bone(side+'Toes', (sign*.10,-.12,.04), (sign*.10,-.20,.04), side+'Foot')
    extra = data.edit_bones.new('unnamed_accessory_branch')
    extra.head=(0,.05,1.55);extra.tail=(0,.05,1.4);extra.parent=data.edit_bones[mapping['head']]
    for suffix,sign in [('L',1),('R',-1)]:
        parent=structural_root
        for role in ('足IK親','足ＩＫ','つま先ＩＫ'):
            b=data.edit_bones.new(role+'.'+suffix)
            b.head=(sign*.1,.2,.3);b.tail=(sign*.1,.2,.35);b.parent=parent;parent=b
    for name in ['つま先ＩＫ.L.001','足IK.L']:
        b=data.edit_bones.new(name);b.head=(.1,.2,.3);b.tail=(.1,.2,.35);b.parent=structural_root
    child=data.edit_bones.new('duplicate_control_child');child.head=(.1,.3,.3);child.tail=(.1,.3,.4)
    child.parent=data.edit_bones['つま先ＩＫ.L.001']
    if knee_backward:
        for side in ('left','right'):
            thigh=data.edit_bones[mapping[side+'UpperLeg']]
            shin=data.edit_bones[mapping[side+'LowerLeg']]
            knee=shin.head.copy();knee.y=.035
            thigh.tail=knee;shin.head=knee
    if knee_outward:
        for side,sign in [('left',1),('right',-1)]:
            knee=data.edit_bones[mapping[side+'LowerLeg']].head.copy()
            knee.x+=sign*.035;knee.y=.003
            data.edit_bones[mapping[side+'UpperLeg']].tail=knee
            data.edit_bones[mapping[side+'LowerLeg']].head=knee
    bpy.ops.object.mode_set(mode='OBJECT')
    vertices, faces, weights = [], [], []
    for role, x, y, z in [('hips',0,0,.89), ('leftUpperArm',.35,0,1.3),
                          ('rightUpperArm',-.35,0,1.3), ('leftToes',.1,-.23,.04),
                          ('rightToes',-.1,-.23,.04)]:
        i = len(vertices)
        vertices.extend([(x-.015,y-.01,z), (x+.015,y-.01,z), (x,y+.01,z+.005)])
        faces.append((i,i+1,i+2));weights.append((mapping[role],[i,i+1,i+2]))
    # Along-arm skin samples exercise twist gradients on both upper/lower segments.
    for side,sign in [('left',1),('right',-1)]:
        for segment,start,length in [('UpperArm',.18,.30),('LowerArm',.48,.25)]:
            for fraction in (.05,.25,.45,.65,.85,.98):
                i=len(vertices);x=sign*(start+length*fraction)
                vertices.extend([(x,-.02,1.3),(x,.01,1.317),(x,.01,1.283)])
                faces.append((i,i+1,i+2));weights.append((mapping[side+segment],[i,i+1,i+2]))
    mesh = bpy.data.meshes.new('synthetic_skin');mesh.from_pydata(vertices,[],faces)
    obj = bpy.data.objects.new('synthetic_skin',mesh);bpy.context.scene.collection.objects.link(obj)
    obj.parent=arm
    for name, ids in weights:
        g=obj.vertex_groups.get(name) or obj.vertex_groups.new(name=name);g.add(ids,1,'REPLACE')
    obj.vertex_groups.new(name='root')
    for b in data.bones:
        if 'ＩＫ' in b.name or '足IK' in b.name:
            obj.vertex_groups.new(name=b.name)
    mod=obj.modifiers.new('skin','ARMATURE');mod.object=arm
    obj.shape_key_add(name='Basis');key=obj.shape_key_add(name='test_expression');key.data[1].co.z+=.02
    arm.matrix_world=Matrix.Translation((2,3,.4))@Matrix.Rotation(.4,4,'Z')
    obj.matrix_world=arm.matrix_world.copy()
    # Populate the actual addon metadata as well as returning the test mapping.
    ext=arm.data.vrm_addon_extension;ext.spec_version=version
    if version=='0.0':
        for role,name in mapping.items():
            h=ext.vrm0.humanoid.human_bones.add();h.bone=role;h.node.bone_name=name
    else:
        for role,h in ext.vrm1.humanoid.human_bones.human_bone_name_to_human_bone().items():
            if role.value in mapping:h.node.bone_name=mapping[role.value]
    return arm,obj,mapping
