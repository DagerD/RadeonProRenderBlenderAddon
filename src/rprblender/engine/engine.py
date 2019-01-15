''' Engine is the functionality of the rendering process, 
maintains context, processes, etc.

Other modules in this directory could be viewport, etc.
'''

''' main Render object '''

import weakref
import numpy as np
from abc import ABCMeta, abstractmethod

import bpy

from .context import RPRContext
from rprblender.properties.view_layer import RPR_ViewLayerProperites

from rprblender.utils import logging
log = logging.Log(tag='Engine')


class Engine(metaclass=ABCMeta):
    def __init__(self, rpr_engine):
        self.rpr_engine = weakref.proxy(rpr_engine)
        self.rpr_context = RPRContext()

    @abstractmethod
    def render(self):
        pass

    @abstractmethod
    def sync(self, depsgraph):
        ''' sync all data '''
        pass

    def set_render_result(self, render_passes: bpy.types.RenderPasses):
        def zeros_image(channels):
            return np.zeros((self.rpr_context.height, self.rpr_context.width, channels), dtype=np.float32)

        images = []

        for p in render_passes:
            try:
                aov = next(aov for aov in RPR_ViewLayerProperites.aovs_info if aov['name'] == p.name)  # finding corresponded aov
                image = self.rpr_context.get_image(aov['rpr'])

            except StopIteration:
                log.warn("AOV '{}' is not found in aovs_info".format(p.name))
                image = zeros_image(p.channels)

            except KeyError:
                # This could happen when Depth or Combined was not selected, but they still are in view_layer.use_pass_*
                log.warn("AOV '{}' is not enabled in rpr_context".format(aov['name']))
                image = zeros_image(p.channels)

            if p.channels != image.shape[2]:
                image = image[:, :, 0:p.channels]

            images.append(image.flatten())

        # efficient way to copy all AOV images
        render_passes.foreach_set('rect', np.concatenate(images))
