import traceback
from pathlib import Path

import bpy.utils.previews

from rprblender import logging
from rprblender.helpers import CallLogger
import rprblender.render.scene
import rprblender.core.nodes
import rprblender.render
from rprblender.nodes import RPRPanel

import pyrpr
from pyrpr import ffi
import numpy as np
import os
import sys
import threading
import time
from . import config

from . import rpraddon

import pyrprx

call_logger = CallLogger(tag='sync')


def log_thumbnails(*args):
    logging.debug(*args, tag='thumbnails')


# NodeThumbnailManager manages node thumbnails for all materials. It provides the RPR
# context and scene for rendering, and maintains a collection of active thumbnails.
class NodeThumbnailManager:
    def __init__(self):
        self.enabled = config.node_thumbnail_enabled

        # Thumbnail settings.
        self.size = 128
        self.iterations = 50
        self.debug_output = False
        self.write_to_file = False

        # Thumbnail collection.
        self.thumbnails = {}

        self.materials_for_render = {}

        self.materials_queue = {}

        # Only initialize RPR is enabled.
        if not self.enabled:
            return

        # Acquire the render lock for context creation. RPR deadlocks
        # if context creation and render calls occur synchronously.
        with rprblender.render.core_operations():
            settings = bpy.context.scene.rpr.render_thumbnail  # type: rprblender.properties.RenderSettings

            self.scene_renderer = rprblender.render.scene.SceneRenderer(
                rprblender.render.get_render_device(is_production=False),
                settings, False)
            self.scene_renderer_lock = threading.Lock()  # to make sure only one thread renders thumbnail at a time

            aov_extracted = rprblender.render.render_layers.extract_settings(bpy.context.scene.rpr.thumbnails_aov)
            self.scene_renderer.update_aov(aov_extracted)
            self.scene_renderer.update_render_resolution((self.size, self.size))

            # Create the shared render context and scene state used by all thumbnails.
            self.context = self.scene_renderer.context
            self.scene = self.create_scene()
            self.camera = self.create_camera()
            self.mesh = self.create_mesh()
            self.material_system = pyrpr.MaterialSystem(self.context)
            self.uber_rprx_context = pyrprx.Context(self.material_system)

            ibl_map = str(Path(rprblender.__file__).parent / 'img/env.hdr')
            self.light, self.ibl_img = self.create_environment_light(ibl_map)

            self.back_mesh, self.back_shader, self.back_checker = self.create_back()

            # Create a material for parsing nodes, and a default
            # shader for rendering a node if it's not a shader itself.
            self.material = rprblender.core.nodes.Material(self)
            self.material.output_node_was_parsed = True
            self.shader = rprblender.core.nodes.DiffuseShader(self.material)

            self.thread = None
            self.lock = self.scene_renderer_lock

    @call_logger.logged
    def __del__(self):

        if self.uber_rprx_context != None:
            pyrprx.DeleteContext(self.uber_rprx_context)

        self.context = None
        self.scene = None
        self.frame_buffer = None
        self.camera = None
        self.mesh = None

        self.back_mesh = None
        self.back_shader = None
        self.back_checker = None

        self.material_system = None
        self.light = None
        self.ibl_img = None
        self.material = None
        self.shader = None

    def get_node_id(self, material_node):
        node_tree = self.get_node_tree()
        assert node_tree
        node_name = material_node.name
        value = material_node.as_pointer()
        hash_val = hash(value)
        sh = str(hash_val)
        return node_tree.name + node_name + "_" + sh

    def get_thumbnail(self, material_node):
        if not self.enabled:
            return None

        # Clean up thumbnails for deleted or renamed nodes.
        self.clean_up_thumbnails()

        # Create a unique node ID based on the node
        # name and the name of it's containing tree.
        node_tree = self.get_node_tree()
        if not node_tree:
            return None
        if not material_node:
            return None

        node_name = material_node.name
        node_id = self.get_node_id(material_node)

        # Create a new thumbnail if not already cached. Create a tuple
        # with the name and tree so the thumbnail can be cleaned up later.
        if node_id not in self.thumbnails:
            el = NodeThumbnail(self)
            el.nodeid = node_id
            self.thumbnails[node_id] = (el, node_name, node_tree)

        # Return the cached thumbnail.
        return self.thumbnails[node_id][0]

    def clean_up_thumbnails(self):
        # Check each existing thumbnail.
        for key, value in list(self.thumbnails.items()):

            thumbnail = value[0]
            name = value[1]
            tree = value[2]

            # Delete a thumbnail if its name is not in its node tree.
            if tree is not None and tree.nodes.find(name) < 0:
                thumbnail.clean_up()
                del self.thumbnails[key]

    def get_node_tree(self):
        if not hasattr(bpy.context, 'active_object'):
            return None

        # Get the active object.
        active_object = bpy.context.active_object
        if active_object is None:
            return None

        # Get the active material.
        material = active_object.active_material
        if material is None:
            return None

        # Get the material node tree.
        return material.node_tree

    def get_material_system(self):
        return self.material_system

    def get_uber_rprx_context(self):
        return self.uber_rprx_context

    def create_scene(self):
        scene = pyrpr.Scene(self.context)
        self.context.set_scene(scene)
        return scene

    def create_camera(self):
        camera = pyrpr.Camera(self.context)
        camera.look_at((0, 0, 7), (0, 0, 0), (0, 1, 0))
        self.scene.set_camera(camera)
        return camera

    def create_mesh(self, size=3, z=0):
        vertices = np.array([[-size, size, z],
                             [size, size, z],
                             [size, -size, z],
                             [-size, -size, z]], dtype=np.float32)

        normals = np.array([[0.0, 0.0, 1.0],
                            [0.0, 0.0, 1.0],
                            [0.0, 0.0, 1.0],
                            [0.0, 0.0, 1.0]], dtype=np.float32)
        uvs = np.array([[0.0, 0.0],
                        [1.0, 0.0],
                        [1.0, 1.0],
                        [0.0, 1.0]], dtype=np.float32)
        indices = np.array([3, 2, 1, 0], dtype=np.int32)
        num_face_vertices = np.array([4,], dtype=np.int32)

        mesh = pyrpr.Mesh(self.context,
                          vertices, normals, uvs,
                          indices, indices, indices,
                          num_face_vertices)

        self.scene.attach(mesh)
        return mesh

    def create_back(self):
        mesh = self.create_mesh(z=-0.05)
        shader = pyrpr.MaterialNode(self.material_system, pyrpr.MATERIAL_NODE_DIFFUSE)
        checker = pyrpr.MaterialNode(self.material_system, pyrpr.MATERIAL_NODE_CHECKER_TEXTURE)
        shader.set_input('color', checker)
        mesh.set_material(shader)
        return mesh, shader, checker

    def create_environment_light(self, image_path):
        image_path = bpy.path.native_pathsep(bpy.path.abspath(image_path))
        image = None
        assert os.path.isfile(image_path)

        try:
            image = bpy.data.images.load(image_path, True)
        except RuntimeError:
            raise
        assert image
        pixels = image.pixels[:]
        im = np.array(pixels, dtype=np.float32).reshape(image.size[1], image.size[0], 4)
        if not image.users:
            bpy.data.images.remove(image)

        return self.environment_light_from_image_data(im)

    def environment_light_from_image_data(self, im):
        img = pyrpr.Image(self.context, data=im)
        ibl = pyrpr.EnvironmentLight(self.context)
        ibl.set_image(img)
        envmap_transform_fixup = [[1, 0, 0, 0],
                                  [0, 0, 1, 0],
                                  [0, 1, 0, 0],
                                  [0, 0, 0, 1], ]
        matrix = np.array(envmap_transform_fixup, dtype=np.float32)
        ibl.set_transform(matrix)
        self.scene.attach(ibl)
        return ibl, img

    def get_exported_material_node(self, material_node):
        log_thumbnails('get_exported_material_node...')

        if "shader" in material_node.bl_idname:
            # Apply shader nodes directly to the geometry.
            log_thumbnails('parse_shader... ', material_node.name)
            shader = self.material.parse_node(None, material_node)
        else:
            # Apply intermediate nodes to a default diffuse shader.
            log_thumbnails('parse_node... ', material_node.name)
            material = rprblender.core.nodes.Material(self)
            shader = rprblender.core.nodes.DiffuseShader(material)
            value = self.material.parse_node(None, material_node)
            shader.set_color(value)

        log_thumbnails('parse ok...')
        return shader

    def get_frame_buffer_pixels(self):
        return self.scene_renderer.get_image()

    def render(self):
        log_thumbnails("render thumbnail...")
        try:
            while True:
                time.sleep(0.2)
                try:
                    self.lock.acquire()
                    log_thumbnails("lock.acquire (render)")

                    try:
                        item = self.materials_for_render.popitem()
                    except KeyError:
                        break

                    node_id = item[0]
                    shader = item[1]

                    log_thumbnails("  set material for: ", node_id)
                    self.mesh.set_material(shader.handle)

                    for i in self.scene_renderer.render_proc():
                        pass

                    # Update the thumbnail preview image.
                    if node_id in self.thumbnails:
                        thumbnail = self.thumbnails[node_id][0]
                        assert thumbnail
                        thumbnail.update_preview()

                    if len(self.materials_for_render) == 0:
                        break
                except:
                    print("Unexpected exception: " + traceback.format_exc())
                finally:
                    log_thumbnails("lock.release (render)")
                    self.lock.release()


        # Display information for any unexpected exceptions.
        except:
            print("Unexpected exception: " + traceback.format_exc())

        # Finalize.
        finally:
            log_thumbnails("render.done, exit thread", thumbnail)
            # Clear the render thread.
            self.thread = None
            # Release the thumbnail lock.

    def swap_queue(self):
        if len(self.materials_queue) == 0:
            return

        is_starting = self.thread is None

        if self.thread and self.thread.is_alive():
            return

        if is_starting:
            log_thumbnails('create thread...')
            self.thread = RenderThumbnailThread(self)

        log_thumbnails("lock.acquire...")
        self.lock.acquire()
        log_thumbnails("   locked...")

        # copy and update shaders for render
        for node_id, shader in list(self.materials_queue.items()):
            self.materials_for_render[node_id] = shader  # overwrite if exist

        self.materials_queue.clear()

        log_thumbnails("lock.release...")
        self.lock.release()

        if is_starting:
            log_thumbnails('start thread...')
            self.thread.start()

    def thumbnail_update(self, thumbnail, material_node):
        if not material_node:
            return
        if not thumbnail.material_node:
            thumbnail.material_node = material_node

        # Check that thumbnails are enabled.
        if not self.enabled:
            return

        assert thumbnail.material_node
        with rprblender.render.core_operations():
            shader = self.get_exported_material_node(thumbnail.material_node)
            node_id = self.get_node_id(thumbnail.material_node)
            self.materials_queue[node_id] = shader  # overwrite if exist

            ThumbnailUpdateCallerOperator.material_changed()

    def on_scene_update(self):
        if bpy.data.materials.is_updated:
            for mat in bpy.data.materials:
                tree = mat.node_tree
                if not tree:
                    continue
                if mat.is_updated or tree.is_updated or tree.is_updated_data:
                    # log_thumbnails('material changed: ', mat.name)
                    for node in mat.node_tree.nodes:
                        if not hasattr(node, 'has_thumbnail'):
                            continue
                        if not node.has_thumbnail:
                            continue

                        node_id = self.get_node_id(node)
                        if node_id in self.thumbnails:
                            thumbnail = self.thumbnails[node_id][0]
                            assert thumbnail
                            self.thumbnail_update(thumbnail, node)


