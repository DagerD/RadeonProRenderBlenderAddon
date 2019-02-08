import threading
import time

from rprblender import config
from rprblender import utils
from .engine import Engine
from rprblender.properties import SyncError

from rprblender.utils import logging
log = logging.Log(tag='RenderEngine')


class RenderEngine(Engine):
    def __init__(self, rpr_engine):
        super().__init__(rpr_engine)

        self.render_lock = threading.Lock()
        self.is_synced = False
        self.render_event = threading.Event()
        self.finish_render = False

        self.iterations = 0
        self.iteration_samples = 1

        self.status_title = ""

    def notify_status(self, progress, info):
        self.rpr_engine.update_progress(progress)
        self.rpr_engine.update_stats(self.status_title, info)

        if config.notifier_log_calls:
            log("%d - %s" % (int(progress*100), info))

    def _do_update_result(self, result):
        while not self.finish_render:
            self.render_event.wait()
            self.render_event.clear()

            with self.render_lock:
                self.rpr_context.resolve()

            log("Updating render result")
            self.rpr_context.resolve_extras()
            self.set_render_result(result.layers[0].passes)
            self.rpr_engine.update_result(result)

            time.sleep(config.render_update_result_interval)

    def _do_render(self, iterations, samples):
        self.finish_render = False
        try:
            self.rpr_context.set_parameter('iterations', samples)

            for it in range(iterations):
                if self.rpr_engine.test_break():
                    break

                self.notify_status(it / iterations, "Iteration: %d/%d" % (it + 1, iterations))

                with self.render_lock:
                    self.rpr_context.render()

                self.render_event.set()
        finally:
            self.finish_render = True

    def _do_render_tile(self, n, m, samples):
        # TODO: This is a prototype of tile render
        #  currently it produces core error, needs to be checked

        self.finish_render = False
        try:
            self.rpr_context.set_parameter('iterations', samples)

            for i, tile in enumerate(utils.get_tiles(self.rpr_context.width, self.rpr_context.height, n, m)):
                if self.rpr_engine.test_break():
                    break

                self.notify_status(i / (n * m), "Tile: %d/%d" % (i, n * m))

                with self.render_lock:
                    self.rpr_context.render(tile)

                self.render_event.set()
        finally:
            self.finish_render = True

    def render(self):
        if not self.is_synced:
            return

        log("Start render")

        self.notify_status(0, "Start render")

        result = self.rpr_engine.begin_result(0, 0, self.rpr_context.width, self.rpr_context.height)
        self.rpr_context.clear_frame_buffers()
        self.rpr_context.sync_auto_adapt_subdivision()
        self.render_event.clear()

        update_result_thread = threading.Thread(target=RenderEngine._do_update_result, args=(self, result))
        update_result_thread.start()

        self._do_render(self.iterations, self.iteration_samples)
        # self._do_render_tile(20, 20)

        update_result_thread.join()

        if self.render_event.is_set():
            log('Getting final render result')
            self.rpr_context.resolve()
            self.rpr_context.resolve_extras()
            self.set_render_result(result.layers[0].passes)

        self.rpr_engine.end_result(result)
        self.notify_status(1, "Finish render")
        log('Finish render')

    @staticmethod
    def is_object_allowed_for_motion_blur(obj):
        """Check if object could have motion blur effect: meshes, area lights and cameras can"""
        if not obj.rpr.motion_blur:
            return False
        # TODO allow cameras
        if obj.type not in ['MESH', 'LIGHT', 'CAMERA']:
            return False
        if obj.type == 'LIGHT' and obj.data.type != 'AREA':
            return False
        return True

    def collect_motion_blur_info(self, scene):
        if not scene.rpr.motion_blur:
            return {}

        motion_blur_info = {}

        prev_frame_matrices = {}
        next_frame_matrices = {}
        scales = {}

        current_frame = scene.frame_current
        # TODO check for corner case of first animation frame; Should I ask Brian?
        previous_frame = current_frame - scene.frame_step

        # collect previous frame matrices
        scene.frame_set(previous_frame)
        for obj in scene.objects:
            if not self.is_object_allowed_for_motion_blur(obj):
                continue

            key = utils.key(obj)
            prev_frame_matrices[key] = obj.matrix_world.copy()

        # restore current frame and collect matrices
        scene.frame_set(current_frame)
        for obj in scene.objects:
            if not self.is_object_allowed_for_motion_blur(obj):
                continue

            key = utils.key(obj)
            next_frame_matrices[key] = obj.matrix_world.copy()
            scales[key] = float(obj.rpr.motion_blur_scale)

        for key, prev in prev_frame_matrices.items():
            this = next_frame_matrices.get(key, None)

            # User can animate the object's "motion_blur" flag.
            # Ignore such objects at ON-OFF/OFF-ON frames. Calculate difference for anything else
            if not this:
                continue

            # calculate velocities
            info = utils.MotionBlurInfo(prev, this, scales[key])

            motion_blur_info[key] = info

        return motion_blur_info

    def sync(self, depsgraph):
        log('Start syncing')
        self.is_synced = False

        scene = depsgraph.scene
        view_layer = depsgraph.view_layer
        self.status_title = "%s: %s" % (scene.name, view_layer.name)

        self.notify_status(0, "Start syncing")

        scene.rpr.sync(self.rpr_context)
        self.rpr_context.resize(
            int(scene.render.resolution_x * scene.render.resolution_percentage / 100),
            int(scene.render.resolution_y * scene.render.resolution_percentage / 100)
        )

        frame_motion_blur_info = self.collect_motion_blur_info(scene)
        scene.world.rpr.sync(self.rpr_context)

        # getting visible objects
        for i, obj_instance in enumerate(depsgraph.object_instances):
            obj = obj_instance.object
            self.notify_status(0, "Syncing (%d/%d): %s" % (i, len(depsgraph.object_instances), obj.name))
            obj_motion_blur_info = None
            if obj.type != 'CAMERA':
                obj_motion_blur_info = frame_motion_blur_info.get(utils.key(obj), None)
            try:
                obj.rpr.sync(self.rpr_context, obj_instance, motion_blur_info=obj_motion_blur_info)
            except SyncError as e:
                log.warn("Skipping to add mesh", e)   # TODO: Error to UI log

            if self.rpr_engine.test_break():
                log.warn("Syncing stopped by user termination")
                return

        self.rpr_context.scene.set_name(scene.name)
        camera_key = utils.key(scene.camera)
        rpr_camera = self.rpr_context.objects[camera_key]
        if scene.camera.rpr.motion_blur:
            rpr_camera.set_exposure(scene.camera.rpr.motion_blur_exposure)

            if camera_key in frame_motion_blur_info:
                camera_motion_blur = frame_motion_blur_info[camera_key]
                rpr_camera.set_angular_motion(*camera_motion_blur.angular_momentum)
                rpr_camera.set_linear_motion(*camera_motion_blur.linear_velocity)

        self.rpr_context.scene.set_camera(rpr_camera)

        self.rpr_context.sync_shadow_catcher()

        view_layer.rpr.sync(view_layer, self.rpr_context, self.rpr_engine)

        self.rpr_context.set_parameter('preview', False)

        self.iterations = scene.rpr.limits.iterations
        self.iteration_samples = scene.rpr.limits.iteration_samples

        self.is_synced = True
        self.notify_status(0, "Finish syncing")
        log('Finish sync')
