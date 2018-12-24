import bpy
from nodeitems_utils import (
    NodeCategory,
    NodeItem,
    register_node_categories,
    unregister_node_categories,
)
from nodeitems_builtins import (
    ShaderNodeCategory,
)

from rprblender.utils import is_rpr_active
from rprblender.utils import logging

from .sockets import classes
from .uber_node import RPR_Node_Uber
from .output_node import RPR_Node_Output
from .rpr_nodes import RPRShadingNode


log = logging.Log(tag='nodes')


class RPR_ShaderNodeCategory(NodeCategory):
    @classmethod
    def poll(cls, context):
        return context.scene.render.engine == "RPR"\
               and context.space_data.tree_type in ('ShaderNodeTree', 'RPRTreeType')


node_categories = [
    RPR_ShaderNodeCategory('rpr_shader_output', 'Output', items=[
        NodeItem('ShaderNodeOutputMaterial'),
    ],),
    RPR_ShaderNodeCategory('rpr_shader_blender_nodes', 'Shader', items=[
        NodeItem('ShaderNodeBsdfPrincipled'),
    ]),
    RPR_ShaderNodeCategory('RPR_SHADER', "RPR Shader", items=[
        NodeItem('rpr_shader_node_uber'),
    ])
]


def hide_cycles_and_eevee_poll(method):
    @classmethod
    def func(cls, context):
        return not is_rpr_active(context) and method(context)
    return func


old_shader_node_category_poll = None


classes += (RPRShadingNode, RPR_Node_Output, RPR_Node_Uber)
register_classes, unregister_classes = bpy.utils.register_classes_factory(classes)


def register():
    # rpr_nodes.generate_types()

    # some nodes are hidden from plugins by Cycles itself(like Material Output), some we could not support.
    # thus we'll hide 'em all to show only selected set of supported Blender nodes
    global old_shader_node_category_poll
    old_shader_node_category_poll = ShaderNodeCategory.poll
    ShaderNodeCategory.poll = hide_cycles_and_eevee_poll(ShaderNodeCategory.poll)

    register_classes()
    register_node_categories("RPR_NODES", node_categories)


def unregister():
    if old_shader_node_category_poll and ShaderNodeCategory.poll is not old_shader_node_category_poll:
        ShaderNodeCategory.poll = old_shader_node_category_poll
    unregister_node_categories("RPR_NODES")
    unregister_classes()
