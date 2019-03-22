from dataclasses import dataclass
import numpy as np
import math

import bpy
import bmesh
import mathutils

import pyrpr
from rprblender.properties import SyncError
from rprblender.engine.context import RPRContext
from . import object, material

from rprblender.utils import logging
log = logging.Log(tag='export.mesh')


@dataclass(init=False)
class MeshData:
    """ Dataclass which holds all mesh settings. It is used also for area lights creation """

    vertices: np.array
    normals: np.array
    uvs: np.array
    vertex_indices: np.array
    normal_indices: np.array
    uv_indices: np.array
    num_face_vertices: np.array
    area: float = None

    @staticmethod
    def init_from_mesh(mesh: bpy.types.Mesh, calc_area=False):
        """ Returns MeshData from bpy.types.Mesh """

        data = MeshData()

        # preparing mesh to export
        mesh.calc_normals_split()
        mesh.calc_loop_triangles()

        # getting mesh export data
        tris_len = len(mesh.loop_triangles)
        if tris_len == 0:
            raise SyncError("Mesh %s has no polygons" % mesh.name, mesh)

        data.vertices = np.array([vert.co for vert in mesh.vertices], dtype=np.float32)
        data.normals = np.array(
            [norm for tri in mesh.loop_triangles
                  for norm in tri.split_normals],
            dtype=np.float32
        )

        data.uvs = None
        data.uv_indices = None
        if len(mesh.uv_layers) > 0:
            uv_layer = mesh.uv_layers.active
            uvs = np.array([[d.uv.x, d.uv.y] for d in uv_layer.data], dtype=np.float32)
            if len(uvs) > 0:
                data.uvs = uvs
                data.uv_indices = np.array([tri.loops for tri in mesh.loop_triangles], dtype=np.int32).reshape((tris_len * 3,))

        data.num_face_vertices = np.full((tris_len,), 3, dtype=np.int32)
        data.vertex_indices = np.array([tri.vertices for tri in mesh.loop_triangles], dtype=np.int32).reshape((tris_len * 3,))
        data.normal_indices = np.arange(tris_len * 3, dtype=np.int32)

        if calc_area:
            data.area = sum(tri.area for tri in mesh.loop_triangles)

        return data

    @staticmethod
    def init_from_shape_type(shape_type, size, size_y, segments):
        """
        Returns MeshData depending of shape_type of area light.
        Possible values of shape_type: 'SQUARE', 'RECTANGLE', 'DISK', 'ELLIPSE'
        """

        bm = bmesh.new()
        try:
            if shape_type in ('SQUARE', 'RECTANGLE'):
                bmesh.ops.create_grid(bm, x_segments=1, y_segments=1, size=0.5)

            elif shape_type in ('DISK', 'ELLIPSE'):
                bmesh.ops.create_circle(bm, cap_ends=True, cap_tris=True, segments=segments, radius=0.5)

            else:
                raise TypeError("Incorrect shape type", shape_type)

            data = MeshData()

            # getting uvs before modifying mesh
            bm.verts.ensure_lookup_table()
            data.uvs = np.array([(vert.co[0] + 0.5, vert.co[1] + 0.5) for vert in bm.verts], dtype=np.float32)

            # scale and rotate mesh around Y axis
            bmesh.ops.scale(bm, verts=bm.verts,
                            vec=(size, size if shape_type in ('SQUARE', 'DISK') else size_y, 1.0))
            bmesh.ops.rotate(bm, verts=bm.verts,
                             matrix=mathutils.Matrix.Rotation(math.pi, 4, 'Y'))

            # preparing mesh to get data
            bm.verts.ensure_lookup_table()
            bm.faces.ensure_lookup_table()
            loop_triangles = bm.calc_loop_triangles()
            tris_len = len(loop_triangles)

            data.vertices = np.array([vert.co for vert in bm.verts], dtype=np.float32)
            data.normals = np.array([vert.normal for vert in bm.verts], dtype=np.float32)

            data.num_face_vertices = np.full((tris_len,), 3, dtype=np.int32)
            data.vertex_indices = np.array([vert.vert.index for tri in loop_triangles for vert in tri], dtype=np.int32)
            data.normal_indices = data.vertex_indices
            data.uv_indices = data.vertex_indices

            data.area = sum(face.calc_area() for face in bm.faces)

            return data

        finally:
            bm.free()

def assign_materials(rpr_context: RPRContext, rpr_shape: pyrpr.Shape, obj: bpy.types.Object):
    """ Assigns materials from obj.material_slots to rpr_shape. It also syncs new material """

    if len(obj.material_slots) == 0:
        return False

    mesh = obj.data

    material_indices = np.array([tri.material_index for tri in mesh.loop_triangles], dtype=np.int32)
    material_unique_indices = np.unique(material_indices)
    for i in material_unique_indices:
        slot = obj.material_slots[i]

        log("Syncing material '%s'" % slot.name, slot)

        if not slot.material:
            continue

        rpr_material = material.sync(rpr_context, slot.material)

        if rpr_material:
            if len(material_unique_indices) == 1:
                rpr_shape.set_material(rpr_material)
            else:
                # It is important not to remove previous unused materials here, because core could crash.
                # They will be in memory till mesh exists.
                face_indices = np.array(np.where(material_indices == i)[0], dtype=np.int32)
                rpr_shape.set_material_faces(rpr_material, face_indices)
        else:
            rpr_shape.set_material(None)

    return True


def sync(rpr_context: RPRContext, obj: bpy.types.Object):
    """ Creates pyrpr.Shape from obj.data:bpy.types.Mesh """

    mesh = obj.data
    log("sync", mesh, obj)

    data = MeshData.init_from_mesh(mesh)
    rpr_shape = rpr_context.create_mesh(
        object.key(obj),
        data.vertices, data.normals, data.uvs,
        data.vertex_indices, data.normal_indices, data.uv_indices,
        data.num_face_vertices
    )

    assign_materials(rpr_context, rpr_shape, obj)

    rpr_context.scene.attach(rpr_shape)
    rpr_shape.set_transform(object.get_transform(obj))
    obj.rpr.export_visibility(rpr_shape)
    obj.rpr.export_subdivision(rpr_shape)

def sync_update(rpr_context: RPRContext, obj: bpy.types.Object, is_updated_geometry, is_updated_transform):
    """ Update existing mesh from obj.data: bpy.types.Mesh or create a new mesh """

    mesh = obj.data
    log("Updating mesh: %s" % mesh.name)

    obj_key = object.key(obj)
    rpr_shape = rpr_context.objects.get(obj_key, None)
    if rpr_shape:
        if is_updated_geometry:
            rpr_context.remove_object(obj_key)
            sync(rpr_context, obj)
            return True

        if is_updated_transform:
            rpr_shape.set_transform(object.get_transform(obj))
            return True

        return assign_materials(rpr_context, rpr_shape, obj)

    sync(rpr_context, obj)
    return True
