import math
import bpy
import bgl
import pyrpr
import numpy as np
import bmesh
import mathutils
import rprblender.core.image
from rprblender.helpers import convert_K_to_RGB
import rprblender.versions as versions

MAX_LUMINOUS_EFFICACY = 684.0

class LightError(RuntimeError):
    pass

class Light:
    def set_light_group(self, lamp):
        rpr_lamp = lamp.rpr_lamp
        group_id = 1 if rpr_lamp.group == 'KEY' else 2
        self.light.set_light_group_id(group_id)

    def set_transform(self, transform):
        self.light.set_transform(transform)

    def attach(self, scene):
        scene.attach(self.light)

    def detach(self, scene):
        scene.detach(self.light)

    def get_core_obj(self):
        return self.light

    def _get_radiant_power(self, lamp, area=0):
        rpr_lamp = lamp.rpr_lamp

        # calculating color intensity
        color = np.array(rpr_lamp.color)
        if rpr_lamp.use_temperature:
            color *= convert_K_to_RGB(rpr_lamp.temperature)
        intensity = color * rpr_lamp.intensity

        # calculate luminous efficiency
        luminous_efficiency = rpr_lamp.luminous_efficacy / MAX_LUMINOUS_EFFICACY

        # calculating radian power for core
        if lamp.type in ('POINT', 'SPOT'):
            units = rpr_lamp.intensity_units_point
            if units == 'DEFAULT':
                return intensity / (4*math.pi)  # dividing by 4*pi to be more convenient with cycles point light

            # converting to lumens
            if units == 'LUMEN':
                lumens = intensity
            elif units == 'WATTS':
                lumens = intensity * luminous_efficiency
            return lumens

        elif lamp.type == 'SUN':
            units = rpr_lamp.intensity_units_dir
            if units == 'DEFAULT':
                return intensity * 0.01         # multiplying by 0.01 to be more convenient with point light

            # converting to luminance
            if units == 'LUMINANCE':
                luminance = intensity
            if units == 'RADIANCE':
                luminance = intensity * luminous_efficiency
            return luminance

        else: 
            assert lamp.type == 'AREA'

            units = rpr_lamp.intensity_units_area
            if units == 'DEFAULT':
                if rpr_lamp.intensity_normalization:
                    return intensity / area
                return intensity

            # converting to luminance
            if units == 'LUMEN':
                luminance = intensity / area
            if units == 'WATTS':
                luminance = intensity * luminous_efficiency / area
            if units == 'LUMINANCE':
                luminance = intensity
            if units == 'RADIANCE':
                luminance = intensity * luminous_efficiency
            return luminance


class EmptyLight(Light):
    def set_transform(self, transform):
        pass

    def attach(self, scene):
        pass

    def detach(self, scene):
        pass


class IESLight(Light):
    def __init__(self, lamp, context):
        self.light = pyrpr.IESLight(context)
        self.light.set_name(lamp.name)
        power = self._get_radiant_power(lamp)
        self.light.set_radiant_power(*power)
        self.light.set_image_from_file(lamp.rpr_lamp.ies_file_name, 256, 256)
        self.set_light_group(lamp)


class PointLight(Light):
    def __init__(self, lamp, context):
        self.light = pyrpr.PointLight(context)
        power = self._get_radiant_power(lamp)
        self.light.set_radiant_power(*power)
        self.set_light_group(lamp)


class DirectionalLight(Light):
    def __init__(self, lamp, context):
        self.light = pyrpr.DirectionalLight(context)
        power = self._get_radiant_power(lamp)
        self.light.set_radiant_power(*power)
        self.light.set_shadow_softness(lamp.rpr_lamp.shadow_softness)
        self.set_light_group(lamp)


class SpotLight(Light):
    def __init__(self, lamp, context):
        self.light = pyrpr.SpotLight(context)
        power = self._get_radiant_power(lamp)
        self.light.set_radiant_power(*power)
        oangle = 0.5 * lamp.spot_size   # half of spot_size
        iangle = oangle * (1.0 - lamp.spot_blend * lamp.spot_blend)   # square dependency of spot_blend
        self.light.set_cone_shape(iangle, oangle)
        self.set_light_group(lamp)


