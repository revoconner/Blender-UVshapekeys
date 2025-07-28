bl_info = {
    "name": "UV ShapeKeys",
    "author": "Rév",
    "version": (2, 2),
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
    if self.mesh:
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
    store_original: BoolProperty(default=False)
    original_coords: CollectionProperty(type=PropertyGroup)

class UVShapeKeySettings(PropertyGroup):
    """Settings for UV shape keys"""
    targets: CollectionProperty(type=UVShapeKeyTarget)
    active_target_index: IntProperty()
    base_coords: CollectionProperty(type=PropertyGroup)
    initialized: BoolProperty(default=False)

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

        row = layout.row()
        row.template_list("UI_UL_list", "uv_shape_key_targets",
                         settings, "targets",
                         settings, "active_target_index")

        col = row.column(align=True)
        col.operator("object.uv_shape_key_target_add", icon='ADD', text="")
        col.operator("object.uv_shape_key_target_remove", icon='REMOVE', text="")

        if len(settings.targets) > 0 and settings.active_target_index < len(settings.targets):
            target = settings.targets[settings.active_target_index]
            layout.prop(target, "mesh")
            layout.prop(target, "value", slider=True)

        # Add save deformation section
        layout.separator()
        
        # Check if there are any active deformations
        has_active_deformation = any(abs(target.value) > 1e-6 for target in settings.targets if target.mesh)
        
        row = layout.row()
        row.enabled = has_active_deformation
        row.operator("object.save_uv_shape_deformation", icon='FILE_TICK')
        
        if has_active_deformation:
            box = layout.box()
            box.label(text="Warning:", icon='INFO')
            box.label(text="This will permanently apply")
            box.label(text="the current deformation to")
            box.label(text="the mesh and reset all values.")

def store_coordinates(coords_collection, vertices):
    """Store coordinates in a property collection"""
    coords_collection.clear()
    for v in vertices:
        item = coords_collection.add()
        item.name = str(tuple(v.co))

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
        
        # Dictionary to store all deltas and their respective values for each vertex
        vertex_deltas = {i: {'deltas': [], 'values': []} for i in range(len(original_coords))}
        
        # Calculate deltas from all targets
        for target in settings.targets:
            if not target.mesh or abs(target.value) < 1e-6:  # Skip if value is effectively zero
                continue
                
            target_uv_map = get_uv_vertex_map(target.mesh)
            
            if not target.store_original:
                store_coordinates(target.original_coords, target.mesh.data.vertices)
                target.store_original = True
            
            target_coords = get_coordinates(target.original_coords)
            
            # Match vertices based on UV coordinates
            for uv_coord, source_verts in source_uv_map.items():
                if uv_coord in target_uv_map:
                    target_verts = target_uv_map[uv_coord]
                    
                    for source_vert in source_verts:
                        for target_vert in target_verts:
                            # Calculate raw delta and store with its value
                            delta = target_coords[target_vert] - original_coords[source_vert]
                            # Only store if the delta is significant
                            if np.any(np.abs(delta) > 1e-6):
                                vertex_deltas[source_vert]['deltas'].append(delta)
                                vertex_deltas[source_vert]['values'].append(target.value)

        # Process the deltas for each vertex
        for vert_idx, data in vertex_deltas.items():
            if not data['deltas']:
                continue
                
            deltas_array = np.array(data['deltas'])
            values_array = np.array(data['values'])
            
            # Process each dimension (x, y, z) separately
            for dim in range(3):
                dim_deltas = deltas_array[:, dim]
                
                # Skip if no significant deltas
                if not np.any(np.abs(dim_deltas) > 1e-6):
                    continue
                
                # Group similar deltas (within tolerance)
                tolerance = 1e-5
                unique_groups = {}
                
                for delta, value in zip(dim_deltas, values_array):
                    found_group = False
                    for group_delta in unique_groups:
                        if abs(delta - group_delta) < tolerance:
                            unique_groups[group_delta].append(value)
                            found_group = True
                            break
                    if not found_group:
                        unique_groups[delta] = [value]
                
                # Calculate final delta for this dimension
                final_delta = 0
                for delta, values in unique_groups.items():
                    # For similar deltas, use the maximum value
                    final_delta += delta * max(values)
                
                final_coords[vert_idx][dim] = original_coords[vert_idx][dim] + final_delta

        # Update mesh vertices
        for i, coord in enumerate(final_coords):
            obj.data.vertices[i].co = Vector(coord)
            
        obj.data.update()
        
        return {'FINISHED'}

class SaveUVShapeDeformation(Operator):
    """Save the current UV shape key deformation permanently to the mesh"""
    bl_idname = "object.save_uv_shape_deformation"
    bl_label = "Save Deformation to Mesh"
    bl_description = "Permanently apply the current deformation to the mesh and reset all target values"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        obj = context.object
        if not obj or obj.type != 'MESH':
            return False
        settings = obj.uv_shape_key_settings
        # Only enable if there are active deformations
        return any(abs(target.value) > 1e-6 for target in settings.targets if target.mesh)

    def invoke(self, context, event):
        # Show confirmation dialog
        return context.window_manager.invoke_confirm(self, event)

    def execute(self, context):
        obj = context.object
        settings = obj.uv_shape_key_settings
        
        if not obj or not obj.data.vertices:
            self.report({'ERROR'}, "No valid mesh object")
            return {'CANCELLED'}

        # First, make sure the current deformation is applied
        bpy.ops.object.update_uv_shape_keys()
        
        # Store the current deformed coordinates as the new base
        store_coordinates(settings.base_coords, obj.data.vertices)
        
        # Reset all target values to 0
        for target in settings.targets:
            target.value = 0.0
        
        # Update the mesh one more time to ensure consistency
        bpy.ops.object.update_uv_shape_keys()
        
        self.report({'INFO'}, "Deformation saved to mesh successfully")
        return {'FINISHED'}

class ResetUVShapeKeys(Operator):
    """Reset all UV shape key values and restore original mesh"""
    bl_idname = "object.reset_uv_shape_keys"
    bl_label = "Reset All Values"
    bl_description = "Reset all target values to 0 and restore the original mesh shape"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        obj = context.object
        if not obj or obj.type != 'MESH':
            return False
        settings = obj.uv_shape_key_settings
        return settings.initialized and len(settings.targets) > 0

    def execute(self, context):
        obj = context.object
        settings = obj.uv_shape_key_settings
        
        # Reset all target values
        for target in settings.targets:
            target.value = 0.0
        
        # Update to restore original shape
        bpy.ops.object.update_uv_shape_keys()
        
        self.report({'INFO'}, "All UV shape key values reset")
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
        # Reset mesh to base coordinates and reapply remaining targets
        bpy.ops.object.update_uv_shape_keys()
        return {'FINISHED'}

# Enhanced UI Panel
class UV_PT_ShapeKeys_Enhanced(Panel):
    bl_label = "UV Shape Keys"
    bl_idname = "UV_PT_shape_keys_enhanced"
    bl_space_type = 'PROPERTIES'
    bl_region_type = 'WINDOW'
    bl_context = "data"

    @classmethod
    def poll(cls, context):
        return context.object and context.object.type == 'MESH'

    def draw(self, context):
        layout = self.layout
        settings = context.object.uv_shape_key_settings

        # Target list
        row = layout.row()
        row.template_list("UI_UL_list", "uv_shape_key_targets",
                         settings, "targets",
                         settings, "active_target_index")

        col = row.column(align=True)
        col.operator("object.uv_shape_key_target_add", icon='ADD', text="")
        col.operator("object.uv_shape_key_target_remove", icon='REMOVE', text="")

        # Target properties
        if len(settings.targets) > 0 and settings.active_target_index < len(settings.targets):
            target = settings.targets[settings.active_target_index]
            layout.prop(target, "mesh")
            layout.prop(target, "value", slider=True)

        # Control buttons section
        layout.separator()
        
        # Check if there are any active deformations
        has_active_deformation = any(abs(target.value) > 1e-6 for target in settings.targets if target.mesh)
        has_targets = len(settings.targets) > 0 and settings.initialized
        
        # Save deformation button
        row = layout.row()
        row.scale_y = 1.2
        row.enabled = has_active_deformation
        row.operator("object.save_uv_shape_deformation", icon='FILE_TICK')
        
        # Reset button
        row = layout.row()
        row.enabled = has_targets
        row.operator("object.reset_uv_shape_keys", icon='LOOP_BACK')
        
        # Information box
        if has_active_deformation:
            box = layout.box()
            box.label(text="Active Deformations:", icon='INFO')
            for i, target in enumerate(settings.targets):
                if target.mesh and abs(target.value) > 1e-6:
                    row = box.row()
                    row.label(text=f"• {target.name}: {target.value:.3f}")

classes = (
    UVShapeKeyTarget,
    UVShapeKeySettings,
    UV_PT_ShapeKeys_Enhanced,
    UpdateUVShapeKeys,
    SaveUVShapeDeformation,
    ResetUVShapeKeys,
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
