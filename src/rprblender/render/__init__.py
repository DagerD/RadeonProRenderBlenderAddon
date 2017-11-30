import os
import sys
import platform
import threading
from pathlib import Path
import contextlib
import traceback

import gc

import rprblender
from rprblender import config, logging

import bpy

_lock = threading.Lock()


@contextlib.contextmanager
def core_operations(raise_error=False):
    """ Contextmanager for all render calls, besides locking catches all exceptions
    and forwards them to log file"""
    try:
        with _lock:
            yield
    except:
        logging.critical(traceback.format_exc(), tag='render')
        if raise_error:
            raise


def get_package_root_dir():
    return Path(rprblender.__file__).parent


def log_pyrpr(*argv):
    logging.info(*argv, tag='render.pyrpr')


def pyrpr_init(bindings_import_path, rprsdk_bin_path):
    if bindings_import_path not in sys.path:
        sys.path.append(bindings_import_path)
    try:
        import pyrpr
        import pyrprapi  # import this to be have it in the sys.modules available later

        pyrpr.lib_wrapped_log_calls = config.pyrpr_log_calls
        pyrpr.init(log_pyrpr, rprsdk_bin_path=rprsdk_bin_path)

        import pyrpr_load_store
        pyrpr_load_store.init(rprsdk_bin_path)

        import pyrprx
        pyrprx.lib_wrapped_log_calls = config.pyrprx_log_calls
        pyrprx.init(log_pyrpr, rprsdk_bin_path=rprsdk_bin_path)

        import pyrprimagefilters
        pyrprimagefilters.lib_wrapped_log_calls = config.pyrprimagefilters_log_calls
        pyrprimagefilters.init(log_pyrpr, rprsdk_bin_path=rprsdk_bin_path)

        import pyrpropencl
        pyrpropencl.init()

    except:
        logging.critical(traceback.format_exc(), tag='')
        return False
    finally:
        sys.path.remove(bindings_import_path)
    return True


if 'pyrpr' not in sys.modules:

    # TODO: nasty dependency on cffi_backend module that is implicitly imported from our ffi
    # solution - use something without this dependency, ctypes, cython, SWIG

    # try loading pyrpr for installed addon
    bindings_import_path = str(get_package_root_dir())
    rprsdk_bin_path = get_package_root_dir()
    if not pyrpr_init(bindings_import_path, rprsdk_bin_path):
        logging.critical('failed to load rpr from ', bindings_import_path)
        for line in traceback.format_stack():
            logging.critical(line)

        # try loading pyrpr from source
        src = get_package_root_dir().parent
        project_root = src.parent
        rprsdk_path = str(project_root / 'ThirdParty/RadeonProRender SDK')

        if "Windows" == platform.system():
            bin_folder = 'Win/bin'
        elif "Linux" == platform.system():
            bin_folder = 'Linux-Ubuntu/lib'
        else:
            assert False

        rprsdk_bin_path = Path(rprsdk_path) / bin_folder

        bindings_import_path = str(src / 'bindings/pyrpr/.build')
        pyrpr_import_path = str(src / 'bindings/pyrpr/src')

        if bindings_import_path not in sys.path:
            sys.path.append(pyrpr_import_path)

        try:
            assert pyrpr_init(bindings_import_path, rprsdk_bin_path)
        finally:
            sys.path.remove(pyrpr_import_path)

    logging.debug('rprsdk_bin_path:', rprsdk_bin_path)

import pyrpr

log_pyrpr("Radeon ProRender ", hex(pyrpr.API_VERSION))


def ensure_core_cache_folder():
    # TODO: set cache path to a user/temp folder?

    path = str(get_package_root_dir() / '.core_cache' / hex(pyrpr.API_VERSION))

    if not os.path.isdir(path):
        os.makedirs(path)
    return path


def ensure_core_trace_folder():
    if bpy.context.scene.rpr.dev.trace_dump_folder == '':
        path = str(get_package_root_dir() / '.core_trace')
    else:
        path = bpy.path.native_pathsep(bpy.path.abspath(bpy.context.scene.rpr.dev.trace_dump_folder))

    if not os.path.isdir(path):
        os.makedirs(path)
    return path


from rprblender import helpers


def get_context_creation_flags(is_production):
    settings = helpers.get_user_settings()
    if settings.device_type == 'cpu':
        flags = pyrpr.CREATION_FLAGS_ENABLE_CPU
        logging.info('Using CPU only')
    else:
        flags = helpers.render_resources_helper.get_used_devices_flags()
        assert flags != 0
        if (settings.device_type_plus_cpu) and (is_production):
            flags |= pyrpr.CREATION_FLAGS_ENABLE_CPU
            logging.info('Using GPU+CPU')
    return flags


def create_context(cache_path, flags) -> pyrpr.Context:
    # init trace dump settings
    from rprblender import properties
    properties.init_trace_dump(bpy.context.scene.rpr.dev)

    tahoe_path = get_core_render_plugin_path()

    logging.debug('tahoe_path', repr(tahoe_path))
    tahoe_plugin_i_d = pyrpr.RegisterPlugin(tahoe_path.encode('utf8'))

    assert -1 != tahoe_plugin_i_d


    return pyrpr.Context([tahoe_plugin_i_d], flags, cache_path=str(cache_path))


def get_core_render_plugin_path():
    if 'Windows' == platform.system():
        lib_name = 'Tahoe64.dll'
    elif 'Linux' == platform.system():
        lib_name = 'libTahoe64.so'
    else:
        assert False, platform.system()
    tahoe_path = str(rprsdk_bin_path / lib_name)
    return tahoe_path


support_path = str(get_package_root_dir() / 'support')
if support_path not in sys.path:
    sys.path.append(support_path)


render_devices = {}


def get_render_device(is_production=True, persistent=False):
    import rprblender.render.device

    flags = rprblender.render.get_context_creation_flags(is_production)

    logging.debug("get_render_device(is_production=%s), flags: %s" %(is_production, hex(flags)), tag='render.device')

    if persistent:
        key = (is_production, flags)

        if key in render_devices:
            return render_devices[key]
        render_devices.clear()  # don't keep more than one device(not to multiply memory usage for image cache)

    logging.debug("create new device, not found in cache:", render_devices, tag='render.device')
    device = rprblender.render.device.RenderDevice(is_production=is_production, context_flags=flags)
    if persistent:
        render_devices[key] = device
    return device


def register():
    logging.debug('rpr.render.register')


def unregister():
    logging.debug('rpr.render.unregister')


def free_render_devices():
    logging.debug("free_render_devices", tag='render.device')
    import rprblender.render.device
    while render_devices:
        render_device = render_devices.popitem()[1]  # type: rprblender.render.device.RenderDevice

        if config.debug:
            referrers = gc.get_referrers(render_device)
            logging.critical("render_device has more than one reference(current frame and something else):")
            for r in referrers:
                if r != sys._getframe(0):
                    logging.critical(r)
                    try:
                        logging.critical(r.f_code)
                        while r.f_back is not None:
                            r = r.f_back
                            logging.critical(r.f_code)

                    except AttributeError:pass
            del referrers
        del render_device