# A NodeThumbnail renders a thumbnail image for a material node. Updating a thumbnail
# from a material node constructs an RPR material for the node and all preceding
# nodes in the tree. It applies this material to the thumbnail geometry and renders
# the scene, then copies the resulting image to the image preview. The preview
# is displayed in the UI using a template_icon_view.
class NodeThumbnail:
    def __init__(self, manager: NodeThumbnailManager):

        # Store a reference to the thumbnail manager.
        self.manager = manager

        # Initialize members.
        self.material_node = None

        self.restart_thread = False
        self.initialized = False
        self.update_pending = False

        # Initialize preview.
        self.previews = bpy.utils.previews.new()
        self.preview = None

        self.nodeid = ''

    def clean_up(self):
        bpy.utils.previews.remove(self.previews)
        self.material_node = None
        self.restart_thread = False

    def initialize(self, material_node):
        # Check if already initialized.
        if self.initialized:
            return

        # Perform an initial update.
        self.manager.thumbnail_update(self, material_node)
        self.initialized = True

    def set_material_node(self, material_node):
        if not material_node:
            return
        if not self.material_node:
            self.material_node = material_node

    def update_preview(self):
        # Update the image pixels from the frame buffer pixels.
        pixels = self.manager.get_frame_buffer_pixels()
        preview = self.get_preview()
        preview.image_pixels_float = pixels.flatten()

        # Tell the node to redraw itself.
        if self.material_node is not None:
            self.material_node.redraw()

    def get_preview(self):

        if self.preview is None:
            self.preview = self.previews.load("", "", "IMAGE", False)
            self.preview.image_size = [self.manager.size, self.manager.size]

        return self.preview


