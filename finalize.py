# ##### BEGIN GPL LICENSE BLOCK #####
#
#  This program is free software; you can redistribute it and/or
#  modify it under the terms of the GNU General Public License
#  as published by the Free Software Foundation; either version 3
#  of the License, or (at your option) any later version.
#
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#  GNU General Public License for more details.
#
#  You should have received a copy of the GNU General Public License
#  along with this program; if not, write to the Free Software Foundation,
#  Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301, USA.
#
# ##### END GPL LICENSE BLOCK #####
#
# Copyright (C) 2020-2021 Michael Vigovsky

import numpy, logging, traceback
import bpy # pylint: disable=import-error

from . import library, morphing, fitting, rigging, rigify, utils

logger = logging.getLogger(__name__)

def remove_armature_modifiers(obj):
    for m in list(obj.modifiers):
        if m.type == "ARMATURE":
            obj.modifiers.remove(m)

def delete_old_rig(obj, rig):
    rigify.remove_rig(rig)
    remove_armature_modifiers(obj)

def clear_vg_names(vgs, vg_names):
    if not vg_names:
        return
    for vg in list(vgs):
        if vg.name in vg_names:
            vgs.remove(vg)

def clear_old_weights(obj, char, rig):
    vgs = obj.vertex_groups
    for bone in rig.data.bones:
        if bone.use_deform:
            vg = vgs.get(bone.name)
            if vg:
                vgs.remove(vg)
    clear_vg_names(vgs, set(rigging.char_rig_vg_names(char, rig)))

def clear_old_weights_with_assets(obj, char, rig):
    clear_old_weights(obj, char, rig)
    for asset in fitting.get_assets(obj):
        clear_old_weights(asset, char, rig)

def delete_old_rig_with_assets(obj, rig):
    delete_old_rig(obj, rig)
    for asset in fitting.get_assets(obj):
        remove_armature_modifiers(asset)

def add_rig(obj, char, rig_name, verts):
    conf = char.armature.get(rig_name)
    if not conf:
        raise rigging.RigException("Rig is not found")

    rig_type = conf.type
    if rig_type not in ("arp", "rigify", "regular"):
        raise rigging.RigException("Rig type {} is not supported".format(rig_type))

    rig = library.import_obj(char.path(conf.file), conf.obj_name, "ARMATURE")
    if not rig:
        raise rigging.RigException("Rig import failed")

    new_vgs = None
    err = None
    try:
        bpy.context.view_layer.objects.active = rig
        bpy.ops.object.mode_set(mode="EDIT")

        rig.data.use_mirror_x = False
        rigger = rigging.Rigger(bpy.context)
        rigger.configure(conf, obj, verts)
        joints = None
        if rig_type == "arp":
            joints = rigging.layer_joints(bpy.context, conf.arp_reference_layer)
        if not rigger.run(joints):
            raise rigging.RigException("Rig fitting failed")

        bpy.ops.object.mode_set(mode="OBJECT")

        old_rig = obj.find_armature()
        if old_rig:
            clear_old_weights_with_assets(obj, char, old_rig)

        if conf.weights:
            new_vgs = rigging.import_vg(obj, conf.weights, False)

        attach = True
        if rig_type == "rigify":
            rigify.apply_metarig_parameters(rig)
            metarig_only = bpy.context.window_manager.charmorph_ui.rigify_metarig_only
            if metarig_only or not hasattr(rig.data, "rigify_generate_mode"):
                if not metarig_only:
                    err = "Rigify is not found! Generating metarig only"
                utils.copy_transforms(rig, obj)
                attach = False
            else:
                rig = rigify.do_rig(obj, conf, rigger)

        if rig_type == "arp":
            if hasattr(bpy.ops, "arp") and hasattr(bpy.ops.arp, "match_to_rig"):
                try:
                    bpy.ops.arp.match_to_rig()
                except Exception as e:
                    err = str(e)
                    logger.error(traceback.format_exc())
            else:
                err = "Auto-Rig Pro is not found! Can't match the rig"

        rig.data["charmorph_template"] = obj.data.get("charmorph_template", "")
        rig.data["charmorph_rig_type"] = rig_name

        if old_rig:
            delete_old_rig_with_assets(obj, old_rig)

        if attach:
            attach_rig(obj, rig)
    except:
        try:
            clear_vg_names(obj.vertex_groups, new_vgs)
            bpy.data.armatures.remove(rig.data)
        except:
            pass
        raise
    return err

