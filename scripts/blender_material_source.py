"""Trace supported effective Blender outputs into base color and opacity only."""
import math


def inspect_surface(material, image_info, empty_texture):
    """Ignore disconnected nodes; support explicit image/Principled/transparent mix."""
    def source(socket):
        if len(socket.links)!=1:
            raise ValueError('Expected exactly one effective link: '+material.name+'/'+socket.name)
        return socket.links[0]

    def texture(node):
        if node.type!='TEX_IMAGE' or not node.image or node.projection!='FLAT' or node.extension not in ('REPEAT','EXTEND','MIRROR'):
            raise ValueError('Image projection/source/sampler needs an adapter: '+material.name)
        row=dict(empty_texture(),image=node.image.name,descriptor=image_info(node.image),
                 encoding='srgb' if node.image.colorspace_settings.name=='sRGB' else 'linear',uv_map=None)
        row['wrap']=[{'REPEAT':10497,'EXTEND':33071,'MIRROR':33648}[node.extension]]*2
        vector=node.inputs['Vector']
        if vector.is_linked:
            link=source(vector);uv=link.from_node
            if uv.type=='MAPPING':
                if uv.vector_type!='POINT' or any(uv.inputs[key].is_linked for key in ('Location','Rotation','Scale')):
                    raise ValueError('Animated/linked Mapping needs an adapter: '+material.name)
                location=list(uv.inputs['Location'].default_value)
                rotation=list(uv.inputs['Rotation'].default_value)
                scale=list(uv.inputs['Scale'].default_value)
                if max(abs(rotation[i]) for i in (0,1))>1e-8:
                    raise ValueError('Only planar Mapping is supported: '+material.name)
                row.update(offset=location[:2],scale=scale[:2],rotation=rotation[2])
                link=source(uv.inputs['Vector']);uv=link.from_node
            if uv.type=='UVMAP':
                row['uv_map']=uv.uv_map or None
            elif uv.type!='TEX_COORD' or link.from_socket.name!='UV':
                raise ValueError('Expected UV coordinates: '+material.name)
        return row

    def color(socket):
        if not socket.is_linked:
            return list(socket.default_value),empty_texture()
        link=source(socket)
        if link.from_node.type!='TEX_IMAGE' or link.from_socket.name!='Color':
            raise ValueError('Complex base-color network needs an adapter: '+material.name)
        return [1,1,1,1],texture(link.from_node)

    def opacity(socket):
        if not socket.is_linked:
            value=float(socket.default_value)
            if not math.isfinite(value) or not 0<=value<=1:
                raise ValueError('Opacity factor must be in [0,1]')
            return value,None
        link=source(socket)
        if link.from_node.type!='TEX_IMAGE' or link.from_socket.name not in ('Alpha','Color'):
            raise ValueError('Complex opacity network needs an adapter: '+material.name)
        tex=texture(link.from_node)
        tex['channel']='alpha' if link.from_socket.name=='Alpha' else 'grayscale_rgb'
        return 1,tex

    def parse(link):
        node=link.from_node
        row=dict(kind='blender_surface',base_factor=[1,1,1,1],alpha_mode='OPAQUE',alpha_cutoff=.5,
                 base_alpha_used=False,textures={'base':empty_texture()},warnings=[])
        if node.type=='TEX_IMAGE' and link.from_socket.name=='Color':
            row['kind']='image_to_surface'
            row['textures']['base']=texture(node)
            row['warnings'].append('Effective image Color-to-Surface retained as base color; disconnected shaders ignored')
        elif node.type=='BSDF_PRINCIPLED':
            row['kind']='principled'
            row['base_factor'],row['textures']['base']=color(node.inputs['Base Color'])
            value,mask=opacity(node.inputs['Alpha'])
            row['base_factor'][3]=value
            if mask:
                row['textures']['alpha']=mask
            if mask or value<1:
                row['alpha_mode']='BLEND'
            row['warnings'].append('Effective Principled base/alpha retained; PBR response omitted')
        elif node.type=='EMISSION':
            if node.inputs['Strength'].is_linked or abs(node.inputs['Strength'].default_value-1)>1e-6:
                raise ValueError('Non-unit emission strength needs an adapter: '+material.name)
            row['kind']='emission_base_color'
            row['base_factor'],row['textures']['base']=color(node.inputs['Color'])
            row['base_factor'][3]=1
            row['warnings'].append('Emission color retained as base color; emissive lighting omitted')
        elif node.type=='MIX_SHADER':
            branches=[source(node.inputs[i]) for i in (1,2)]
            transparent=[i for i,branch in enumerate(branches) if branch.from_node.type=='BSDF_TRANSPARENT']
            if len(transparent)!=1:
                raise ValueError('Only a single Transparent/colored Mix Shader is supported: '+material.name)
            side=transparent[0]
            trans=branches[side].from_node.inputs['Color']
            if trans.is_linked or any(abs(v-1)>1e-6 for v in trans.default_value[:3]):
                raise ValueError('Tinted transparency needs an adapter: '+material.name)
            row=parse(branches[1-side])
            if row['textures'].get('alpha'):
                raise ValueError('Stacked opacity networks need an adapter: '+material.name)
            value,mask=opacity(node.inputs[0])
            if mask:
                mask['invert']=side==1
                row['textures']['alpha']=mask
            else:
                row['base_factor'][3]*=1-value if side==1 else value
            row['kind']='transparent_mix'
            row['alpha_mode']='BLEND'
            row['warnings'].append('Effective Transparent Mix opacity baked into base alpha')
        else:
            raise ValueError('Unsupported effective Surface node: '+material.name+'/'+node.bl_idname)
        return row

    outputs=[n for n in material.node_tree.nodes if n.type=='OUTPUT_MATERIAL' and n.is_active_output]
    if len(outputs)!=1:
        raise ValueError('Expected one active material output: '+material.name)
    if material.node_tree.animation_data:
        raise ValueError('Animated material needs a static adapter: '+material.name)
    return parse(source(outputs[0].inputs['Surface']))
