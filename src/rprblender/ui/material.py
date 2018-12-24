from bpy_extras.node_utils import find_node_input

from . import RPR_Panel
from rprblender.utils import material as mat_utils


class RPR_MATERIAL_PT_context(RPR_Panel):
    bl_label = ""
    bl_context = "material"
    bl_options = {'HIDE_HEADER'}

    @classmethod
    def poll(cls, context):
        if context.active_object and context.active_object.type == 'GPENCIL':
            return False
        else:
            return (context.material or context.object) and RPR_Panel.poll(context)

    def draw(self, context):
        layout = self.layout

        material = context.material
        object = context.object
        slot = context.material_slot
        space = context.space_data

        if object:
            is_sortable = len(object.material_slots) > 1
            rows = 1
            if is_sortable:
                rows = 4

            row = layout.row()

            row.template_list("MATERIAL_UL_matslots", "", object, "material_slots", object, "active_material_index", rows=rows)

            col = row.column(align=True)
            col.operator("object.material_slot_add", icon='ADD', text="")
            col.operator("object.material_slot_remove", icon='REMOVE', text="")

            col.menu("MATERIAL_MT_specials", icon='DOWNARROW_HLT', text="")

            if is_sortable:
                col.separator()

                col.operator("object.material_slot_move", icon='TRIA_UP', text="").direction = 'UP'
                col.operator("object.material_slot_move", icon='TRIA_DOWN', text="").direction = 'DOWN'

            if object.mode == 'EDIT':
                row = layout.row(align=True)
                row.operator("object.material_slot_assign", text="Assign")
                row.operator("object.material_slot_select", text="Select")
                row.operator("object.material_slot_deselect", text="Deselect")

        split = layout.split(factor=0.65)

        if object:
            split.template_ID(object, "active_material", new="material.new")
            row = split.row()

            if slot:
                row.prop(slot, "link", text="")
            else:
                row.label()
        elif material:
            split.template_ID(space, "pin_id")
            split.separator()


class RPR_MATERIAL_PT_preview(RPR_Panel):
    bl_label = "Preview"
    bl_context = "material"
    bl_options = {'DEFAULT_CLOSED'}

    @classmethod
    def poll(cls, context):
        return context.material and RPR_Panel.poll(context)

    def draw(self, context):
        self.layout.template_preview(context.material)


class RPR_MATERIAL_PT_surface(RPR_Panel):
    bl_label = "Surface"
    bl_context = "material"

    @classmethod
    def poll(cls, context):
        return context.material and RPR_Panel.poll(context)

    def draw(self, context):
        layout = self.layout

        material = context.material
        if not self.panel_node_draw(material, 'OUTPUT_MATERIAL', 'Surface'):
            layout.prop(material, "diffuse_color")

    def panel_node_draw(self, id_data, output_type, input_name):
        node_tree = id_data.node_tree

    #    node = node_tree.get_output_node('OUTPUT')
        node = mat_utils.find_output_node_in_tree(node_tree)
        if node:
            input = find_node_input(node, input_name)
            if input:
                self.layout.template_node_view(node_tree, node, input)
            else:
                self.layout.label(text="Incompatible output node")
        else:
            self.layout.label(text="No output node")

        return True