# A thread used to perform the thumbnail render.
class RenderThumbnailThread(threading.Thread):
    def __init__(self, manager):
        super().__init__()
        self.manager = manager

    def run(self):
        self.manager.render()


_thumbnail_manager = None


def get_thumbnail_manager():
    global _thumbnail_manager
    if not _thumbnail_manager:
        _thumbnail_manager = NodeThumbnailManager()
    return _thumbnail_manager


# Get a thumbnail for the given material node
def get_node_thumbnail(material_node):
    return get_thumbnail_manager().get_thumbnail(material_node)


@bpy.app.handlers.persistent
def on_scene_update_post(scene):
    try:
        if bpy.context.scene.rpr.thumbnails.enable:
            get_thumbnail_manager().on_scene_update()
    except:
        log_thumbnails("Unexpected exception (on_scene_update): " + traceback.format_exc())


bpy.app.handlers.scene_update_post.append(on_scene_update_post)


@rpraddon.register_class
class ThumbnailUpdateCallerOperator(bpy.types.Operator):
    bl_idname = "wm.rpr_thumbnail_update_caller_operator"
    bl_label = "Modal Timer Operator"

    limits = bpy.props.IntProperty(default=0)
    _timer = None
    _last_change = 0
    _terminate = False

    @staticmethod
    def material_changed():
        ThumbnailUpdateCallerOperator._last_change = time.perf_counter()

    def modal(self, context, event):
        if ThumbnailUpdateCallerOperator._terminate:
            self.cancel(context)
            return {'FINISHED'}

        if event.type == 'TIMER' and context.scene.render.engine == 'RPR':
            if time.perf_counter() - ThumbnailUpdateCallerOperator._last_change > 0.6:
                get_thumbnail_manager().swap_queue()

        return {'PASS_THROUGH'}

    def execute(self, context):
        if ThumbnailUpdateCallerOperator._timer:
            return {'FINISHED'}
        wm = context.window_manager
        ThumbnailUpdateCallerOperator._terminate = False

        logging.info('execute rpr_thumbnail_update_caller_operator...')
        ThumbnailUpdateCallerOperator._timer = wm.event_timer_add(time_step=1, window=context.window)
        wm.modal_handler_add(self)
        return {'RUNNING_MODAL'}

    def cancel(self, context):
        logging.info('Cancel rpr_thumbnail_update_caller_operator...')
        wm = context.window_manager
        wm.event_timer_remove(ThumbnailUpdateCallerOperator._timer)
        ThumbnailUpdateCallerOperator._timer = None
        return {'FINISHED'}


