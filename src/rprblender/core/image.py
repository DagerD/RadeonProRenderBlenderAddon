import bpy
import numpy as np

import pyrpr
from pathlib import Path

import rprblender.images
from rprblender import config, logging
from rprblender.helpers import CallLogger

logged = CallLogger(tag="core.image").logged

@logged
def get_core_image_for_blender_image(context, blender_image):
    if config.image_cache_core:
        return get_cached_core_image_for_blender_image(context, blender_image)
    else:
        if rprblender.images.downscaled_image_size[context]:
            return create_core_downscaled_image_from_blender_image(context, blender_image,
                        rprblender.images.downscaled_image_size[context])

        return create_core_image_from_blender_image(context, blender_image, None)


@logged
def get_cached_core_image_for_blender_image(context, blender_image):
    if rprblender.images.downscaled_image_size[context]:
        return rprblender.images.core_downscaled_image_cache.get_core_image(
            context, blender_image, rprblender.images.downscaled_image_size[context],
            create_core_downscaled_image_from_blender_image)

    return rprblender.images.core_image_cache.get_core_image(
        context, blender_image, None, create_core_image_from_blender_image)


@logged
def create_core_downscaled_image_from_blender_image(context, blender_image, image_size):
    if blender_image.size[0] < image_size and blender_image.size[1] < image_size:
        # this is small image we will not downscale it
        return create_core_image_from_blender_image(context, blender_image, image_size)

    new_size = (min(blender_image.size[0], image_size), 
                min(blender_image.size[1], image_size))

    logging.debug("create_core_downscaled_image_from_blender_image: %s, path: %s, lib: %s, size=(%d, %d), new_size=(%d, %d)" %(
                  blender_image.name,
                  blender_image.filepath,
                  blender_image.library.filepath if blender_image.library else "<none>",
                  blender_image.size[0], blender_image.size[1],
                  new_size[0], new_size[1]),
                  tag="core.image")

    # scaling existing blender image and leave it in this state
    blender_image.scale(new_size[0], new_size[1])

    return create_core_image_from_pixels(context, get_pixels_for_blender_image(context, blender_image))


@logged
def create_core_image_from_blender_image(context, blender_image, image_size):
    logging.debug("create_core_image_from_blender_image: %s, path: %s, lib: %s, cur_size=(%d, %d)" %(
                  blender_image.name,
                  blender_image.filepath,
                  blender_image.library.filepath if blender_image.library else "<none>",
                  blender_image.size[0], blender_image.size[1]),
                  tag="core.image")
    fpath = Path(bpy.path.abspath(blender_image.filepath, library=blender_image.library))
    logging.debug("full path:", fpath, tag="core.image")
    use_core_to_load = config.rpr_image_loading  and fpath.is_file() and not fpath.suffix.lower() == '.exr'

    logging.debug("using ", "RPR" if use_core_to_load else "!Blender!", "to load", tag="core.image")

    if use_core_to_load:
        # don't use bpy.images.load for loading images during RenderEngine.render call -
        # it crashes since 2.79, probably decause of depsgraph modification in a thread other than main(render's)
        return create_core_image_from_image_file(context, str(fpath))
    else:
        if image_size:
            # reloading blender image because it probably was downscaled before. If not
            # then probably it was not loaded fully (pixels were not readed) 
            # therefore reload will be a cheap operation
            blender_image.reload()
            logging.debug("create_core_image_from_blender_image: after reload size=(%d, %d)" % 
                          (blender_image.size[0], blender_image.size[1]))

        core_image = create_core_image_from_pixels(context, get_pixels_for_blender_image(context, blender_image))

        if image_size:
            # scaling image back to downscaled size because we don't want to store it in Blender's memory
            new_size = (min(blender_image.size[0], image_size), 
                        min(blender_image.size[1], image_size))
            blender_image.scale(new_size[0], new_size[1])
            logging.debug("create_core_image_from_blender_image: after srinking back new_size=(%d, %d)" % 
                          (blender_image.size[0], blender_image.size[1]))

        return core_image


@logged
def get_pixels_for_blender_image(context, blender_image):
    if config.image_cache_blender:
        pixels = get_cached_pixels_for_blender_image(context, blender_image)
    else:
        pixels = extract_pixels_from_blender_image(blender_image)
    return pixels


