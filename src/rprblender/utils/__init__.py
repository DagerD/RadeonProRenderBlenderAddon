import numpy as np
from pathlib import Path
import multiprocessing

import bpy
import rprblender


def is_rpr_active(context: bpy.types.Context):
    return context.scene.render.engine == 'RPR'


def get_transform(obj):
    if isinstance(obj, bpy.types.DepsgraphObjectInstance):
        if obj.is_instance:
            return np.array(obj.matrix_world, dtype=np.float32).reshape(4, 4)
        return np.array(obj.object.matrix_world, dtype=np.float32).reshape(4, 4)

    if isinstance(obj, bpy.types.Object):
        return np.array(obj.matrix_world, dtype=np.float32).reshape(4, 4)

    raise TypeError("Cannot get transform for object", obj)


def key(obj):
    if isinstance(obj, bpy.types.Object):
        return obj.name
    if isinstance(obj, bpy.types.Mesh):
        return obj.name
    if isinstance(obj, bpy.types.Material):
        return obj.name
    if isinstance(obj, bpy.types.Node):
        return obj.name
    if isinstance(obj, bpy.types.Image):
        return obj.name
    if isinstance(obj, bpy.types.DepsgraphObjectInstance):
        obj_key = key(obj.object)
        if not obj.is_instance:
            return obj_key
        return obj_key + '-' + str(obj.random_id)

    raise TypeError("Cannot create key for object", obj)


def package_root_dir():
    return Path(rprblender.__file__).parent


def get_cpu_threads_number():
    return multiprocessing.cpu_count()


def get_tiles(width, height, n, m):
    for i in range(n):
        for j in range(m):
            yield (width * i // n, width * (i + 1) // n - 1,
                   height * j // n, height * (i + 1) // n - 1)


def get_rpr_image(rpr_context, image: bpy.types.Image):
    image_key = key(image)
    if image_key in rpr_context.images:
        return rpr_context.images[image_key]

    filepath = image.filepath_from_user()
    if filepath:
        return rpr_context.create_image_file(image_key, filepath)

    if not image.has_data:
        raise ValueError("Image has no data", image)

    if image.channels != 4:
        raise ValueError("Image has %s channels instead of 4" % image.channels, image)

    data = np.fromiter(image.pixels, dtype=np.float32, count=image.size[0] * image.size[1] * image.channels)
    return rpr_context.create_image_data(image_key, data.reshape(image.size[1], image.size[0], 4))
