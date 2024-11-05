bl_info = {
    "name": "UV ShapeKeys",
    "author": "Rév",
    "version": (1, 0),
    "blender": (4, 0, 0),
    "location": "Properties > Object Data > UV Shape Keys",
    "description": "Creates shapekeys based on UV coordinates",
    "category": "Object",
}

import bpy
import bmesh
from bpy.props import (FloatProperty, 
                      PointerProperty,
                      CollectionProperty,
                      StringProperty,
                      IntProperty,
                      BoolProperty)
from bpy.types import (PropertyGroup,
                      Operator,
                      Panel)
import numpy as np
from mathutils import Vector

def mesh_update_callback(self, context):
    bpy.ops.object.update_uv_shape_keys()
    return None

def target_mesh_update_callback(self, context):
    global monitored_objects
    # Register new mesh for monitoring
    if self.mesh:
        monitored_objects[self.name] = self.mesh
        # Force an update
        bpy.ops.object.update_uv_shape_keys()
    return None

class UVShapeKeyTarget(PropertyGroup):
    """Target mesh for UV shape key"""
    name: StringProperty(name="Name", default="Target")
    mesh: PointerProperty(
        name="Target Mesh",
        type=bpy.types.Object,
        poll=lambda self, obj: obj.type == 'MESH',
        update=target_mesh_update_callback
    )
    value: FloatProperty(
        name="Value",
        min=-1.0,
        max=1.0,
        default=0.0,
        update=mesh_update_callback
    )
    store_original: BoolProperty(default=False)  # To store if we've saved original coords
    original_coords: CollectionProperty(type=PropertyGroup)  # To store original coordinates

class UVShapeKeySettings(PropertyGroup):
    """Settings for UV shape keys"""
    targets: CollectionProperty(type=UVShapeKeyTarget)
    active_target_index: IntProperty()
    base_coords: CollectionProperty(type=PropertyGroup)  # To store base mesh coordinates
    initialized: BoolProperty(default=False)  # To track if we've stored base coordinates

class UV_PT_ShapeKeys(Panel):
    bl_label = "UV Shape Keys"
    bl_idname = "UV_PT_shape_keys"
    bl_space_type = 'PROPERTIES'
    bl_region_type = 'WINDOW'
    bl_context = "data"

    @classmethod
    def poll(cls, context):
        return context.object and context.object.type == 'MESH'

    def draw(self, context):
        layout = self.layout
        settings = context.object.uv_shape_key_settings

        # Draw the target list
        row = layout.row()
        row.template_list("UI_UL_list", "uv_shape_key_targets",
                         settings, "targets",
                         settings, "active_target_index")

        # Add/Remove target buttons
        col = row.column(align=True)
        col.operator("object.uv_shape_key_target_add", icon='ADD', text="")
        col.operator("object.uv_shape_key_target_remove", icon='REMOVE', text="")

        # Draw active target properties
        if len(settings.targets) > 0 and settings.active_target_index < len(settings.targets):
            target = settings.targets[settings.active_target_index]
            layout.prop(target, "mesh")
            layout.prop(target, "value", slider=True)

def store_coordinates(coords_collection, vertices):
    """Store coordinates in a property collection"""
    coords_collection.clear()
    for v in vertices:
        item = coords_collection.add()
        item.name = str(tuple(v.co))  # Store as string since we can't store Vector directly

def get_coordinates(coords_collection):
    """Get coordinates from a property collection"""
    return np.array([eval(item.name) for item in coords_collection])

def get_uv_vertex_map(obj):
    """Returns a dictionary mapping UV coordinates to vertex indices"""
    uv_map = {}
    mesh = obj.data
    
    if not mesh.uv_layers.active:
        return {}

    bm = bmesh.new()
    bm.from_mesh(mesh)
    bm.faces.ensure_lookup_table()
    
    uv_layer = bm.loops.layers.uv.active
    
    for face in bm.faces:
        for loop in face.loops:
            uv_coord = tuple(round(c, 5) for c in loop[uv_layer].uv)
            vert_idx = loop.vert.index
            
            if uv_coord in uv_map:
                if vert_idx not in uv_map[uv_coord]:
                    uv_map[uv_coord].append(vert_idx)
            else:
                uv_map[uv_coord] = [vert_idx]
    
    bm.free()
    return uv_map

class UpdateUVShapeKeys(Operator):
    """Update UV shape key deformations"""
    bl_idname = "object.update_uv_shape_keys"
    bl_label = "Update UV Shape Keys"

    def execute(self, context):
        obj = context.object
        settings = obj.uv_shape_key_settings
        
        if not obj or not obj.data.vertices:
            return {'CANCELLED'}

        # Store base coordinates if not already stored
        if not settings.initialized:
            store_coordinates(settings.base_coords, obj.data.vertices)
            settings.initialized = True

        # Get original coordinates
        original_coords = get_coordinates(settings.base_coords)
        final_coords = original_coords.copy()
        source_uv_map = get_uv_vertex_map(obj)
        
        # Process each target
        for target in settings.targets:
            if not target.mesh or target.value == 0.0:
                continue
                
            target_uv_map = get_uv_vertex_map(target.mesh)
            
            # Store target's original coordinates if not already stored
            if not target.store_original:
                store_coordinates(target.original_coords, target.mesh.data.vertices)
                target.store_original = True
            
            target_coords = get_coordinates(target.original_coords)
            
            # Match vertices based on UV coordinates
            for uv_coord, source_verts in source_uv_map.items():
                if uv_coord in target_uv_map:
                    target_verts = target_uv_map[uv_coord]
                    
                    # Apply deformation
                    for source_vert in source_verts:
                        for target_vert in target_verts:
                            # Calculate the full delta between base and target positions
                            delta = target_coords[target_vert] - original_coords[source_vert]
                            # Apply the weighted delta to the current position
                            final_coords[source_vert] = original_coords[source_vert] + (delta * target.value)

        # Update mesh vertices
        for i, coord in enumerate(final_coords):
            obj.data.vertices[i].co = Vector(coord)
            
        obj.data.update()
        
        return {'FINISHED'}

class AddUVShapeKeyTarget(Operator):
    """Add a new UV shape key target"""
    bl_idname = "object.uv_shape_key_target_add"
    bl_label = "Add UV Shape Key Target"

    def execute(self, context):
        settings = context.object.uv_shape_key_settings
        target = settings.targets.add()
        target.name = f"Target {len(settings.targets)}"
        settings.active_target_index = len(settings.targets) - 1
        return {'FINISHED'}

class RemoveUVShapeKeyTarget(Operator):
    """Remove the active UV shape key target"""
    bl_idname = "object.uv_shape_key_target_remove"
    bl_label = "Remove UV Shape Key Target"

    def execute(self, context):
        settings = context.object.uv_shape_key_settings
        settings.targets.remove(settings.active_target_index)
        settings.active_target_index = min(settings.active_target_index,
                                         len(settings.targets) - 1)
        # Reset mesh to base coordinates
        bpy.ops.object.update_uv_shape_keys()
        return {'FINISHED'}

classes = (
    UVShapeKeyTarget,
    UVShapeKeySettings,
    UV_PT_ShapeKeys,
    UpdateUVShapeKeys,
    AddUVShapeKeyTarget,
    RemoveUVShapeKeyTarget,
)

def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.Object.uv_shape_key_settings = PointerProperty(type=UVShapeKeySettings)

def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
    del bpy.types.Object.uv_shape_key_settings

if __name__ == "__main__":
    register()