def attach_rig(obj, rig):
    utils.copy_transforms(rig, obj)
    utils.reset_transforms(obj)
    obj.parent = rig

    utils.lock_obj(obj, True)

    mod = obj.modifiers.new("charmorph_rig", "ARMATURE")
    mod.use_vertex_groups = True
    mod.object = rig
    rigging.reposition_armature_modifier(obj)
    if "preserve_volume" in obj.vertex_groups or "preserve_volume_inv" in obj.vertex_groups:
        mod2 = obj.modifiers.new("charmorph_rig_pv", "ARMATURE")
        mod2.use_vertex_groups = True
        mod2.use_deform_preserve_volume = True
        mod2.use_multi_modifier = True
        mod2.object = rig
        if "preserve_volume_inv" in obj.vertex_groups:
            mod2.vertex_group = "preserve_volume_inv"
        else:
            mod2.vertex_group = "preserve_volume"
            mod2.invert_vertex_group = True
        rigging.reposition_armature_modifier(obj)
    else:
        mod.use_deform_preserve_volume = True

    if bpy.context.window_manager.charmorph_ui.fitting_armature:
        fitting.transfer_new_armature(obj)

#FIXME: Use data from morpher instead? But we should consider manual sculpting too.
def sk_to_verts(obj, sk):
    if isinstance(sk, str):
        k = obj.data.shape_keys
        if k and k.key_blocks:
            sk = k.key_blocks.get(sk)
    if sk is None:
        return
    arr = numpy.empty(len(sk.data) * 3)
    sk.data.foreach_get("co", arr)
    obj.data.vertices.foreach_set("co", arr)