@logged
def get_cached_pixels_for_blender_image(context, blender_image):
    if rprblender.images.downscaled_image_size[context]:
        return rprblender.images.downscaled_image_cache.get_image_pixels(
            blender_image, rprblender.images.downscaled_image_size[context],
            extract_pixels_from_blender_downscaled_image)

    if config.image_cache_core:
        # blender cache is not needed there
        return extract_pixels_from_blender_image(blender_image)

    return rprblender.images.image_cache.get_image_pixels(
        blender_image, extract_pixels_from_blender_image)


@logged
def create_core_image_from_pixels(context, pixels):
    logging.debug("create_core_image_from_pixels:", pixels.shape, tag="core.image")

    desc = pyrpr.ffi.new("rpr_image_desc*")
    desc.image_width = pixels.shape[1]
    desc.image_height = pixels.shape[0]
    desc.image_depth = 0
    desc.image_row_pitch = desc.image_width * pyrpr.ffi.sizeof('rpr_float') * 4
    desc.image_slice_pitch = 0
    handle = pyrpr.Image()
    pyrpr.ContextCreateImage(context,
                             (4, pyrpr.COMPONENT_TYPE_FLOAT32), desc,
                             pyrpr.ffi.cast("float *", pixels.ctypes.data), handle)
    return handle


@logged
def create_core_image_from_image_file(context, filename):
    logging.debug("create_core_image_from_image_file:", filename, tag="core.image")
    if config.image_dont_load_use_small:
        return create_core_image_from_pixels(context, get_tiny_image())

    handle = pyrpr.Image()
    pyrpr.ContextCreateImageFromFile(context, str(filename).encode('utf8'), handle)
    return handle


def get_tiny_image():
    logging.debug("get_tiny_image", tag="core.image")
    return np.full((2, 2, 4),
                   np.array((np.random.rand(), np.random.rand(), np.random.rand(), 1)), dtype=np.float32)


@logged
def create_core_image_from_image_file_via_blender(context, filename, flipud):
    if config.image_dont_load_use_small:
        return create_core_image_from_pixels(context, get_tiny_image())

    image = None
    try:
        image = bpy.data.images.load(filename)
        pixels = extract_pixels_from_blender_image(image, flipud=flipud)
    except Exception as e:
        logging.error("Can't load image: ", repr(filename), ", reason:", e, tag="core.image")
        raise
    finally:
        if image:
            bpy.data.images.remove(image)
    return create_core_image_from_pixels(context, pixels)

@logged
def extract_pixels_from_blender_downscaled_image(image, image_size, flipud=True):
    if image.size[0] < image_size and image.size[1] < image_size:
        # this is small image we will not downscale it
        return extract_pixels_from_blender_image(image, flipud)

    if config.image_dont_load_use_small:
        return get_tiny_image()

    new_size = (min(image.size[0], image_size), 
                min(image.size[1], image_size))
    logging.debug("extract_pixels_from_blender_downscaled_image: %s, size=(%d, %d), new_size=(%d, %d)" %
                  (image.name, image.size[0], image.size[1], new_size[0], new_size[1]), 
                  tag="core.image")

    # scaling existing blender image and leave it in this state
    image.scale(new_size[0], new_size[1])

    raw = np.fromiter(image.pixels, dtype=np.float32, count=image.size[0]*image.size[1]*image.channels)

    if 4 != image.channels:
        raise Exception("Image: %s has %s channels" % (image.name, image.channels))

    pixels = raw.reshape(image.size[1], image.size[0], 4)
    if flipud:
        pixels = np.flipud(pixels)

    return np.ascontiguousarray(pixels)


@logged
def extract_pixels_from_blender_image(image, flipud=True):
    if config.image_dont_load_use_small:
        return get_tiny_image()

    logging.debug("extract_pixels_from_blender_image: %s, size=(%d, %d)", 
                  (image.name, image.size[0], image.size[1]), 
                  tag="core.image")
    raw = np.fromiter(image.pixels, dtype=np.float32, count=image.size[0]*image.size[1]*image.channels)

    if 4 != image.channels:
        raise Exception("Image: %s has %s channels" % (image.name, image.channels))

    pixels = raw.reshape(image.size[1], image.size[0], 4)
    if flipud:
        pixels = np.flipud(pixels)

    return np.ascontiguousarray(pixels)


