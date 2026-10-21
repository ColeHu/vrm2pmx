"""Blender helpers with explicit installed mmd_tools package and caller-owned plans."""
import importlib
import math
from pathlib import Path


def create_material(name, settings, textures, mmd_package):
    """Create an unattached material. Caller assigns it after successful creation.

    settings requires alpha, double_sided, cast_shadow, receive_shadow,
    ground_shadow, outline_enabled/color/weight, sphere_mode; RGB defaults optional.
    textures contains base/sphere absolute paths or None, already converted.
    """
    import bpy
    if textures.get('toon') or textures.get('sphere') or settings.get('sphere_mode') or settings.get('outline_enabled') or settings.get('outline_weight'):
        raise ValueError('The authoring profile excludes Toon, Sphere and outlines')
    required = {'alpha', 'double_sided', 'cast_shadow', 'receive_shadow', 'ground_shadow',
                'outline_enabled', 'outline_color', 'outline_weight', 'sphere_mode'}
    if not required <= settings.keys():
        raise ValueError('Missing material decisions: ' + str(sorted(required - settings.keys())))
    if settings['sphere_mode'] not in (0, 1, 2, 3):
        raise ValueError('Invalid Sphere mode')
    if settings['sphere_mode'] != 0 and not textures.get('sphere'):
        raise ValueError('Enabled Sphere needs a texture')
    if not 0 <= settings['alpha'] <= 1 or settings['outline_weight'] < 0:
        raise ValueError('Invalid alpha or outline width')
    colors = {'diffuse': settings.get('diffuse', (1, 1, 1)),
              'specular': settings.get('specular', (0, 0, 0)),
              'ambient': settings.get('ambient', (.5, .5, .5)),
              'outline_color': settings['outline_color']}
    for key, color in colors.items():
        if len(color) != (4 if key == 'outline_color' else 3) or not all(math.isfinite(x) and 0 <= x <= 1 for x in color):
            raise ValueError('Invalid color: ' + key)
    for path in textures.values():
        if path and (not Path(path).is_absolute() or not Path(path).is_file()):
            raise ValueError('Expected an existing absolute texture path: ' + str(path))
    FnMaterial = importlib.import_module(mmd_package + '.core.material').FnMaterial
    if bpy.data.materials.get(name):
        raise ValueError('Material name already exists: ' + name)
    mat = bpy.data.materials.new(name)
    try:
        mat.use_nodes = True
        mat.node_tree.nodes.clear()
        mm = mat.mmd_material
        mm.name_j = settings.get('name_j', name)
        mm.name_e = settings.get('name_e', '')
        mm.diffuse_color, mm.specular_color, mm.ambient_color = colors['diffuse'], colors['specular'], colors['ambient']
        mm.alpha, mm.shininess = settings['alpha'], settings.get('shininess', 5)
        mm.is_double_sided = settings['double_sided']
        mm.enabled_drop_shadow = settings['ground_shadow']
        mm.enabled_self_shadow_map = settings['cast_shadow']
        mm.enabled_self_shadow = settings['receive_shadow']
        mm.enabled_toon_edge = settings['outline_enabled']
        mm.edge_color, mm.edge_weight = colors['outline_color'], settings['outline_weight']
        mm.sphere_texture_type = str(settings['sphere_mode'])
        mm.is_shared_toon_texture = False
        mm.toon_texture = ''
        fn = FnMaterial(mat)
        fn.remove_toon_texture()
        if textures.get('base'):
            fn.create_texture(textures['base'])
            readable=fn.get_texture()
            if readable is None or getattr(readable,'type',None)!='IMAGE' or not getattr(readable,'image',None):
                raise RuntimeError('mmd_tools exporter cannot read the created base texture')
        if settings['sphere_mode'] and textures.get('sphere'):
            fn.create_sphere_texture(textures['sphere'])
        mat.use_backface_culling = not settings['double_sided']
        # Runtime transparency decisions remain explicit and renderer-specific.
        return mat
    except Exception:
        bpy.data.materials.remove(mat)
        raise