class OpFinalize(bpy.types.Operator):
    bl_idname = "charmorph.finalize"
    bl_label = "Finalize"
    bl_description = "Finalize character (add rig, modifiers, cleanup)"
    bl_options = {"UNDO"}

    @classmethod
    def poll(cls, context):
        return context.mode == "OBJECT" and library.get_obj_char(context)[0]

    def execute(self, context):
        t = utils.Timer()
        if context.view_layer != bpy.context.view_layer:
            self.report({'ERROR'}, "Bad context")
            return {"CANCELLED"}

        ui = context.window_manager.charmorph_ui
        obj, char = library.get_obj_char(context)
        if not char.name or not char.config:
            self.report({'ERROR'}, "Character config is not found")
            return {"CANCELLED"}

        unused_l1 = set()

        keys = obj.data.shape_keys
        fin_sk = None
        fin_sk_tmp = False
        if keys and keys.key_blocks:
            if ui.fin_morph != "NO" or ui.fin_rig != "-":
                fin_sk = keys.key_blocks.get("charmorph_final")
                if not fin_sk:
                    fin_sk_tmp = ui.fin_morph == "NO"
                    fin_sk = obj.shape_key_add(name="charmorph_final", from_mix=True)
                fin_sk.value = 1

            unknown_keys = False

            for key in keys.key_blocks:
                if key.name.startswith("L1_") and key.value < 0.01:
                    unused_l1.add(key.name[3:])
                if ui.fin_morph != "NO" and key != keys.reference_key and key != fin_sk:
                    if key.name.startswith("L1_") or key.name.startswith("L2_"):
                        obj.shape_key_remove(key)
                    else:
                        unknown_keys = True

            if ui.fin_morph == "AL":
                if unknown_keys:
                    self.report({"WARNING"}, "Unknown shape keys found. Keeping original basis anyway")
                else:
                    obj.shape_key_remove(keys.reference_key)
                    obj.shape_key_remove(fin_sk)
                    fin_sk = None

        if ui.fin_morph != "NO" and "cm_morpher" in obj.data:
            del obj.data["cm_morpher"]
            for k in [k for k in obj.data.keys() if k.startswith("cmorph_")]:
                del obj.data[k]

        sk_to_verts(obj, fin_sk)

        vg_cleanup = ui.fin_vg_cleanup
        def do_rig():
            nonlocal vg_cleanup
            if ui.fin_rig == "-":
                return True
            try:
                err = add_rig(obj, char, ui.fin_rig, fin_sk.data if fin_sk else obj.data.vertices)
                if err is not None:
                    self.report({"ERROR"}, err)
                    vg_cleanup = False
            except rigging.RigException as e:
                self.report({"ERROR"}, str(e))
                return False
            return True

        ok = do_rig()

        if fin_sk_tmp:
            # Remove temporary mix shape key
            obj.shape_key_remove(fin_sk)

        if not ok:
            return {"CANCELLED"}

        def add_modifier(obj, typ, reposition):
            for mod in obj.modifiers:
                if mod.type == typ:
                    return mod
            mod = obj.modifiers.new("charmorph_" + typ.lower(), typ)
            reposition(obj)
            return mod

        def add_corrective_smooth(obj):
            if ui.fin_csmooth == "NO":
                return
            mod = add_modifier(obj, "CORRECTIVE_SMOOTH", rigging.reposition_cs_modifier)
            mod.smooth_type = ui.fin_csmooth[2:]
            if ui.fin_csmooth[:1] == "L":
                if "corrective_smooth" in obj.vertex_groups:
                    mod.vertex_group = "corrective_smooth"
                elif "corrective_smooth_inv" in obj.vertex_groups:
                    mod.vertex_group = "corrective_smooth_inv"
                    mod.invert_vertex_group = True
            elif ui.fin_csmooth == "U":
                mod.vertex_group = ""

        def add_subsurf(obj):
            if ui.fin_subdivision == "NO":
                return
            mod = add_modifier(obj, "SUBSURF", rigging.reposition_subsurf_modifier)
            mod.show_viewport = ui.fin_subdivision == "RV"

        add_corrective_smooth(obj)
        add_subsurf(obj)

        for asset in fitting.get_assets(obj):
            if ui.fin_csmooth_assets == "RO":
                sk_to_verts(asset, "charmorph_fitting")
            elif ui.fin_csmooth_assets == "FR":
                sk_to_verts(asset, "Basis")
            if ui.fin_csmooth_assets != "NO":
                add_corrective_smooth(asset)
            if ui.fin_subdiv_assets:
                add_subsurf(asset)


        if vg_cleanup:
            if hasattr(context.window_manager, "chartype"):
                current_l1 = context.window_manager.chartype
                m = morphing.morpher
                if m and m.obj == obj and m.morphs_l1:
                    for l1 in m.morphs_l1:
                        if l1 != current_l1:
                            unused_l1.add(l1)

                hair_vg = obj.vertex_groups.get("hair_" + current_l1)
                if hair_vg and "hair" not in obj.vertex_groups:
                    hair_vg.name = "hair"

            # Make sure we won't delete any vertex groups used by hair particle systems
            for psys in obj.particle_systems:
                for attr in dir(psys):
                    if attr.startswith("vertex_group_"):
                        vg = getattr(psys, attr)
                        if vg.startswith("hair_"):
                            unused_l1.remove(vg[5:])

            for vg in obj.vertex_groups:
                if vg.name.startswith("joint_") or (
                        vg.name.startswith("hair_") and vg.name[5:] in unused_l1):
                    obj.vertex_groups.remove(vg)

        morphing.del_charmorphs()

        t.time("total finalize")

        return {"FINISHED"}

