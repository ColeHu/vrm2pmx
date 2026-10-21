"""Apply explicit semantic collection plans inside Blender, without editing bones.

Plan keys: collections [{name, parent}], assignments {internal_bone: [collection]},
unresolved [internal_bone]. Build the plan from humanoid/conversion mappings and
reviewed accessory chains; use final internal names after conversion.
"""


def _armature(name):
    import bpy
    arm = bpy.data.objects.get(name)
    if arm is None or arm.type != 'ARMATURE' or arm.mode == 'EDIT':
        raise ValueError('Expected armature outside Edit Mode: ' + name)
    return arm


def inspect_collections(armature_name):
    arm = _armature(armature_name)
    return {'collections': [
        {'name': c.name, 'parent': c.parent.name if c.parent else None,
         'visible': c.is_visible, 'bones': [b.name for b in c.bones]}
        for c in arm.data.collections_all],
         'unassigned': [b.name for b in arm.data.bones if not b.collections]}


def prune_empty_collections(armature_name, candidates=None, *, plan=None):
    """Remove empty leaf collections, recursively, on a single-user work rig.

    Keep empty organizational parents when a nonempty child still uses them.
    Synchronize the saved collection plan so subsequent validation stays valid.
    """
    import json
    arm = _armature(armature_name)
    if arm.data.users > 1:
        raise ValueError('Use a single-user work armature before collection cleanup')
    pending = set(candidates if candidates is not None else (c.name for c in arm.data.collections_all))
    removed = []
    while True:
        leaves = [c for c in arm.data.collections_all
                  if c.name in pending and not c.bones and not c.children]
        if not leaves:
            break
        for collection in leaves:
            removed.append(collection.name)
            arm.data.collections.remove(collection)
    if plan is None and arm.get('auto_rig_collection_plan'):
        plan = json.loads(arm['auto_rig_collection_plan'])
    if plan is not None:
        plan['collections'] = [s for s in plan['collections'] if s['name'] not in removed]
        if arm.get('auto_rig_collection_plan'):
            arm['auto_rig_collection_plan'] = json.dumps(plan, ensure_ascii=False)
    return dict(status='passed', removed=removed, count=len(removed))


def _check_plan(arm, plan):
    specs = plan['collections']
    names = [c['name'] for c in specs]
    if len(names) != len(set(names)) or not all(names):
        raise ValueError('Expected unique nonempty collection names')
    parents = {c['name']: c.get('parent') for c in specs}
    for name in names:
        visited, current = set(), name
        while current is not None:
            if current in visited or current not in parents:
                raise ValueError('Invalid collection hierarchy: ' + name)
            visited.add(current)
            current = parents[current]
    actual = {b.name for b in arm.data.bones}
    assignments = plan['assignments']
    if set(assignments) != actual:
        raise ValueError('Plan must cover every final bone exactly once as a key; '
                         'missing=' + str(sorted(actual-set(assignments))) +
                         ', stale=' + str(sorted(set(assignments)-actual)))
    for name, memberships in assignments.items():
        if not isinstance(memberships, list) or not memberships or len(set(memberships)) != len(memberships):
            raise ValueError('Expected nonempty unique memberships: ' + name)
        if not set(memberships) <= set(names):
            raise ValueError('Unknown planned collection: ' + name)
    if plan.get('unresolved'):
        raise ValueError('Resolve classification before applying: ' + str(plan['unresolved']))


def validate_collections(armature_name, plan):
    arm = _armature(armature_name)
    try:
        _check_plan(arm, plan)
    except (ValueError, KeyError, TypeError) as exc:
        return {'status': 'failed', 'scope': 'bone_collections', 'issues': [str(exc)]}
    existing = {c.name: c for c in arm.data.collections_all}
    managed = {c['name'] for c in plan['collections']}
    issues = []
    for spec in plan['collections']:
        collection = existing.get(spec['name'])
        if collection is None:
            issues.append({'issue': 'missing_collection', 'collection': spec['name']})
        elif (collection.parent.name if collection.parent else None) != spec.get('parent'):
            issues.append({'issue': 'collection_parent', 'collection': spec['name']})
    for bone in arm.data.bones:
        actual = {c.name for c in bone.collections}
        expected = set(plan['assignments'][bone.name])
        if actual & managed != expected:
            issues.append({'issue': 'wrong_membership', 'bone': bone.name,
                           'expected': sorted(expected), 'actual': sorted(actual & managed)})
        if not actual:
            issues.append({'issue': 'unassigned_bone', 'bone': bone.name})
    return {'status': 'failed' if issues else 'passed', 'scope': 'bone_collections',
            'bone_count': len(arm.data.bones), 'issues': issues,
            'counts': {n: len(existing[n].bones) for n in managed if n in existing}}


def apply_collections(armature_name, plan):
    """Idempotent membership update; preserve memberships outside the managed set.

    Existing collection hierarchy is reused. Conflicts are rejected before
    modification. New collection hierarchy and visibility follow the plan.
    """
    arm = _armature(armature_name)
    if arm.data.users > 1:
        raise ValueError('Use a single-user armature before changing shared collections')
    _check_plan(arm, plan)
    existing = {c.name: c for c in arm.data.collections_all}
    specs = {s['name']: s for s in plan['collections']}
    for name, spec in specs.items():
        if name in existing:
            parent = existing[name].parent
            if (parent.name if parent else None) != spec.get('parent'):
                raise ValueError('Existing collection hierarchy conflict: ' + name)
    before = {b.name: [c.name for c in b.collections] for b in arm.data.bones}
    created = []
    try:
        pending = [n for n in specs if n not in existing]
        while pending:
            for name in pending[:]:
                spec = specs[name]
                parent = spec.get('parent')
                if parent is not None and parent not in existing:
                    continue
                c = arm.data.collections.new(name, parent=existing.get(parent))
                existing[name] = c
                created.append(c)
                c.is_visible = spec.get('visible', True)
                pending.remove(name)
        managed = set(specs)
        for name, memberships in plan['assignments'].items():
            bone = arm.data.bones[name]
            for c in list(bone.collections):
                if c.name in managed and c.name not in memberships:
                    c.unassign(bone)
            for collection in memberships:
                existing[collection].assign(bone)
        report = validate_collections(armature_name, plan)
        if report['status'] != 'passed':
            raise RuntimeError(str(report['issues']))
        return report
    except Exception:
        for bone in arm.data.bones:
            for c in list(bone.collections):
                c.unassign(bone)
            for name in before[bone.name]:
                existing[name].assign(bone)
        for c in reversed(created):
            arm.data.collections.remove(c)
        raise
