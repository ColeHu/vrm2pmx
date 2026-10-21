"""Plan modifier cancellation read-only; disable only on single-user work meshes."""
import re


def smooth_by_angle_evidence(modifier):
    """Identify the known shading-only node group, not a modifier's display name."""
    if modifier.type != 'NODES':
        return None
    group = modifier.node_group
    if group is None:
        return None
    weak = getattr(group, 'library_weak_reference', None)
    weak_path = (getattr(weak, 'filepath', '') or '').replace('\\', '/')
    weak_id = getattr(weak, 'id_name', '') or ''
    name = re.sub(r'\.\d+$', '', group.name)
    known_name = name in ('Smooth by Angle', '按角度平滑')
    known_asset = weak_path.rsplit('/', 1)[-1] == 'smooth_by_angle.blend' and weak_id == 'NTSmooth by Angle'
    kinds = {node.bl_idname for node in group.nodes}
    required = {'NodeGroupInput', 'NodeGroupOutput', 'GeometryNodeInputMeshEdgeAngle', 'GeometryNodeSetShadeSmooth'}
    allowed = required | {'GeometryNodeInputEdgeSmooth', 'GeometryNodeInputShadeSmooth',
                          'FunctionNodeBooleanMath', 'FunctionNodeCompare', 'ShaderNodeMath',
                          'ShaderNodeValue', 'NodeReroute'}
    if (known_name or known_asset) and required <= kinds and kinds <= allowed:
        return dict(node_group=group.name, recognized_by='asset_and_shading_graph' if known_asset else 'group_name_and_shading_graph',
                    node_types=sorted(kinds))
    return None


def cancellation_reason(modifier):
    if modifier.type == 'TRIANGULATE':
        return dict(reason='triangulate')
    smooth = smooth_by_angle_evidence(modifier)
    if smooth:
        return dict(reason='smooth_by_angle', **smooth)
    if modifier.name.startswith('MToon Outline'):
        return dict(reason='mtoon_outline_preview')
    return None


def plan_mesh_modifiers(obj):
    """Read-only list of known cancellations and unsupported viewport effects."""
    planned, unsupported = [], []
    for index, modifier in enumerate(obj.modifiers):
        if modifier.type == 'ARMATURE':
            continue
        decision = cancellation_reason(modifier)
        if decision and (modifier.show_viewport or modifier.show_render):
            planned.append(dict(index=index, name=modifier.name, type=modifier.type,
                                show_viewport=bool(modifier.show_viewport), show_render=bool(modifier.show_render),
                                **decision))
        elif modifier.show_viewport and not decision:
            unsupported.append(dict(index=index, name=modifier.name, type=modifier.type))
    return dict(disable_on_copy=planned, unsupported=unsupported)


def disable_on_copy(obj, planned, *, work_copy=False):
    """Keep modifier data for manual restoration; do not apply or delete geometry."""
    if not work_copy or obj.type != 'MESH' or obj.data.users != 1:
        raise ValueError('Explicit single-user work mesh required for modifier cancellation')
    # Verify the entire plan before changing any flag.
    for row in planned:
        mod = obj.modifiers[row['index']]
        actual = cancellation_reason(mod)
        if mod.name != row['name'] or mod.type != row['type'] or not actual or actual['reason'] != row['reason']:
            raise ValueError('Modifier stack changed; regenerate plan: '+obj.name)
    changes = []
    for row in planned:
        mod = obj.modifiers[row['index']]
        mod.show_viewport = mod.show_render = False
        changes.append(dict(row, action='disabled_on_work_copy', result_viewport=False, result_render=False))
    return changes