class OpUnrig(bpy.types.Operator):
    bl_idname = "charmorph.unrig"
    bl_label = "Unrig"
    bl_description = "Remove all riging data from the character and all its assets so you can continue morphing it"
    bl_options = {"UNDO"}

    @classmethod
    def poll(cls, context):
        if context.mode != "OBJECT":
            return False
        obj = library.get_obj_char(context)[0]
        return obj.find_armature()

    def execute(self, context): # pylint: disable=no-self-use
        obj, char = library.get_obj_char(context)

        old_rig = obj.find_armature()
        if old_rig:
            if obj.parent is old_rig:
                utils.copy_transforms(obj, old_rig)
                utils.lock_obj(obj, False)
            clear_old_weights_with_assets(obj, char, old_rig)

        delete_old_rig_with_assets(obj, old_rig)

        return {"FINISHED"}

def get_rigs(_, context):
    char = library.get_obj_char(context)[1]
    if not char:
        return []
    return [("-", "<None>", "Don't generate rig")] + [(name, rig.title, "") for name, rig in char.armature.items()]

class UIProps:
    fin_morph: bpy.props.EnumProperty(
        name="Apply morphs",
        default="SK",
        items=[
            ("NO", "Don't apply", "Keep all morphing shape keys"),
            ("SK", "Keep original basis", "Keep original basis shape key (recommended if you plan to fit more assets)"),
            ("AL", "Full apply", "Apply current mix as new basis and remove all shape keys"),
        ],
        description="Apply current shape key mix")
    fin_rig: bpy.props.EnumProperty(
        name="Rig",
        items=get_rigs,
        description="Rigging options")
    fin_subdivision: bpy.props.EnumProperty(
        name="Subdivision",
        default="RO",
        items=[
            ("NO", "No", "No subdivision surface"),
            ("RO", "Render only", "Use subdivision only for rendering"),
            ("RV", "Render+Viewport", "Use subdivision for rendering and viewport (may be slow on old hardware)"),
        ],
        description="Use subdivision surface for smoother look")
    fin_csmooth: bpy.props.EnumProperty(
        name="Corrective smooth",
        default="L_SIMPLE",
        items=[
            ("NO", "None", "No corrective smooth"),
            ("L_SIMPLE", "Limited Simple", ""),
            ("L_LENGTH_WEIGHTED", "Limited Length weighted", ""),
            ("U_SIMPLE", "Unlimited Simple", ""),
            ("U_LENGTH_WEIGHTED", "Unimited Length weighted", ""),
        ],
        description="Use corrective smooth to fix armature deform artifacts")
    fin_csmooth_assets: bpy.props.EnumProperty(
        name="Corrective smooth for assets",
        default="NO",
        description="Use corrective smooth for assets too",
        items=[
            ("NO", "None", "No corrective smooth"),
            ("FR", "Fitting+Rig", "Allow to smooth artifacts caused by fitting and armature deform"),
            ("RO", "Rig only", "Allow to smooth only artifacts caused by armature deform"),
            ("NC", "No change", "Apply corrective smooth to assets but don't change its parameters"),
        ],
        )
    fin_subdiv_assets: bpy.props.BoolProperty(
        name="Subdivide assets",
        default=False,
        description="Subdivide assets together with character")
    fin_vg_cleanup: bpy.props.BoolProperty(
        name="Cleanup vertex groups",
        default=False,
        description="Remove unused vertex groups after finalization")

class CHARMORPH_PT_Finalize(bpy.types.Panel):
    bl_label = "Finalization"
    bl_parent_id = "VIEW3D_PT_CharMorph"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_order = 9

    @classmethod
    def poll(cls, context):
        return context.mode == "OBJECT" and library.get_obj_char(context)[0]

    def draw(self, context):
        l = self.layout
        for prop in UIProps.__annotations__: # pylint: disable=no-member
            l.prop(context.window_manager.charmorph_ui, prop)
        l.operator("charmorph.finalize")
        l.operator("charmorph.unrig")

classes = [OpFinalize, OpUnrig, CHARMORPH_PT_Finalize]
