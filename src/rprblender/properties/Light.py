import bpy
from bpy.props import (
    PointerProperty,
)

from . import RPR_Properties, RPR_Panel
from rprblender import logging
from rprblender.utils import get_transform

class RPR_LightProperties(RPR_Properties):
    def sync(self, rpr_context, obj):
        ''' sync the mesh '''
        light = self.id_data
        print("Syncing light: %s" % light.name)

        rpr_light = rpr_context.create_light(obj.name, 'point')
        rpr_light.set_name(light.name)
        rpr_light.set_radiant_power(10.0, 10.0, 10.0)
        rpr_light.set_transform(get_transform(obj))
        rpr_context.scene.attach(rpr_light)

    @classmethod
    def register(cls):
        logging.info("register", tag='Light')
        bpy.types.Light.rpr = PointerProperty(
            name="RPR Light Settings",
            description="RPR light settings",
            type=cls,
        )

    @classmethod
    def unregister(cls):
        logging.info("unregister", tag='Light')
        del bpy.types.Light.rpr


class RPR_LIGHT_PT_light(RPR_Panel):
    """
    Physical light sources
    """
    bl_idname = 'rpr_data_PT_light'
    bl_label = "RPR Settings"
    bl_context = 'data'

    @classmethod
    def poll(cls, context):
        return context.light and RPR_Panel.poll(context)

    def draw(self, context):
        layout = self.layout

        scene = context.scene
        light = context.light

        layout.prop(light, "type", expand=True)


classes_to_register = (RPR_LightProperties, RPR_LIGHT_PT_light)