@rpraddon.register_class
class ThumbnailUpdateCallerDisableOperator(bpy.types.Operator):
    bl_idname = "wm.rpr_thumbnail_update_caller_disable_operator"
    bl_label = "Modal Timer Disable Operator"

    def execute(self, context):
        ThumbnailUpdateCallerOperator._terminate = True
        return {'FINISHED'}


@rpraddon.register_class
class RPRMaterial_PT_Thumbnails(RPRPanel, bpy.types.Panel):
    bl_label = "RPR Thumbnails"
    bl_context = "material"
    bl_options = {'DEFAULT_CLOSED'}
    bl_space_type = "NODE_EDITOR"
    bl_region_type = "UI"

    @classmethod
    def poll(cls, context):
        return context.active_object and context.active_object.active_material and RPRPanel.poll(context)\
               and context.space_data.tree_type == 'RPRTreeType'

    def draw_header(self, context):
        self.layout.prop(context.scene.rpr.thumbnails, "enable", text='')

    def draw(self, context):
        self.layout.label('Experimental (see console)', icon='ERROR')
        self.layout.prop(context.scene.rpr.thumbnails, "use_large_preview")


def register():
    pass


def unregister():
    logging.debug("node_thumbnail.unregister...")
    global _thumbnail_manager
    if _thumbnail_manager:
        _thumbnail_manager.clean_up_thumbnails()
    _thumbnail_manager = None
