from .base import RPR_Property, RPR_Panel
import bpy
from bpy.props import (
    BoolProperty,
    EnumProperty,
    FloatProperty,
    IntProperty,
    PointerProperty,
    StringProperty,
)
import pyrpr

from rprblender import logging


class RPR_ObjectProperties(RPR_Property):
    """
    Properties for objects
    """

    camera_visible: BoolProperty(name='Camera Visibility', default=True)

    shadowcatcher: BoolProperty(
        name="Shadow Catcher",
        description="Use this object as shadowcatcher",
        default=False,
    )

    def sync(self, parent: bpy.types.Object, context: pyrpr.Context):
        ''' sync the object and any data attached '''
        obj = self.id_data
        print("Syncing Object %s " % obj.name)

        if self.camera_visible and hasattr(obj.data, 'rpr'):
            obj.data.rpr.sync(context)

    @classmethod
    def register(cls):
        logging.info("register", tag='Object')
        bpy.types.Object.rpr = PointerProperty(
            name="RPR Object Settings",
            description="RPR Object settings",
            type=cls,
        )

    @classmethod
    def unregister(cls):
        logging.info("unregister", tag='Object')
        del bpy.types.Object.rpr


class RPR_OBJECT_PT_object(RPR_Panel):
    """
    panel to display above properties
    """

    bl_idname = "rpr_object_PT_object"
    bl_label = "RPR Settings"
    bl_context = 'object'

    @classmethod
    def poll(cls, context):
        return context.object and super().poll(context)

    def draw(self, context):
        if context.object:
            rpr = getattr(context.object, 'rpr', None)
            self.layout.row().label(text="Just the test label")
            if rpr:
                self.layout.row().prop(rpr, 'camera_visible')
                self.layout.row().prop(rpr, 'shadowcatcher')


classes = (RPR_ObjectProperties, RPR_OBJECT_PT_object)