class AreaLight(Light):
    def __init__(self, lamp, context, material_system):

        def create_image_shader(power, color_map):
            if versions.is_blender_support_custom_datablock():
                blender_image = color_map
            else:
                blender_image = bpy.data.images.load(color_map)

            core_image = rprblender.core.image.get_core_image_for_blender_image(context, blender_image)

            self.tex_shader = pyrpr.MaterialNode(material_system, pyrpr.MATERIAL_NODE_IMAGE_TEXTURE) 
            self.tex_shader.set_input('data', core_image)

            self.image_shader = pyrpr.MaterialNode(material_system, pyrpr.MATERIAL_NODE_ARITHMETIC) 
            self.image_shader.set_input('op', pyrpr.MATERIAL_NODE_OP_MUL)
            self.image_shader.set_input('color0', (*power, 1.0))
            self.image_shader.set_input('color1', self.tex_shader)

        def attach_emissive_shader(power, color_map, has_uvs):
            self.shader = pyrpr.MaterialNode(material_system, pyrpr.MATERIAL_NODE_EMISSIVE)
        
            if color_map and has_uvs:
                create_image_shader(power, color_map)
                self.shader.set_input('color', self.image_shader)
            else:
                self.shader.set_input('color', (*power, 1.0))
            
            self.shader.set_name("EmmisiveMaterial")
            self.light.set_material(self.shader)


        (vertices, normals, uvs, vert_ind, norm_ind, uvs_ind, num_face_verts, area) = self._get_mesh_prop(lamp.rpr_lamp, lamp.rpr_lamp.size_1, lamp.rpr_lamp.size_2)

        if area < np.finfo(dtype=np.float32).eps: 
            raise LightError("Surface area of mesh is equal to zero")

        power = self._get_radiant_power(lamp, area)

        self.light = pyrpr.Mesh(context, vertices, normals, uvs, 
                                vert_ind, norm_ind, uvs_ind, 
                                num_face_verts)
        self.light.set_name(lamp.name)

        attach_emissive_shader(power, lamp.rpr_lamp.color_map, not uvs is None)

        self.light.set_visibility_ex('visible.light', lamp.rpr_lamp.visible)
        self.light.set_shadow(lamp.rpr_lamp.visible and lamp.rpr_lamp.cast_shadows)
        self.set_light_group(lamp)


    def _get_mesh_prop(self, rpr_lamp, size_1, size_2, segments=32):
        bm = bmesh.new()
        try:
            if rpr_lamp.shape == 'RECTANGLE':
                matrix=mathutils.Matrix.Scale(size_1, 4, (1, 0, 0)) * mathutils.Matrix.Scale(size_2, 4, (0, 1, 0))
                bmesh.ops.create_grid(bm, x_segments=1, y_segments=1, size=0.5, 
                                        matrix=matrix)

            elif rpr_lamp.shape == 'DISC':
                bmesh.ops.create_circle(bm, cap_ends=True, cap_tris=True, 
                                        segments=segments, diameter=size_1)     # Blender's bug: here diameter is radius

            elif rpr_lamp.shape == 'SPHERE':
                bmesh.ops.create_icosphere(bm, subdivisions=3, diameter=size_1) # Blender's bug: here diameter is radius

            elif rpr_lamp.shape == 'CYLINDER':
                bmesh.ops.create_cone(bm, cap_ends=True, cap_tris=True, 
                                      segments=segments, 
                                      diameter1=size_1, diameter2=size_1,       # Blender's bug: here diameter is radius
                                      depth=size_2)
            else: # 'MESH'
                assert rpr_lamp.shape == 'MESH'

                if not rpr_lamp.mesh_obj:
                    raise LightError("Mesh object for area light not selected")

                if versions.is_blender_support_custom_datablock():
                    mesh_obj = rpr_lamp.mesh_obj
                else:
                    mesh_obj = bpy.data.objects.get(rpr_lamp.mesh_obj, None)
                    if not mesh_obj:
                        raise LightError("Mesh object '%s' for area light not exists" % rpr_lamp.mesh_obj)

                if mesh_obj.type != 'MESH':
                    raise LightError("Mesh object for area light is not a 'MESH'")

                bm.from_object(mesh_obj, bpy.context.scene)
                bmesh.ops.triangulate(bm, faces=bm.faces)

            if len(bm.faces) == 0:
                raise LightError("No faces for area light mesh")

            # rotate mesh around Y axis
            bmesh.ops.rotate(bm, matrix=mathutils.Matrix.Rotation(math.pi, 4, 'Y'), verts=bm.verts)

            bm.faces.ensure_lookup_table()
            num_face_verts = np.zeros((len(bm.faces),), dtype=np.int32)
            normals = np.zeros((len(bm.faces), 3), dtype=np.float32)
            vert_ind = np.zeros((len(bm.faces)*4,), dtype=np.int32)
            norm_ind = np.zeros((len(bm.faces)*4,), dtype=np.int32)
            
            uvs = None
            uvs_ind = None
            uv_lay = None
            if rpr_lamp.shape == 'MESH' and len(bm.loops.layers.uv) > 0:
                uvs = np.zeros((len(bm.faces)*4, 2), dtype=np.float32)
                uvs_ind = np.zeros((len(bm.faces)*4,), dtype=np.int32)
                uv_lay = bm.loops.layers.uv.active

            ind = 0
            area = 0.0
            for i in range(len(bm.faces)):
                bm_face = bm.faces[i]
                num_face_verts[i] = len(bm_face.verts)
                normals[i] = bm_face.normal
               
                for j in range(num_face_verts[i]):
                    vert_ind[ind] = bm_face.verts[j].index
                    norm_ind[ind] = i
                    if uv_lay:
                        uvs[ind] = bm_face.loops[j][uv_lay].uv
                        uvs_ind[ind] = ind
                    ind += 1

                area += bm.faces[i].calc_area()

            vert_ind = vert_ind[:ind]
            norm_ind = norm_ind[:ind]
            if uv_lay:
                uvs = uvs[:ind]
                uvs_ind = uvs_ind[:ind]


            bm.verts.ensure_lookup_table()
            vertices = np.zeros((len(bm.verts), 3), dtype=np.float32)
            if rpr_lamp.shape == 'RECTANGLE' or rpr_lamp.shape == 'DISC':
                uvs = np.zeros((len(bm.verts), 2), dtype=np.float32)
                uvs_ind = vert_ind
                
            for i in range(len(bm.verts)):
                vertices[i] = bm.verts[i].co
                if rpr_lamp.shape == 'RECTANGLE':
                    uvs[i] = ((vertices[i][0] + size_1*0.5)/size_1, (vertices[i][1] + size_2*0.5)/size_2)
                elif rpr_lamp.shape == 'DISC':
                    uvs[i] = ((vertices[i][0] + size_1)/(2*size_1), (vertices[i][1] + size_1)/(2*size_1))

        finally:
            bm.free()

        return (vertices, normals, uvs, vert_ind, norm_ind, uvs_ind, num_face_verts, area)


