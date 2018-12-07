import bpy
from bpy.props import (
    PointerProperty,
)

from .base import RPR_Property, RPR_Panel
from rprblender import logging


class RPR_PROPS_PhysicalLightSettings(RPR_Property):

    def sync(self, context, transform):
        ''' sync the mesh '''
        light = self.id_data
        print("Syncing light: %s" % light.name)

        rpr_light = context.create_point_light()
        rpr_light.set_name(light.name)
        rpr_light.set_radiant_power(10.0, 10.0, 1.0)
        rpr_light.set_transform(transform)

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

class RPR_DATA_PT_light(RPR_Panel):
    """
    Physical light sources
    """
    bl_idname = 'rpr_data_PT_light'
    bl_label = "Light Settings"
    bl_context = 'data'

    @classmethod
    def poll(cls, context):
        return context.light and RPR_Panel.poll(context)

    def draw(self, context):
        layout = self.layout

        scene = context.scene
        light = context.light

        layout.prop(light, "type", expand=True)


classes = (RPR_PROPS_PhysicalLightSettings, RPR_DATA_PT_light)