def callback_light_draw():
    def get_circle_points(radius, segments=32):
        for i in range(segments):
            yield (radius*math.cos(2.0*math.pi*i/segments), radius*math.sin(2.0*math.pi*i/segments))

    def draw_area_light_gizmo(lamp):
        if lamp.shape == 'RECTANGLE':
            x = lamp.size_1*0.5
            y = lamp.size_2*0.5
            bgl.glBegin(bgl.GL_LINE_LOOP)
            bgl.glVertex3f(-x, -y, 0.0)
            bgl.glVertex3f(-x, y, 0.0)
            bgl.glVertex3f(x, y, 0.0)
            bgl.glVertex3f(x, -y, 0.0)
            bgl.glEnd()
        elif lamp.shape == 'DISC':
            bgl.glBegin(bgl.GL_LINE_LOOP)
            for (x, y) in get_circle_points(lamp.size_1):
                bgl.glVertex3f(x, y, 0.0)
            bgl.glEnd()
        elif lamp.shape == 'SPHERE':
            r = lamp.size_1
            bgl.glBegin(bgl.GL_LINE_LOOP)
            for (x, y) in get_circle_points(r):
                bgl.glVertex3f(x, y, 0)
            bgl.glEnd()
            bgl.glBegin(bgl.GL_LINE_LOOP)
            for (x, z) in get_circle_points(r):
                bgl.glVertex3f(x, 0, z)
            bgl.glEnd()
            bgl.glBegin(bgl.GL_LINE_LOOP)
            for (y, z) in get_circle_points(r):
                bgl.glVertex3f(0, y, z)
            bgl.glEnd()
        elif lamp.shape == 'CYLINDER':
            r = lamp.size_1
            z = lamp.size_2*0.5
            bgl.glBegin(bgl.GL_LINE_LOOP)
            for (x, y) in get_circle_points(r):
                bgl.glVertex3f(x, y, -z)
            bgl.glEnd()
            bgl.glBegin(bgl.GL_LINE_LOOP)
            for (x, y) in get_circle_points(r):
                bgl.glVertex3f(x, y, z)
            bgl.glEnd()
            bgl.glBegin(bgl.GL_LINES)
            for (x, y) in get_circle_points(r, 4):
                bgl.glVertex3f(x, y, -z)
                bgl.glVertex3f(x, y, z)
            bgl.glEnd()
        else: # 'MESH'
            if not lamp.mesh_obj:
                return

            if versions.is_blender_support_custom_datablock():
                mesh_obj = lamp.mesh_obj
            else:
                mesh_obj = bpy.data.objects.get(lamp.mesh_obj, None)
                if not mesh_obj:
                    return

            if mesh_obj.type != 'MESH':
                return

            mesh = mesh_obj.data
            if len(mesh.polygons) > 0:
                for poly in mesh.polygons:
                    bgl.glBegin(bgl.GL_LINE_LOOP)
                    for vert_ind in poly.vertices:
                        bgl.glVertex3f(*mesh.vertices[vert_ind].co)
                    bgl.glEnd()
            else:
                bgl.glBegin(bgl.GL_LINES)
                for edge in mesh.edges:
                    bgl.glVertex3f(*mesh.vertices[edge.vertices[0]].co)
                    bgl.glVertex3f(*mesh.vertices[edge.vertices[1]].co)
                bgl.glEnd()


    if bpy.context.scene.render.engine != 'RPR':
        return

    for obj in bpy.context.visible_objects:
        if obj.type != 'LAMP':
            continue
        lamp = obj.data
        if lamp.type != 'AREA':
            continue

        #transform coordinate system to object 
        bgl.glPushMatrix()
        m = bgl.Buffer(bgl.GL_FLOAT, 16)
        m[0:16] = (*obj.matrix_world[0], *obj.matrix_world[1], *obj.matrix_world[2], *obj.matrix_world[3])
        bgl.glMultTransposeMatrixf(m)
        # rotate around Y axis
        bgl.glRotatef(180.0, 0.0, 1.0, 0.0) 

        # setting corresponded color to Blender objects wireframe color
        if obj in bpy.context.selected_objects:
            if obj == bpy.context.object:
                bgl.glColor4f(1.0, 0.666, 0.25, 1.0)
            else:
                bgl.glColor4f(0.945, 0.345, 0.0, 1.0)
        else:
            bgl.glColor4f(0.0, 0.0, 0.0, 1.0)

        bgl.glLineWidth(1)
        bgl.glEnable(bgl.GL_LINE_STIPPLE); 

        draw_area_light_gizmo(lamp.rpr_lamp)

        bgl.glDisable(bgl.GL_LINE_STIPPLE);
        bgl.glColor4f(0.0, 0.0, 0.0, 1.0)

        #transform coordinate system to previous state
        bgl.glPopMatrix()


# Setting draw viewport handler    
handle_light_draw = bpy.types.SpaceView3D.draw_handler_add(callback_light_draw, (), 'WINDOW', 'POST_VIEW')
