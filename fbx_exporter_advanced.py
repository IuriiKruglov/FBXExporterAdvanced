# SPDX-License-Identifier: GPL-3.0-or-later
#
#  FBX Exporter Advanced
#  Created by Claude (Anthropic) under the leadership of Kruglov Iurii.
#
#  Copyright (C) 2026 Kruglov Iurii
#
#  This program is free software: you can redistribute it and/or modify
#  it under the terms of the GNU General Public License as published by
#  the Free Software Foundation, either version 3 of the License, or
#  (at your option) any later version.
#
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#  GNU General Public License for more details.
#
#  You should have received a copy of the GNU General Public License
#  along with this program.  If not, see <https://www.gnu.org/licenses/>.
"""
Exports the asset assembly to FBX the way 3ds Max writes it after the
Blender -> 3ds Max Bridge, without going through 3ds Max:

  * meshes, normals, UVs, colours, materials and textures are written by
    Blender's own FBX exporter (io_scene_fbx), unchanged;
  * the assembly is rebuilt the 3ds Max way: Y-up through a -90 deg X
    PreRotation on root nodes, centimetres, local transforms relative to the
    3ds Max parent, Euler rotation orders, keys on Blender's key frames with
    3ds Max tangents (or no animation), armatures as Null "dummies" (Z forward,
    Y up) with a Skin deformer and a bind pose.

The conventions were measured on 3ds Max 2024 / FBX plug-in 2020.3.2 files.
"""

bl_info = {
    "name": "FBX Exporter Advanced",
    "author": "Kruglov Iurii, Claude (Anthropic)",
    "version": (0, 3, 1),
    "blender": (3, 6, 0),
    "location": "File > Export > FBX Advanced (.fbx)",
    "description": "FBX export of the asset assembly as 3ds Max writes it: Y-up pre-rotation, centimetres, "
                   "rotation orders, exact keys, bones as dummies with Skin and bind pose. "
                   "Geometry from Blender's FBX exporter",
    "doc_url": "https://github.com/IuriiKruglov/FBXExporterAdvanced",
    "tracker_url": "https://github.com/IuriiKruglov/FBXExporterAdvanced/issues",
    "category": "Import-Export",
}

import math
import os
import re
import struct
import tempfile
import time
import zlib

import bpy
import numpy as np
from bpy.props import BoolProperty, EnumProperty, FloatProperty, IntProperty, StringProperty
from bpy_extras.io_utils import ExportHelper
from mathutils import Euler, Matrix, Quaternion, Vector

ADDON_VERSION = "0.3.1"
KTIME = 46186158000                       # FBX time units per second
FBX_VERSION = 7400                        # FBX 2014/2015, like the 3ds Max preset
CONV = Matrix.Rotation(-math.pi / 2.0, 4, 'X')   # Z-up -> Y-up, written as PreRotation on roots
DUMMY_AXES = Matrix.Rotation(math.pi / 2.0, 4, 'X')  # bone axes -> 3ds Max dummy axes (Z forward, Y up)
AXES = ("X", "Y", "Z")
ORDER_ENUM = {'XYZ': 0, 'XZY': 1, 'YZX': 2, 'YXZ': 3, 'ZXY': 4, 'ZYX': 5}
EXPORT_TYPES = {'EMPTY', 'MESH', 'ARMATURE', 'CURVE', 'SURFACE', 'FONT', 'META'}

# FBX key flags (FbxAnimCurveDef)
INTERP_CONSTANT, INTERP_LINEAR, INTERP_CUBIC = 0x2, 0x4, 0x8
TANGENT_AUTO, TANGENT_USER = 0x100, 0x400
TANGENT_TIME_INDEPENDENT, TANGENT_CLAMP_PROGRESSIVE = 0x2000, 0x4000
WEIGHTED_RIGHT, WEIGHTED_NEXT_LEFT = 0x1000000, 0x2000000
MAX_AUTO = INTERP_CUBIC | TANGENT_AUTO | TANGENT_TIME_INDEPENDENT | TANGENT_CLAMP_PROGRESSIVE
MAX_USER = INTERP_CUBIC | TANGENT_USER

FPS_ENUM = {120: 1, 100: 2, 60: 3, 50: 4, 48: 5, 30: 6, 25: 10, 24: 11, 1000: 12, 96: 15, 72: 16}


# ═════════════════════════════════════════════════════════════════════════════
#  FBX binary file: read into a tree, write a tree (same layout as io_scene_fbx)
# ═════════════════════════════════════════════════════════════════════════════

_HEAD_MAGIC = b'Kaydara FBX Binary\x20\x20\x00\x1a\x00'
_FILE_ID = b'\x28\xb3\x2a\xeb\xb6\x24\xcc\xc2\xbf\xc8\xb0\x2a\xa9\x2b\xfc\xf1'
_TIME_ID = b'1970-01-01 10:00:00:000'
_FOOT_ID = b'\xfa\xbc\xab\x09\xd0\xc8\xd4\x66\xb1\x76\xfb\x83\x1c\xf7\x26\x7e'
_ALWAYS_SENTINEL = {b"AnimationStack", b"AnimationLayer"}
_ARRAY_DTYPE = {"f": "<f4", "d": "<f8", "l": "<i8", "i": "<i4", "b": "i1"}


class Elem:
    """One FBX record: id, typed properties, children. Strings are bytes."""
    __slots__ = ("id", "props", "types", "elems")

    def __init__(self, id, props=None, types="", elems=None):
        self.id = id if isinstance(id, bytes) else id.encode()
        self.props = list(props) if props is not None else []
        self.types = types
        self.elems = elems if elems is not None else []

    def find(self, id):
        id = id if isinstance(id, bytes) else id.encode()
        for e in self.elems:
            if e.id == id:
                return e
        return None

    def find_all(self, id):
        id = id if isinstance(id, bytes) else id.encode()
        return [e for e in self.elems if e.id == id]

    def add(self, id, props=(), types="", index=None):
        e = Elem(id, [(_b(p) if t in "SR" else p) for p, t in zip(props, types)], types)
        if index is None:
            self.elems.append(e)
        else:
            self.elems.insert(index, e)
        return e


def _b(v):
    return v if isinstance(v, bytes) else str(v).encode("utf-8")


def fbx_read(path):
    with open(path, "rb") as f:
        data = f.read()
    if data[:21] != _HEAD_MAGIC[:21]:
        raise ValueError("Not a binary FBX file: %s" % path)
    version = struct.unpack_from("<I", data, 23)[0]
    wide = version >= 7500
    hdr = struct.Struct("<QQQB" if wide else "<IIIB")
    null_len = hdr.size

    def read(off):
        end, nprops, _plen, nlen = hdr.unpack_from(data, off)
        off += hdr.size
        if end == 0:
            return None, off
        e = Elem(data[off:off + nlen])
        off += nlen
        types = []
        for _ in range(nprops):
            t = chr(data[off])
            off += 1
            types.append(t)
            if t == "Y":
                e.props.append(struct.unpack_from("<h", data, off)[0]); off += 2
            elif t == "C":
                e.props.append(data[off:off + 1]); off += 1
            elif t == "I":
                e.props.append(struct.unpack_from("<i", data, off)[0]); off += 4
            elif t == "F":
                e.props.append(struct.unpack_from("<f", data, off)[0]); off += 4
            elif t == "D":
                e.props.append(struct.unpack_from("<d", data, off)[0]); off += 8
            elif t == "L":
                e.props.append(struct.unpack_from("<q", data, off)[0]); off += 8
            elif t in "SR":
                n = struct.unpack_from("<I", data, off)[0]; off += 4
                e.props.append(data[off:off + n]); off += n
            elif t in _ARRAY_DTYPE:
                alen, enc, clen = struct.unpack_from("<III", data, off); off += 12
                raw = data[off:off + clen]; off += clen
                if enc == 1:
                    raw = zlib.decompress(raw)
                e.props.append(np.frombuffer(raw, dtype=_ARRAY_DTYPE[t], count=alen).copy())
            else:
                raise ValueError("Unknown FBX property type %r" % t)
        e.types = "".join(types)
        while off < end:
            if data[off:off + null_len] == b"\0" * null_len:
                off += null_len
                break
            child, off = read(off)
            if child is None:
                break
            e.elems.append(child)
        return e, end

    root = Elem(b"")
    off = 27
    while off + null_len <= len(data) and data[off:off + null_len] != b"\0" * null_len:
        e, off = read(off)
        if e is None:
            break
        root.elems.append(e)
    return root, version


def _encode_prop(t, v):
    if t == "Y":
        return struct.pack("<h", v)
    if t == "C":
        return v if isinstance(v, bytes) else (b"\x01" if v else b"\x00")
    if t == "I":
        return struct.pack("<i", int(v))
    if t == "F":
        return struct.pack("<f", float(v))
    if t == "D":
        return struct.pack("<d", float(v))
    if t == "L":
        return struct.pack("<q", int(v))
    if t in "SR":
        v = _b(v)
        return struct.pack("<I", len(v)) + v
    if t in _ARRAY_DTYPE:
        arr = np.ascontiguousarray(np.asarray(v, dtype=_ARRAY_DTYPE[t]))
        raw = arr.tobytes()
        if len(raw) > 128:
            comp = zlib.compress(raw, 1)
            return struct.pack("<III", arr.size, 1, len(comp)) + comp
        return struct.pack("<III", arr.size, 0, len(raw)) + raw
    raise ValueError("Unknown FBX property type %r" % t)


def fbx_write(path, root, version=FBX_VERSION):
    wide = version >= 7500
    meta = "<3Q" if wide else "<3I"
    meta_size = 24 if wide else 12
    sentinel = b"\0" * (meta_size + 1)

    for e in root.elems:                                # same FileId / CreationTime pair as io_scene_fbx
        if e.id == b"FileId":
            e.props, e.types = [_FILE_ID], "R"
        elif e.id == b"CreationTime":
            e.props, e.types = [_TIME_ID], "S"

    out = bytearray()
    out += _HEAD_MAGIC + struct.pack("<I", version)

    def encode(e, is_last):
        start = len(out)
        out.extend(b"\0" * meta_size)                   # patched below
        out.append(len(e.id))
        out.extend(e.id)
        p0 = len(out)
        for t, v in zip(e.types, e.props):
            out.append(ord(t))
            out.extend(_encode_prop(t, v))
        plen = len(out) - p0
        if e.elems:
            last = e.elems[-1]
            for c in e.elems:
                encode(c, c is last)
            out.extend(sentinel)
        elif (not e.props and not is_last) or e.id in _ALWAYS_SENTINEL:
            out.extend(sentinel)
        struct.pack_into(meta, out, start, len(out), len(e.props), plen)

    last = root.elems[-1] if root.elems else None
    for e in root.elems:
        encode(e, e is last and False)
    out.extend(sentinel)
    out += _FOOT_ID + b"\0" * 4
    pad = ((len(out) + 15) & ~15) - len(out)
    out += b"\0" * (pad if pad else 16)
    out += struct.pack("<I", version) + b"\0" * 120
    out += b'\xf8\x5a\x8c\x6a\xde\xf5\xd9\x7e\xec\xe9\x0c\xe3\x75\x8f\x29\x0b'
    with open(path, "wb") as f:
        f.write(out)


# ── Properties70 helpers ─────────────────────────────────────────────────────

def p70_set(props_elem, name, ptype, label, flags, values, vtypes):
    """Replace or append a P record."""
    name_b = _b(name)
    for e in props_elem.elems:
        if e.id == b"P" and e.props and e.props[0] == name_b:
            props_elem.elems.remove(e)
            break
    return props_elem.add("P", [name, ptype, label, flags] + list(values), "SSSS" + vtypes)


def p70_remove(props_elem, names):
    names = {_b(n) for n in names}
    props_elem.elems = [e for e in props_elem.elems if not (e.id == b"P" and e.props and e.props[0] in names)]


def p_vec(props, name, v, flags="", ptype="Vector3D", label="Vector"):
    return props.add("P", [name, ptype, label, flags, float(v[0]), float(v[1]), float(v[2])], "SSSSDDD")


def p_lcl(props, name, v, animated):
    return props.add("P", [name, name, "", "A+" if animated else "A", float(v[0]), float(v[1]), float(v[2])],
                     "SSSSDDD")


def p_int(props, name, ptype, label, value):
    return props.add("P", [name, ptype, label, "", int(value)], "SSSSI")


def p_time(props, name, value):
    return props.add("P", [name, "KTime", "Time", "", int(value)], "SSSSL")


def p_double(props, name, value, ptype="double", label="Number", flags=""):
    return props.add("P", [name, ptype, label, flags, float(value)], "SSSSD")


# ═════════════════════════════════════════════════════════════════════════════
#  Blender data access (shared design with the Blender -> 3ds Max Bridge 2.3)
# ═════════════════════════════════════════════════════════════════════════════

def assigned_fcurves(id_data):
    """F-Curves of the action that animates id_data (Blender 3.6 - 5.x, slotted actions)."""
    ad = getattr(id_data, "animation_data", None)
    if ad is None or ad.action is None:
        return ()
    act = ad.action
    if hasattr(act, "layers"):
        slot = getattr(ad, "action_slot", None)
        if slot is None:
            return ()
        try:
            from bpy_extras.anim_utils import action_get_channelbag_for_slot
        except ImportError:
            action_get_channelbag_for_slot = None
        if action_get_channelbag_for_slot is not None:
            bag = action_get_channelbag_for_slot(act, slot)
            return tuple(bag.fcurves) if bag is not None else ()
        for layer in act.layers:
            for strip in layer.strips:
                bag = strip.channelbag(slot) if hasattr(strip, "channelbag") else None
                if bag is not None:
                    return tuple(bag.fcurves)
        return ()
    return tuple(act.fcurves)


def _quat_from_mode(mode, values):
    if mode == 'QUATERNION':
        return Quaternion(values).normalized()
    axis = Vector(values[1:4])
    if axis.length < 1e-12:
        return Quaternion()
    return Quaternion(axis.normalized(), values[0])


def unit_to_cm(scene):
    """Blender units -> centimetres, as io_scene_fbx computes it with Apply Unit."""
    us = scene.unit_settings
    return 100.0 if us.system == 'NONE' else 100.0 * us.scale_length


_POSE_PATH = re.compile(r'^pose\.bones\["((?:[^"\\]|\\.)*)"\]\.'
                        r'(location|rotation_quaternion|rotation_euler|rotation_axis_angle|scale)$')
_OBJ_PATHS = {"location": "T", "delta_location": "T",
              "rotation_euler": "R", "rotation_quaternion": "R", "rotation_axis_angle": "R",
              "delta_rotation_euler": "R", "delta_rotation_quaternion": "R",
              "scale": "S", "delta_scale": "S"}
_BONE_PATHS = {"location": "T", "rotation_euler": "R", "rotation_quaternion": "R",
               "rotation_axis_angle": "R", "scale": "S"}


class RigPlan:
    """How one armature object is converted to dummies."""

    def __init__(self, arm):
        self.arm = arm
        self.name = arm.name
        self.node = False
        self.bones = []
        self.bone_set = set()
        self.bake = False
        self.parent_ref = None
        self.warnings = []


def _bones_parents_first(bones):
    out, seen = [], set()

    def visit(b):
        if b.name in seen:
            return
        if b.parent is not None:
            visit(b.parent)
        seen.add(b.name)
        out.append(b)

    for b in bones:
        visit(b)
    return out


def _object_animated(obj):
    if any(fc.data_path in _OBJ_PATHS and not fc.mute for fc in assigned_fcurves(obj)):
        return True
    ad = obj.animation_data
    if ad is not None and any(fc.data_path in _OBJ_PATHS and not fc.mute for fc in ad.drivers):
        return True
    return any(not c.mute for c in obj.constraints)


def _rig_needs_bake(arm, bones):
    if any(not c.mute for pb in arm.pose.bones for c in pb.constraints):
        return True
    ad = arm.animation_data
    if ad is not None:
        if any(fc.data_path.startswith("pose.bones[") and not fc.mute for fc in ad.drivers):
            return True
        if ad.use_nla and any(len(t.strips) for t in ad.nla_tracks if not t.mute):
            return True
    return any(not b.use_inherit_rotation or getattr(b, "inherit_scale", 'FULL') != 'FULL' for b in bones)


def resolve_parent(obj, plans, scope_set):
    """('object', obj) / ('bone', (armature, bone)) / None: the node obj is parented to."""
    child, par = obj, obj.parent
    while par is not None:
        if par not in scope_set:
            return None
        plan = plans.get(par.name) if par.type == 'ARMATURE' else None
        if plan is None:
            return ('object', par)
        if child.parent_type == 'BONE' and child.parent_bone in plan.bone_set:
            return ('bone', (plan.name, child.parent_bone))
        if plan.node:
            return ('object', par)
        child, par = par, par.parent
    return None


def plan_rigs(scope, bake_all=False):
    scope_set = set(scope)
    plans = {}
    for arm in scope:
        if arm.type != 'ARMATURE':
            continue
        p = RigPlan(arm)
        bones = arm.data.bones
        wanted = [b for b in bones if b.use_deform]
        wanted += [bones[o.parent_bone] for o in scope
                   if o.parent is arm and o.parent_type == 'BONE' and o.parent_bone in bones]
        keep = set()
        for b in wanted:
            x = b
            while x is not None and x.name not in keep:
                keep.add(x.name)
                x = x.parent
        p.bones = [b for b in _bones_parents_first(list(bones)) if b.name in keep]
        p.bone_set = keep
        p.node = _object_animated(arm)
        p.bake = bake_all or _rig_needs_bake(arm, p.bones)
        if not p.bones:
            p.warnings.append("Armature '%s' has no deform bones, nothing to convert." % arm.name)
        for b in p.bones:
            if b.use_deform and b.bbone_segments > 1:
                p.warnings.append("Bone '%s' has %d B-Bone segments; it deforms as a straight bone."
                                  % (b.name, b.bbone_segments))
        plans[arm.name] = p
    for p in plans.values():
        p.parent_ref = ('object', p.arm) if p.node else resolve_parent(p.arm, plans, scope_set)
    return plans


def rig_fcurves(arm):
    out = {}
    for fc in assigned_fcurves(arm):
        m = _POSE_PATH.match(fc.data_path)
        if m:
            bone = re.sub(r'\\(.)', r'\1', m.group(1))
            out.setdefault(bone, []).append(fc)
    return out


def collect_skin_data(context, plans, scope, warnings):
    """Normalized per-vertex weights from the evaluated mesh (armatures in REST pose),
    in the vertex order Blender's FBX exporter writes."""
    dg = context.evaluated_depsgraph_get()
    skins = {}
    for o in scope:
        if o.type != 'MESH':
            continue
        mods = [m for m in o.modifiers if m.type == 'ARMATURE' and m.object is not None
                and m.object.name in plans and plans[m.object.name].bones and m.show_viewport]
        if not mods:
            continue
        mod = mods[0]
        plan = plans[mod.object.name]
        if len(mods) > 1:
            warnings.append("'%s': only the first Armature modifier is converted." % o.name)
        if not mod.use_vertex_groups:
            warnings.append("'%s': Armature modifier does not use vertex groups; no skin." % o.name)
            continue
        if mod.use_bone_envelopes:
            warnings.append("'%s': bone envelopes are not converted, only vertex group weights." % o.name)
        if mod.use_deform_preserve_volume:
            warnings.append("'%s': 'Preserve Volume' is on; the FBX Skin is linear." % o.name)
        if mod.vertex_group:
            warnings.append("'%s': the Armature modifier vertex group mask is ignored." % o.name)
        deform = [b.name for b in plan.bones if b.use_deform]
        index = {n: i for i, n in enumerate(deform)}
        gnames = [g.name for g in o.vertex_groups]
        eo = o.evaluated_get(dg)
        me = eo.to_mesh(preserve_all_data_layers=True, depsgraph=dg)
        try:
            weights, static = [], 0
            for v in me.vertices:
                acc = {}
                for g in v.groups:
                    bi = index.get(gnames[g.group]) if g.group < len(gnames) else None
                    if bi is not None and g.weight > 0.0:
                        acc[bi] = acc.get(bi, 0.0) + g.weight
                tot = sum(acc.values())
                if tot <= 0.0001:
                    weights.append(())
                    static += 1
                else:
                    weights.append(tuple((bi, acc[bi] / tot) for bi in sorted(acc)))
            nverts = len(me.vertices)
        finally:
            eo.to_mesh_clear()
        skins[o.name] = {"armature": plan.name, "bones": deform, "vertices": nverts,
                         "static_vertices": static, "weights": weights}
    return skins


# ── Scene state: save -> strip for the geometry export -> restore ───────────

def scope_objects(context, scope):
    if scope == 'SELECTED':
        objs = list(context.selected_objects)
    else:
        objs = [o for o in context.view_layer.objects if o.visible_get()]
    return [o for o in objs if o.type in EXPORT_TYPES]


def _basis(o):
    return {
        "rotation_mode": o.rotation_mode,
        "location": tuple(o.location),
        "rotation_euler": tuple(o.rotation_euler),
        "rotation_quaternion": tuple(o.rotation_quaternion),
        "rotation_axis_angle": tuple(o.rotation_axis_angle),
        "scale": tuple(o.scale),
    }


def _set_basis(o, b):
    o.rotation_mode = b["rotation_mode"]
    o.location = b["location"]
    o.rotation_euler = b["rotation_euler"]
    o.rotation_quaternion = b["rotation_quaternion"]
    o.rotation_axis_angle = b["rotation_axis_angle"]
    o.scale = b["scale"]


def save_state(context, scope):
    scene, vl = context.scene, context.view_layer
    st = {"frame": scene.frame_current, "subframe": scene.frame_subframe,
          "active": vl.objects.active, "selected": [o for o in vl.objects if o.select_get()],
          "scope": list(scope), "parents": {}, "basis": {}, "anim": {}, "constraints": []}
    for o in scope:
        st["parents"][o] = (o.parent, o.parent_type, o.parent_bone,
                            tuple(o.parent_vertices), o.matrix_parent_inverse.copy())
    for o in scene.objects:
        ad = o.animation_data
        if o in st["parents"] or (ad is not None and ad.action is not None):
            st["basis"][o] = _basis(o)
        for c in o.constraints:
            st["constraints"].append((c, c.mute))
        if o.pose is not None:
            for pb in o.pose.bones:
                for c in pb.constraints:
                    st["constraints"].append((c, c.mute))
    for o in scope:
        ad = o.animation_data
        if ad is None:
            continue
        act = ad.action
        st["anim"][o] = {"action": act,
                         "slot": getattr(ad, "action_slot", None) if act is not None else None,
                         "fake_user": act.use_fake_user if act is not None else None,
                         "use_nla": ad.use_nla,
                         "drivers": [(fc, fc.mute) for fc in ad.drivers]}
    return st


def strip_for_fbx(context, state, plans):
    """Mute constraints, detach animation, put converted armatures in REST pose and clear
    parents keeping transforms, so Blender's exporter writes every mesh in its own space."""
    state.setdefault("pose_position", [])
    for p in plans.values():
        data = p.arm.data
        if data.pose_position != 'REST' and all(d is not data for d, _pp in state["pose_position"]):
            state["pose_position"].append((data, data.pose_position))
            data.pose_position = 'REST'
    for c, _mute in state["constraints"]:
        c.mute = True
    for o, a in state["anim"].items():
        ad = o.animation_data
        for fc, _mute in a["drivers"]:
            fc.mute = True
        ad.use_nla = False
        if a["action"] is not None:
            a["action"].use_fake_user = True
            ad.action = None
    context.view_layer.update()
    scope = state["scope"]
    world = {o: o.matrix_world.copy() for o in scope}
    for o in scope:
        if o.parent is not None:
            o.parent = None
            o.matrix_world = world[o]
    context.view_layer.update()
    for o in context.view_layer.objects:
        if o.select_get():
            o.select_set(False)
    for o in scope:
        o.select_set(True)
    if scope:
        context.view_layer.objects.active = scope[0]


def restore_state(context, state):
    errors = []

    def guard(label, fn):
        try:
            fn()
        except Exception as exc:
            errors.append("%s: %s" % (label, exc))

    scene = context.scene
    for o, (par, ptype, pbone, pverts, pinv) in state["parents"].items():
        def _p(o=o, par=par, ptype=ptype, pbone=pbone, pverts=pverts, pinv=pinv):
            if o.parent != par:
                o.parent = par
            if par is not None:
                if o.parent_type != ptype:
                    o.parent_type = ptype
                if o.parent_bone != pbone:
                    o.parent_bone = pbone
                if tuple(o.parent_vertices) != pverts:
                    o.parent_vertices = pverts
            o.matrix_parent_inverse = pinv
        guard("parent " + o.name, _p)
    for o, a in state["anim"].items():
        def _a(o=o, a=a):
            ad = o.animation_data
            if a["action"] is not None:
                if ad.action != a["action"]:
                    ad.action = a["action"]
                slot = a["slot"]
                if slot is not None and hasattr(ad, "action_slot") and ad.action_slot != slot:
                    ad.action_slot = slot
                a["action"].use_fake_user = a["fake_user"]
            ad.use_nla = a["use_nla"]
            for fc, mute in a["drivers"]:
                fc.mute = mute
        guard("animation " + o.name, _a)
    for c, mute in state["constraints"]:
        guard("constraint " + c.name, lambda c=c, mute=mute: setattr(c, "mute", mute))
    for data, pp in state.get("pose_position", []):
        guard("pose position " + data.name, lambda data=data, pp=pp: setattr(data, "pose_position", pp))
    guard("frame", lambda: scene.frame_set(state["frame"], subframe=state["subframe"]))
    for o, b in state["basis"].items():
        guard("transform " + o.name, lambda o=o, b=b: _set_basis(o, b))

    def _sel():
        vl = context.view_layer
        for o in vl.objects:
            if o.select_get():
                o.select_set(False)
        for o in state["selected"]:
            if o.name in vl.objects:
                o.select_set(True)
        vl.objects.active = state["active"]
    guard("selection", _sel)
    guard("update", context.view_layer.update)
    return errors


def _ensure_object_mode(context):
    obj = context.view_layer.objects.active
    if obj is not None and obj.mode != 'OBJECT' and bpy.ops.object.mode_set.poll():
        prev = obj.mode
        bpy.ops.object.mode_set(mode='OBJECT')
        return obj, prev
    return None, None


def _restore_mode(context, obj, prev):
    if obj is None or prev is None:
        return
    try:
        if context.view_layer.objects.active is obj and bpy.ops.object.mode_set.poll():
            bpy.ops.object.mode_set(mode=prev)
    except Exception:
        pass


# ── Blender's own FBX exporter for the geometry ─────────────────────────────

GEOMETRY_FIXED = dict(
    use_selection=True, use_visible=False, use_active_collection=False,
    object_types={'EMPTY', 'MESH', 'OTHER'},
    global_scale=1.0, apply_scale_options='FBX_SCALE_NONE', apply_unit_scale=True,
    axis_forward='Y', axis_up='Z', use_space_transform=True, bake_space_transform=True,
    bake_anim=False, batch_mode='OFF',
)


def _fbx_operator_props():
    try:
        return set(bpy.ops.export_scene.fbx.get_rna_type().properties.keys())
    except Exception:
        pass
    try:
        import addon_utils
        addon_utils.enable("io_scene_fbx", default_set=True, persistent=True)
        return set(bpy.ops.export_scene.fbx.get_rna_type().properties.keys())
    except Exception:
        return None


def export_geometry(filepath, user_settings):
    valid = _fbx_operator_props()
    if valid is None:
        raise RuntimeError("Blender's FBX exporter (io_scene_fbx) is not available.")
    kwargs = {k: v for k, v in user_settings.items() if k in valid}
    kwargs.update({k: v for k, v in GEOMETRY_FIXED.items() if k in valid})
    kwargs["filepath"] = filepath
    if "check_existing" in valid:
        kwargs["check_existing"] = False
    result = bpy.ops.export_scene.fbx(**kwargs)
    if 'FINISHED' not in result:
        raise RuntimeError("Blender's FBX export did not finish: %s" % (result,))


# ═════════════════════════════════════════════════════════════════════════════
#  The 3ds Max node graph
# ═════════════════════════════════════════════════════════════════════════════

class XNode:
    """One node of the exported assembly (an object, a bone dummy or a skin helper)."""

    def __init__(self, name, kind):
        self.name = name
        self.kind = kind          # 'object', 'bone', 'helper'
        self.obj = None           # object, or armature for bones, or mesh for helpers
        self.bone = None          # bone name
        self.parent = None        # XNode or None
        self.order = 'XYZ'        # FBX rotation order (Euler)
        self.fbx_class = 'Null'
        self.uid = None
        self.static = None        # (t, r_deg, s) local values at the pose frame
        self.curves = {}          # 'T'/'R'/'S' -> [curve per axis] (see Curve)
        self.bake = False
        self.groups = {}          # 'T'/'R'/'S' -> sorted key frames
        self.fcurves = {}         # 'T'/'R'/'S' -> {axis index: fcurve}
        self.euler_src = None     # Blender Euler mode of the channel values (objects and pose bones)
        self.samples = {}         # frame -> local Matrix (Max space)


def build_graph(scope, plans, warnings):
    scope_set = set(scope)
    nodes, by_obj, by_bone = [], {}, {}
    for o in scope:
        plan = plans.get(o.name) if o.type == 'ARMATURE' else None
        if plan is not None and not plan.node:
            continue
        n = XNode(o.name, 'object')
        n.obj = o
        n.fbx_class = 'Mesh' if o.type in {'MESH', 'CURVE', 'SURFACE', 'FONT', 'META'} else 'Null'
        if o.rotation_mode in ORDER_ENUM:
            n.order = o.rotation_mode
            n.euler_src = o.rotation_mode
        nodes.append(n)
        by_obj[o] = n
    for p in plans.values():
        for b in p.bones:
            n = XNode(b.name, 'bone')
            n.obj = p.arm
            n.bone = b.name
            rmode = p.arm.pose.bones[b.name].rotation_mode
            if rmode in ORDER_ENUM:
                n.euler_src = rmode                    # dummies stay XYZ, as the bridge creates them
            nodes.append(n)
            by_bone[(p.name, b.name)] = n
    for n in nodes:
        if n.kind == 'object':
            ref = resolve_parent(n.obj, plans, scope_set)
            if ref is not None:
                n.parent = by_obj.get(ref[1]) if ref[0] == 'object' else by_bone.get(ref[1])
        else:
            p = plans[n.obj.name]
            b = n.obj.data.bones[n.bone]
            if b.parent is not None:
                n.parent = by_bone[(p.name, b.parent.name)]
            elif p.parent_ref is not None:
                kind, ref = p.parent_ref
                n.parent = by_obj.get(ref) if kind == 'object' else by_bone.get(ref)
    names = {}
    for n in nodes:
        names.setdefault(n.name, []).append(n)
    clash = sorted(k for k, v in names.items() if len(v) > 1)
    if clash:
        warnings.append("Several nodes share a name (bone and object?): " + ", ".join(clash[:5]))
    return nodes, by_obj, by_bone


def node_world(n, U, rest=False):
    """World matrix of a node in 3ds Max space (Z-up, centimetres), current frame."""
    if n.kind == 'object':
        m = n.obj.matrix_world.copy()
    elif n.kind == 'bone':
        arm = n.obj
        bm = arm.data.bones[n.bone].matrix_local if rest else arm.pose.bones[n.bone].matrix
        m = arm.matrix_world @ bm @ DUMMY_AXES
    else:
        m = n.obj.matrix_world.copy()
    m.translation = m.translation * U
    return m


def local_matrices(nodes, U, rest=False):
    w = {n: node_world(n, U, rest) for n in nodes}
    out = {}
    for n in nodes:
        out[n] = (w[n.parent].inverted_safe() @ w[n]) if n.parent is not None else w[n]
    return out, w


def decompose(m, order, compat=None):
    loc, q, sca = m.decompose()
    if compat is not None:
        e = q.to_euler(order, Euler(tuple(compat), order))
    else:
        e = q.to_euler(order)
    return loc, e, sca


def lcl_from_local(n, m, compat=None):
    """(translation cm, Euler radians in n.order, scale) as written in Lcl properties."""
    loc, e, sca = decompose(m, n.order, compat)
    if n.parent is None:
        loc = Vector((loc.x, loc.z, -loc.y))          # CONV applied to the translation
    return loc, e, sca


# ═════════════════════════════════════════════════════════════════════════════
#  Animation: key frames, sampling and FBX curves
# ═════════════════════════════════════════════════════════════════════════════

class Curve:
    """One FBX animation curve: times (frames), values, per-key flags and data."""

    def __init__(self):
        self.frames = []
        self.values = []
        self.flags = []
        self.data = []            # [right slope, next-left slope, right weight, next-left weight]


def _fcurve_needs_bake(fc):
    if fc.modifiers and any(not m.mute for m in fc.modifiers):
        return True
    if fc.extrapolation == 'LINEAR' and len(fc.keyframe_points) > 1:
        return True
    return False


def plan_animation(nodes, plans, scene, mode, warnings):
    """Fill n.groups (key frames per channel group) and n.fcurves; mark baked nodes."""
    f0, f1 = scene.frame_start, scene.frame_end
    all_frames = [float(f) for f in range(f0, f1 + 1)]
    rigs = {name: rig_fcurves(p.arm) for name, p in plans.items()}
    for n in nodes:
        groups = {"T": set(), "R": set(), "S": set()}
        fcs = {"T": {}, "R": {}, "S": {}}
        bake = mode == 'BAKE'
        if n.kind == 'object':
            o = n.obj
            for fc in assigned_fcurves(o):
                g = _OBJ_PATHS.get(fc.data_path)
                if g is None or fc.mute:
                    continue
                if fc.data_path.startswith("delta_") or _fcurve_needs_bake(fc):
                    bake = True
                if fc.data_path in ("location", "scale") or fc.data_path == "rotation_euler":
                    fcs[g][fc.array_index] = fc
                for kp in fc.keyframe_points:
                    groups[g].add(round(kp.co[0], 4))
            ad = o.animation_data
            if any(not c.mute for c in o.constraints):
                bake = True
            if ad is not None:
                if any(fc.data_path in _OBJ_PATHS and not fc.mute for fc in ad.drivers):
                    bake = True
                if ad.use_nla and any(len(t.strips) for t in ad.nla_tracks if not t.mute):
                    bake = True
        elif n.kind == 'bone':
            p = plans[n.obj.name]
            bake = bake or p.bake
            for fc in rigs[p.name].get(n.bone, []):
                m = _POSE_PATH.match(fc.data_path)
                g = _BONE_PATHS.get(m.group(2)) if m else None
                if g is None or fc.mute:
                    continue
                if _fcurve_needs_bake(fc):
                    bake = True
                if m.group(2) in ("location", "scale", "rotation_euler"):
                    fcs[g][fc.array_index] = fc
                for kp in fc.keyframe_points:
                    groups[g].add(round(kp.co[0], 4))
        n.bake = bake
        if bake:
            n.groups = {g: list(all_frames) for g in "TRS"}
        else:
            n.groups = {g: sorted(v) for g, v in groups.items() if v}
        n.fcurves = fcs


def sample_frames(context, nodes, U, frames_needed):
    """Evaluate Blender at every needed frame; store local matrices per node."""
    scene = context.scene
    for f in sorted(frames_needed):
        fi = math.floor(f)
        scene.frame_set(int(fi), subframe=float(f - fi))
        need = frames_needed[f]
        loc, _w = local_matrices(need, U)
        for n in need:
            if n in loc:
                n.samples[f] = loc[n]


def _channel_values(n, frame):
    """Blender channel values (location, Euler rotation, scale) of an object or pose bone at a
    frame, from its F-Curves; unkeyed channels keep their current property values."""
    if n.kind == 'object':
        src = n.obj
    else:
        src = n.obj.pose.bones[n.bone]
    vals = {"T": list(src.location), "R": list(src.rotation_euler), "S": list(src.scale)}
    for g, d in n.fcurves.items():
        for i, fc in d.items():
            vals[g][i] = fc.evaluate(frame)
    return vals


def _rot_matrix(order, e):
    return Euler(e, order).to_matrix()


def _same_rotation(a, b, eps_deg=5e-4):
    d = a.transposed() @ b
    s = math.sqrt((d[2][1] - d[1][2]) ** 2 + (d[0][2] - d[2][0]) ** 2 + (d[1][0] - d[0][1]) ** 2) / 2.0
    c = (d[0][0] + d[1][1] + d[2][2] - 1.0) / 2.0
    return abs(math.degrees(math.atan2(s, c))) < eps_deg


class RotationMap:
    """FBX Euler channel a = offset[a] + sign[a] * Blender rotation channel src[a] (radians)."""

    def __init__(self, src, sign, offset):
        self.src = src
        self.sign = sign
        self.offset = offset


# Bone axes -> dummy axes (DUMMY_AXES): X -> X, Y -> -Z, Z -> Y.
_DUMMY_SRC = (0, 2, 1)               # dummy channel a comes from bone channel _DUMMY_SRC[a]
_DUMMY_ROT_SIGN = (1.0, 1.0, -1.0)   # dummy Z = -bone Y


def find_rotation_map(n, pose_frame):
    """RotationMap of the node's Euler channels, verified at every sampled frame, or None.
    It keeps Blender's own angle values (whole turns survive between keys, as the bridge
    transfers them). The canonical Euler solution (what 3ds Max holds after linking) is
    preferred, then the branch nearest to Blender's channel values."""
    if not n.samples or n.euler_src is None or n.kind not in ('object', 'bone'):
        return None
    if n.kind == 'object':
        src_r, sign_r = (0, 1, 2), (1.0, 1.0, 1.0)
    else:
        src_r, sign_r = _DUMMY_SRC, _DUMMY_ROT_SIGN
    pose_f = pose_frame if pose_frame in n.samples else min(n.samples)
    ref = n.samples[pose_f]
    mapped = {}
    for f in n.samples:
        ch = _channel_values(n, f)["R"]
        mapped[f] = Vector([sign_r[a] * ch[src_r[a]] for a in range(3)])
    mapped0 = mapped[pose_f]
    for compat in (None, mapped0):
        _l, e0, _s = lcl_from_local(n, ref, compat)
        off = [e0[a] - mapped0[a] for a in range(3)]
        if all(_same_rotation(_rot_matrix(n.order, [mapped[f][a] + off[a] for a in range(3)]),
                              m.to_3x3().normalized()) for f, m in n.samples.items()):
            return RotationMap(list(src_r), list(sign_r), off)
    return None


def mapped_rotation(rm, ch):
    """FBX Euler values (radians) from Blender channel values through a RotationMap."""
    return Vector([rm.offset[a] + rm.sign[a] * ch["R"][rm.src[a]] for a in range(3)])


def _max_auto_slopes(t, v):
    """3ds Max / FBX auto tangents: Catmull-Rom, zero at extremes, clamped so the segment
    cannot overshoot its neighbours ('generic clamp progressive'). Units: value per frame."""
    n = len(t)
    s = [0.0] * n
    for i in range(1, n - 1):
        dl, dr = v[i] - v[i - 1], v[i + 1] - v[i]
        if dl * dr <= 0.0:
            continue
        cr = (v[i + 1] - v[i - 1]) / (t[i + 1] - t[i - 1])
        lim = 3.0 * min(abs(dl) / (t[i] - t[i - 1]), abs(dr) / (t[i + 1] - t[i]))
        s[i] = math.copysign(min(abs(cr), lim), cr)
    return s


def curves_max_style(frames, values_per_axis, hints=None, is_rotation=False, fps=30.0):
    """3ds Max FBX keys: cubic auto (objects), or per-key hints for dummies (flat/linear/step/auto)."""
    curves = []
    for vals in values_per_axis:
        c = Curve()
        c.frames = list(frames)
        c.values = list(vals)
        slopes = _max_auto_slopes(c.frames, c.values)
        nk = len(frames)
        for i in range(nk):
            hint = hints[i] if hints else "auto"
            last = i == nk - 1
            if hint == "step":
                flag = INTERP_CONSTANT
            elif hint == "linear":
                flag = INTERP_LINEAR
            elif hint == "flat":
                flag = MAX_USER | WEIGHTED_RIGHT | (0 if last else WEIGHTED_NEXT_LEFT)
            else:
                flag = MAX_AUTO | WEIGHTED_RIGHT | (0 if last else WEIGHTED_NEXT_LEFT)
            rs = slopes[i] * fps if hint == "auto" else 0.0
            nl = (slopes[i + 1] * fps if (not last and (hints[i + 1] if hints else "auto") == "auto") else 0.0)
            if is_rotation:                     # 3ds Max stores rotation slopes in radians
                rs, nl = math.radians(rs), math.radians(nl)
            c.flags.append(flag)
            c.data.append((rs, nl, 0.3332, 0.3332))
        curves.append(c)
    return curves


def curves_sampled(frames, values_per_axis):
    curves = []
    for vals in values_per_axis:
        c = Curve()
        c.frames = list(frames)
        c.values = list(vals)
        c.flags = [INTERP_LINEAR] * len(frames)
        c.data = [(0.0, 0.0, 1.0 / 3.0, 1.0 / 3.0)] * len(frames)
        curves.append(c)
    return curves


def _bone_hints(fcs, frames):
    """Per-key tangent hints for dummies, as the bridge sets them in 3ds Max."""
    out = []
    for f in frames:
        hs_in, hs_out = set(), set()
        for fc in fcs:
            kps = fc.keyframe_points
            for i, kp in enumerate(kps):
                if abs(kp.co[0] - f) > 1e-4:
                    continue
                for side in ("in", "out"):
                    if side == "out":
                        if i == len(kps) - 1:
                            continue
                        mode, h = kp.interpolation, kp.handle_right
                    else:
                        if i == 0:
                            continue
                        mode, h = kps[i - 1].interpolation, kp.handle_left
                    if mode == 'CONSTANT':
                        hint = "step"
                    elif mode == 'LINEAR':
                        hint = "linear"
                    elif mode == 'BEZIER' and abs(h[1] - kp.co[1]) <= 1e-6 * max(1.0, abs(kp.co[1])):
                        hint = "flat"
                    else:
                        hint = "auto"
                    (hs_out if side == "out" else hs_in).add(hint)
        hs = hs_out or hs_in
        out.append(hs.pop() if len(hs) == 1 else "auto")
    return out


STATIC_EPS = {"T": 1e-4, "R": 1e-4, "S": 1e-6}    # cm, degrees, factor


def _axis_moves(seq, static, eps):
    lo, hi = min(seq), max(seq)
    return hi - lo > eps * max(1.0, abs(hi)) or abs(seq[0] - static) > eps * max(1.0, abs(static))


def build_curves(n, fps, rot_map, rig_fc=None, only_animated=True):
    """n.curves['T'|'R'|'S'] = [Curve or None per axis].
    Exact Keys: keys on Blender's key frames only, exact values there, 3ds Max auto tangents
    (bone dummies: tangent types from Blender's interpolation, as the bridge sets them).
    Baked nodes (Bake Every Frame, constraints, drivers, NLA, ...): a linear key on every frame.
    only_animated: channels whose value never changes get no curve (the node keeps their exact
    static value)."""
    n.curves = {}
    vals = {}
    prev = Vector([math.radians(x) for x in n.static[1]])
    for f in sorted(n.samples):
        m = n.samples[f]
        if rot_map is not None:
            e = mapped_rotation(rot_map, _channel_values(n, f))    # Blender's own values: turns kept
            loc, _e, sca = lcl_from_local(n, m, e)
        else:
            loc, e, sca = lcl_from_local(n, m, prev)
        prev = Vector(e)
        vals[f] = (Vector(loc), Vector(e), Vector(sca))
    gi = {"T": 0, "R": 1, "S": 2}
    for g, keyframes in n.groups.items():
        frames = [f for f in keyframes if f in vals]
        if not frames:
            continue
        per_axis = []
        for a in range(3):
            seq = [vals[f][gi[g]][a] for f in frames]
            if g == "R":
                seq = [math.degrees(x) for x in seq]
            per_axis.append(seq)
        static = n.static[gi[g]]
        moves = [_axis_moves(per_axis[a], static[a], STATIC_EPS[g]) for a in range(3)]
        if not any(moves) and (only_animated or n.bake):
            continue
        if n.bake:
            n.curves[g] = curves_sampled(frames, per_axis)
        else:
            hints = _bone_hints(rig_fc.get(g, []), frames) if n.kind == 'bone' else None
            n.curves[g] = curves_max_style(frames, per_axis, hints, is_rotation=(g == "R"), fps=fps)
        if only_animated or n.bake:
            n.curves[g] = [c if moves[a] else None for a, c in enumerate(n.curves[g])]


# ═════════════════════════════════════════════════════════════════════════════
#  Assembly of the final file
# ═════════════════════════════════════════════════════════════════════════════

TEMPLATES = {
    "AnimationStack": ("FbxAnimStack", [
        ("Description", "KString", "", "", [""], "S"),
        ("LocalStart", "KTime", "Time", "", [0], "L"), ("LocalStop", "KTime", "Time", "", [0], "L"),
        ("ReferenceStart", "KTime", "Time", "", [0], "L"), ("ReferenceStop", "KTime", "Time", "", [0], "L")]),
    "AnimationLayer": ("FbxAnimLayer", [
        ("Weight", "Number", "", "A", [100.0], "D"), ("Mute", "bool", "", "", [0], "I"),
        ("Solo", "bool", "", "", [0], "I"), ("Lock", "bool", "", "", [0], "I"),
        ("Color", "ColorRGB", "Color", "", [0.8, 0.8, 0.8], "DDD"),
        ("BlendMode", "enum", "", "", [0], "I"), ("RotationAccumulationMode", "enum", "", "", [0], "I"),
        ("ScaleAccumulationMode", "enum", "", "", [0], "I"),
        ("BlendModeBypass", "ULongLong", "", "", [0], "L")]),
    "AnimationCurveNode": ("FbxAnimCurveNode", [("d", "Compound", "", "", [], "")]),
    "AnimationCurve": (None, []),
    "Deformer": (None, []),
}


class UidPool:
    def __init__(self, used):
        self.used = set(used)
        self.next = 2000000000

    def new(self):
        while self.next in self.used:
            self.next += 1
        self.used.add(self.next)
        return self.next


def _name_class(name, cls):
    return _b(name) + b"\x00\x01" + _b(cls)


def _split_name(raw):
    if b"\x00\x01" in raw:
        a, b = raw.split(b"\x00\x01", 1)
        return a.decode("utf-8", "replace"), b.decode("utf-8", "replace")
    return raw.decode("utf-8", "replace"), ""


def _pack_weights(wr, wl):
    lo = max(0, min(65535, int(round(wr * 9999.0))))
    hi = max(0, min(65535, int(round(wl * 9999.0))))
    return struct.unpack("<f", struct.pack("<I", lo | (hi << 16)))[0]


def _time_mode(fps):
    for k, v in FPS_ENUM.items():
        if abs(fps - k) < 1e-6:
            return v
    return 14   # custom


def assemble(root, nodes, skins, plans, U, opts, warnings, rest_world, pose_world):
    objects = root.find("Objects")
    conns = root.find("Connections")
    used = [e.props[0] for e in objects.elems if e.props and isinstance(e.props[0], int)]
    uids = UidPool(used)

    models = {}
    geometry_of = {}
    for e in objects.elems:
        if e.id == b"Model":
            nm, _cls = _split_name(e.props[1])
            models[nm] = e
    model_by_uid = {e.props[0]: e for e in models.values()}
    for c in conns.elems:
        if c.id == b"C" and c.props[0] == b"OO":
            src, dst = c.props[1], c.props[2]
            if dst in model_by_uid:
                for e in objects.elems:
                    if e.props and e.props[0] == src and e.id == b"Geometry":
                        geometry_of[_split_name(model_by_uid[dst].props[1])[0]] = src

    fps = opts["fps"]

    # ── Models: existing ones (from Blender's exporter) and new dummies ──────
    for n in nodes:
        if n.name in models:
            m = models[n.name]
            n.uid = m.props[0]
        else:
            n.uid = uids.new()
            attr_uid = uids.new()
            na = objects.add("NodeAttribute", [attr_uid, _name_class(n.name, "NodeAttribute"), "Null"], "LSS")
            na.add("TypeFlags", ["Null"], "S")
            m = objects.add("Model", [n.uid, _name_class(n.name, "Model"), "Null"], "LSS")
            m.add("Version", [232], "I")
            m.add("Properties70")
            m.add("MultiLayer", [0], "I")
            m.add("MultiTake", [0], "I")
            m.add("Shading", [b"\x01"], "C")
            m.add("Culling", ["CullingOff"], "S")
            conns.add("C", ["OO", attr_uid, n.uid], "SLL")
            models[n.name] = m
        props = m.find("Properties70") or m.add("Properties70")
        p70_remove(props, ["Lcl Translation", "Lcl Rotation", "Lcl Scaling", "PreRotation", "RotationActive",
                           "RotationOrder", "InheritType", "DefaultAttributeIndex", "ScalingMax"])
        keep = props.elems
        props.elems = []
        t, r, s = n.static
        root_node = n.parent is None
        if root_node:
            p_vec(props, "PreRotation", (-90.0, 0.0, 0.0))
        if ORDER_ENUM[n.order]:
            p_int(props, "RotationOrder", "enum", "", ORDER_ENUM[n.order])
        if root_node or ORDER_ENUM[n.order]:
            p_int(props, "RotationActive", "bool", "", 1)
        p_int(props, "InheritType", "enum", "", 1)
        p_vec(props, "ScalingMax", (0.0, 0.0, 0.0))
        p_int(props, "DefaultAttributeIndex", "int", "Integer", 0)
        an = n.curves
        if any(abs(x) > 1e-12 for x in t) or "T" in an:
            p_lcl(props, "Lcl Translation", t, "T" in an)
        if any(abs(x) > 1e-12 for x in r) or "R" in an:
            p_lcl(props, "Lcl Rotation", r, "R" in an)
        if any(abs(x - 1.0) > 1e-12 for x in s) or "S" in an:
            p_lcl(props, "Lcl Scaling", s, "S" in an)
        props.elems.extend(keep)

    # ── Hierarchy ────────────────────────────────────────────────────────────
    node_uids = {n.uid for n in nodes}
    conns.elems = [c for c in conns.elems
                   if not (c.id == b"C" and c.props[0] == b"OO" and c.props[1] in node_uids
                           and (c.props[2] == 0 or c.props[2] in node_uids))]
    for n in nodes:
        conns.add("C", ["OO", n.uid, n.parent.uid if n.parent is not None else 0], "SLL")

    # ── Skin ─────────────────────────────────────────────────────────────────
    bind = {}                               # node -> bind world matrix (Y-up, cm), see Bind pose
    for mesh_name, sk in skins.items():
        geo = geometry_of.get(mesh_name)
        if geo is None:
            warnings.append("'%s': no geometry found for its Skin." % mesh_name)
            continue
        mesh_node = next((n for n in nodes if n.kind == 'object' and n.name == mesh_name), None)
        mesh_world = CONV @ pose_world[mesh_node]
        bind[mesh_node] = mesh_world
        skin_uid = uids.new()
        nv = sk["vertices"]
        sd = objects.add("Deformer", [skin_uid, _name_class("", "Deformer"), "Skin"], "LSS")
        sd.add("Version", [101], "I")
        sd.add("Link_DeformAcuracy", [50.0], "D")
        sd.add("SkinningType", ["Blend"], "S")
        sd.add("Indexes", [np.arange(nv, dtype=np.int32)], "i")
        sd.add("BlendWeights", [np.zeros(nv, dtype=np.float64)], "d")
        conns.add("C", ["OO", skin_uid, geo], "SLL")
        per_bone = {i: ([], []) for i in range(len(sk["bones"]))}
        static_idx = []
        for vi, row in enumerate(sk["weights"]):
            if not row:
                static_idx.append(vi)
            for bi, w in row:
                per_bone[bi][0].append(vi)
                per_bone[bi][1].append(w)
        targets = []
        for bi, bname in enumerate(sk["bones"]):
            bn = next((n for n in nodes if n.kind == 'bone' and n.bone == bname and n.obj.name == sk["armature"]),
                      None)
            if bn is not None and per_bone[bi][0]:
                targets.append((bn, per_bone[bi]))
        if static_idx:
            hn = XNode(mesh_name + "__SkinStatic", 'helper')
            hn.obj = mesh_node.obj
            hn.parent = mesh_node
            hn.uid = uids.new()
            attr_uid = uids.new()
            na = objects.add("NodeAttribute", [attr_uid, _name_class(hn.name, "NodeAttribute"), "Null"], "LSS")
            na.add("TypeFlags", ["Null"], "S")
            hm = objects.add("Model", [hn.uid, _name_class(hn.name, "Model"), "Null"], "LSS")
            hm.add("Version", [232], "I")
            hp = hm.add("Properties70")
            p_int(hp, "InheritType", "enum", "", 1)
            p_int(hp, "DefaultAttributeIndex", "int", "Integer", 0)
            hm.add("Shading", [b"\x01"], "C")
            hm.add("Culling", ["CullingOff"], "S")
            conns.add("C", ["OO", attr_uid, hn.uid], "SLL")
            conns.add("C", ["OO", hn.uid, mesh_node.uid], "SLL")
            rest_world[hn] = pose_world[mesh_node]
            targets.append((hn, (static_idx, [1.0] * len(static_idx))))
        for bn, (idx, wts) in targets:
            link = CONV @ rest_world[bn]
            transform = link.inverted_safe() @ mesh_world
            cl_uid = uids.new()
            cl = objects.add("Deformer", [cl_uid, _name_class("", "SubDeformer"), "Cluster"], "LSS")
            cl.add("Version", [100], "I")
            cl.add("UserData", ["", ""], "SS")
            cl.add("Indexes", [np.asarray(idx, dtype=np.int32)], "i")
            cl.add("Weights", [np.asarray(wts, dtype=np.float64)], "d")
            cl.add("Transform", [np.array([transform[r][c] for c in range(4) for r in range(4)])], "d")
            cl.add("TransformLink", [np.array([link[r][c] for c in range(4) for r in range(4)])], "d")
            conns.add("C", ["OO", cl_uid, skin_uid], "SLL")
            conns.add("C", ["OO", bn.uid, cl_uid], "SLL")
            bind[bn] = link

    # ── Bind pose ────────────────────────────────────────────────────────────
    # One BindPose named as 3ds Max names it: the skinned meshes, every cluster link and all their
    # parents. 3ds Max's importer takes the Skin's initial pose from it; without it, it warns
    # "The Initial position (or Initial Pose) of the skinned objects is missing", and it rejects a
    # pose that lacks a parent ("Incomplete BindPose [nodes parents]"). Link matrices equal the
    # clusters' TransformLink and mesh matrices TransformLink x Transform: objects at the pose frame,
    # bone dummies in their rest pose. A Pose has no connections.
    if bind:
        for x0 in list(bind):
            x = x0.parent
            while x is not None:
                if x not in bind:
                    bind[x] = CONV @ (rest_world[x] if x.kind == 'bone' else pose_world[x])
                x = x.parent
        node_set = set(nodes)
        order = [n for n in nodes if n in bind] + [x for x in bind if x not in node_set]
        pose = objects.add("Pose", [uids.new(), _name_class("BIND_POSES", "Pose"), "BindPose"], "LSS")
        pose.add("Type", ["BindPose"], "S")
        pose.add("Version", [100], "I")
        pose.add("NbPoseNodes", [len(order)], "I")
        for x in order:
            m = bind[x]
            pn = pose.add("PoseNode")
            pn.add("Node", [x.uid], "L")
            pn.add("Matrix", [np.array([m[r][c] for c in range(4) for r in range(4)])], "d")

    # ── Animation ────────────────────────────────────────────────────────────
    start, stop = opts["range"]
    t0, t1 = int(round(start * KTIME / fps)), int(round(stop * KTIME / fps))
    animated = [n for n in nodes if n.curves]
    stack_uid = layer_uid = None
    if animated:
        stack_uid, layer_uid = uids.new(), uids.new()
        st = objects.add("AnimationStack", [stack_uid, _name_class("Take 001", "AnimStack"), ""], "LSS")
        sp = st.add("Properties70")
        p_time(sp, "LocalStart", t0)
        p_time(sp, "LocalStop", t1)
        p_time(sp, "ReferenceStart", t0)
        p_time(sp, "ReferenceStop", t1)
        objects.add("AnimationLayer", [layer_uid, _name_class("BaseLayer", "AnimLayer"), ""], "LSS")
        conns.add("C", ["OO", layer_uid, stack_uid], "SLL")
        prop_names = {"T": "Lcl Translation", "R": "Lcl Rotation", "S": "Lcl Scaling"}
        for n in animated:
            for g in ("T", "R", "S"):
                if g not in n.curves:
                    continue
                curves = n.curves[g]
                cn_uid = uids.new()
                cn = objects.add("AnimationCurveNode", [cn_uid, _name_class(g, "AnimCurveNode"), ""], "LSS")
                cp = cn.add("Properties70")
                static = n.static[{"T": 0, "R": 1, "S": 2}[g]]
                for a, ax in enumerate(AXES):
                    cp.add("P", ["d|" + ax, "Number", "", "A", float(static[a])], "SSSSD")
                conns.add("C", ["OO", cn_uid, layer_uid], "SLL")
                conns.add("C", ["OP", cn_uid, n.uid, prop_names[g]], "SLLS")
                for a, c in enumerate(curves):
                    if c is None:
                        continue
                    cu = uids.new()
                    ce = objects.add("AnimationCurve", [cu, _name_class("", "AnimCurve"), ""], "LSS")
                    ce.add("Default", [float(c.values[0]) if c.values else 0.0], "D")
                    ce.add("KeyVer", [4009], "I")
                    times = np.array([int(round(f * KTIME / fps)) for f in c.frames], dtype=np.int64)
                    ce.add("KeyTime", [times], "l")
                    ce.add("KeyValueFloat", [np.asarray(c.values, dtype=np.float32)], "f")
                    ce.add("KeyAttrFlags", [np.asarray(c.flags, dtype=np.int32)], "i")
                    data = []
                    for rs, nl, wr, wl in c.data:
                        data += [rs, nl, _pack_weights(wr, wl), 0.0]
                    ce.add("KeyAttrDataFloat", [np.asarray(data, dtype=np.float32)], "f")
                    ce.add("KeyAttrRefCount", [np.ones(len(c.frames), dtype=np.int32)], "i")
                    conns.add("C", ["OP", cu, cn_uid, "d|" + AXES[a]], "SLLS")

    # ── Global settings, definitions, takes ─────────────────────────────────
    gs = root.find("GlobalSettings").find("Properties70")
    for name, val in (("UpAxis", 1), ("UpAxisSign", 1), ("FrontAxis", 2), ("FrontAxisSign", 1),
                      ("CoordAxis", 0), ("CoordAxisSign", 1), ("OriginalUpAxis", 2), ("OriginalUpAxisSign", 1)):
        p70_set(gs, name, "int", "Integer", "", [val], "I")
    p70_set(gs, "UnitScaleFactor", "double", "Number", "", [1.0], "D")
    p70_set(gs, "OriginalUnitScaleFactor", "double", "Number", "", [1.0], "D")
    tm = _time_mode(fps)
    p70_set(gs, "TimeMode", "enum", "", "", [tm], "I")
    p70_set(gs, "TimeSpanStart", "KTime", "Time", "", [t0], "L")
    p70_set(gs, "TimeSpanStop", "KTime", "Time", "", [t1], "L")
    p70_set(gs, "CustomFrameRate", "double", "Number", "", [fps if tm == 14 else -1.0], "D")

    counts = {}
    for e in objects.elems:
        counts[e.id.decode()] = counts.get(e.id.decode(), 0) + 1
    defs = root.find("Definitions")
    have = {}
    for ot in defs.find_all("ObjectType"):
        have[ot.props[0].decode()] = ot
    for typ, cnt in counts.items():
        if typ in have:
            have[typ].find("Count").props[0] = cnt
        else:
            ot = defs.add("ObjectType", [typ], "S")
            ot.add("Count", [cnt], "I")
            tmpl, plist = TEMPLATES.get(typ, (None, []))
            if tmpl:
                pt = ot.add("PropertyTemplate", [tmpl], "S")
                pp = pt.add("Properties70")
                for name, ptype, label, flags, vals, vt in plist:
                    pp.add("P", [name, ptype, label, flags] + vals, "SSSS" + vt)
    for typ, ot in have.items():
        if typ != "GlobalSettings" and typ not in counts:
            defs.elems.remove(ot)
    total = 1 + sum(counts.values())
    defs.find("Count").props[0] = total

    takes = root.find("Takes")
    if takes is None:
        takes = root.add("Takes")
    takes.elems = []
    takes.add("Current", [""], "S")
    if animated:
        tk = takes.add("Take", ["Take 001"], "S")
        tk.add("FileName", ["Take_001.tak"], "S")
        tk.add("LocalTime", [t0, t1], "LL")
        tk.add("ReferenceTime", [t0, t1], "LL")
    return len(animated)


# ═════════════════════════════════════════════════════════════════════════════
#  Export
# ═════════════════════════════════════════════════════════════════════════════

def export_fbx_advanced(context, filepath, opts):
    """opts: scope, pose_frame, animation (bool), fps (None = scene), curves ('EXACT'|'BAKE'),
    only_animated, geometry settings. Without animation the file is the same, minus the keys and
    the take: static values are computed exactly as for the animated export."""
    t_start = time.perf_counter()
    scene = context.scene
    warnings = []
    U = unit_to_cm(scene)
    scope = scope_objects(context, opts["scope"])
    if not scope:
        raise RuntimeError("Nothing to export.")
    for o in scope:
        ad = o.animation_data
        if ad is not None and ad.use_tweak_mode:
            raise RuntimeError("'%s' is in NLA tweak mode. Exit tweak mode and try again." % o.name)
    fps = opts.get("fps") or (scene.render.fps / (scene.render.fps_base or 1.0))
    pose_frame = opts["pose_frame"]
    mode = 'BAKE' if opts.get("curves") == 'BAKE' else 'EXACT'
    use_anim = opts.get("animation", True)

    mode_obj, mode_prev = _ensure_object_mode(context)
    before = {o: o.matrix_world.copy() for o in scope}
    state = save_state(context, scope)
    tmp_dir = tempfile.mkdtemp(prefix="fbxadvanced_")
    tmp = os.path.join(tmp_dir, "geometry.fbx")
    errors = []
    try:
        scene.frame_set(pose_frame)
        plans = plan_rigs(scope, bake_all=(mode == 'BAKE'))
        for p in plans.values():
            warnings += p.warnings
        nodes, by_obj, _by_bone = build_graph(scope, plans, warnings)

        # static values at the pose frame, bind (rest) matrices for the skin
        loc_pose, w_pose = local_matrices(nodes, U)
        _loc_rest, w_rest = local_matrices(nodes, U, rest=True)
        plan_animation(nodes, plans, scene, mode, warnings)
        frames_needed = {}                      # frame -> nodes to evaluate (with their parents)
        for n in nodes:
            fr = set()
            for frames in n.groups.values():
                fr.update(frames)
            if not fr:
                continue
            fr.add(float(pose_frame))
            chain = []
            x = n
            while x is not None:
                chain.append(x)
                x = x.parent
            for f in fr:
                frames_needed.setdefault(f, set()).update(chain)
        sample_frames(context, nodes, U, frames_needed)
        scene.frame_set(pose_frame)

        rot_maps = {}
        for n in nodes:
            rm = find_rotation_map(n, float(pose_frame))
            rot_maps[n] = rm
            t, e, s = lcl_from_local(n, loc_pose[n])      # canonical Euler, as 3ds Max holds it
            if rm is not None:
                e = mapped_rotation(rm, _channel_values(n, float(pose_frame)))
            n.static = (tuple(t), tuple(math.degrees(x) for x in e), tuple(s))
        rigs = {name: rig_fcurves(p.arm) for name, p in plans.items()}
        for n in nodes:
            if not use_anim or not n.samples:
                continue                        # no curves: the node keeps its static values
            rig_fc = None
            if n.kind == 'bone':
                rig_fc = {"T": [], "R": [], "S": []}
                for fc in rigs[n.obj.name].get(n.bone, []):
                    m = _POSE_PATH.match(fc.data_path)
                    g = _BONE_PATHS.get(m.group(2)) if m else None
                    if g:
                        rig_fc[g].append(fc)
            build_curves(n, fps, rot_maps[n], rig_fc, opts.get("only_animated", True))

        # geometry through Blender's own exporter
        scene.frame_set(pose_frame)
        strip_for_fbx(context, state, plans)
        skins = collect_skin_data(context, plans, scope, warnings)
        export_geometry(tmp, opts.get("geometry", {}))
    finally:
        errors = restore_state(context, state)
        _restore_mode(context, mode_obj, mode_prev)

    worst = 0.0
    for o, m in before.items():
        m2 = o.matrix_world
        worst = max(worst, max(abs(m[r][c] - m2[r][c]) for r in range(4) for c in range(4)))
    if errors:
        warnings.append("Scene restore problems: " + "; ".join(errors[:3]))
    if worst > 1e-4:
        warnings.append("Objects moved by up to %.6f after restoring the scene." % worst)

    root, _ver = fbx_read(tmp)
    known = {n.name for n in nodes}
    extra = [_split_name(e.props[1])[0] for e in root.find("Objects").elems
             if e.id == b"Model" and _split_name(e.props[1])[0] not in known]
    if extra:
        warnings.append("Not converted (e.g. collection instances), left in Blender's layout: "
                        + ", ".join(extra[:5]))
    key_frames = [f for n in nodes for frames in n.groups.values() for f in frames]
    start = min([scene.frame_start, pose_frame] + key_frames)
    stop = max([scene.frame_end] + key_frames)
    opts2 = dict(opts, fps=fps, range=(start, stop))
    animated = assemble(root, nodes, skins, plans, U, opts2, warnings, w_rest, w_pose)
    fbx_write(filepath, root, FBX_VERSION)
    try:
        os.remove(tmp)
        os.rmdir(tmp_dir)
    except OSError:
        pass
    return {"nodes": len(nodes), "animated": animated, "dummies": sum(1 for n in nodes if n.kind == 'bone'),
            "skins": len(skins), "warnings": warnings, "seconds": time.perf_counter() - t_start}


# ═════════════════════════════════════════════════════════════════════════════
#  UI
# ═════════════════════════════════════════════════════════════════════════════

class EXPORT_SCENE_OT_fbx_advanced(bpy.types.Operator, ExportHelper):
    """Export the asset assembly to FBX the way 3ds Max writes it"""
    bl_idname = "export_scene.fbx_advanced"
    bl_label = "Export FBX Advanced"
    bl_options = {'PRESET'}

    filename_ext = ".fbx"
    filter_glob: StringProperty(default="*.fbx", options={'HIDDEN'})

    scope: EnumProperty(name="Objects", items=(('VISIBLE', "All Visible", "All objects visible in the view layer"),
                                               ('SELECTED', "Selected", "Selected objects only")),
                        default='VISIBLE')
    pose_frame: IntProperty(name="Pose Frame", default=0,
                            description="Frame of the static pose (the 3ds Max import frame). "
                                        "Without animation, the assembly is written in this pose")
    use_animation: BoolProperty(name="Animation", default=True,
                                description="Write the animation. Off: the same assembly in the pose of "
                                            "Pose Frame, with no keys and no take")
    curves: EnumProperty(
        name="Keys",
        items=(('EXACT', "Exact Keys", "Keys only on Blender's key frames, with 3ds Max auto tangents, "
                                       "as in files exported from 3ds Max after the bridge"),
               ('BAKE', "Bake Every Frame", "One key per frame on every animated node")),
        default='EXACT')
    fps_mode: EnumProperty(name="Frame Rate",
                           items=(('SCENE', "Scene", "Blender's scene frame rate"),
                                  ('CUSTOM', "Custom", "Keep frame numbers, write this frame rate "
                                                       "(3ds Max files from the bridge use 30)")),
                           default='CUSTOM')
    fps_custom: FloatProperty(name="FPS", default=30.0, min=1.0, max=1000.0,
                              description="Frame rate written to the file. Frame numbers are kept")
    only_animated: BoolProperty(name="Only Animated Channels", default=True,
                                description="Channels whose value never changes get no keys; "
                                            "their exact value is kept in the node")

    # Blender's own exporter settings for the data it writes
    use_mesh_modifiers: BoolProperty(name="Apply Modifiers", default=True)
    mesh_smooth_type: EnumProperty(name="Smoothing", items=(('OFF', "Normals Only", ""), ('FACE', "Face", ""),
                                                            ('EDGE', "Edge", "")), default='OFF')
    use_tspace: BoolProperty(name="Tangent Space", default=False)
    use_triangles: BoolProperty(name="Triangulate Faces", default=False)
    use_custom_props: BoolProperty(name="Custom Properties", default=False)
    path_mode: EnumProperty(name="Path Mode", items=(('AUTO', "Auto", ""), ('ABSOLUTE', "Absolute", ""),
                                                     ('RELATIVE', "Relative", ""), ('MATCH', "Match", ""),
                                                     ('STRIP', "Strip Path", ""), ('COPY', "Copy", "")),
                            default='AUTO')
    embed_textures: BoolProperty(name="Embed Textures", default=False)

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False
        col = layout.column()
        col.prop(self, "scope")
        col.prop(self, "pose_frame")
        col = layout.column(heading="Export")       # the heading labels the first checkbox
        col.prop(self, "use_animation")
        sub = col.column()
        sub.active = self.use_animation
        sub.prop(self, "curves")
        sub.prop(self, "only_animated")
        sub.prop(self, "fps_mode")
        if self.fps_mode == 'CUSTOM':
            sub.prop(self, "fps_custom")
        col = layout.column(heading="Blender FBX data")
        col.prop(self, "use_mesh_modifiers")
        col.prop(self, "mesh_smooth_type")
        col.prop(self, "use_tspace")
        col.prop(self, "use_triangles")
        col.prop(self, "use_custom_props")
        col.prop(self, "path_mode")
        if self.path_mode == 'COPY':
            col.prop(self, "embed_textures")

    def execute(self, context):
        opts = {
            "scope": self.scope, "pose_frame": self.pose_frame, "animation": self.use_animation,
            "curves": self.curves, "only_animated": self.only_animated,
            "fps": self.fps_custom if self.fps_mode == 'CUSTOM' else None,
            "geometry": {"use_mesh_modifiers": self.use_mesh_modifiers, "mesh_smooth_type": self.mesh_smooth_type,
                         "use_tspace": self.use_tspace, "use_triangles": self.use_triangles,
                         "use_custom_props": self.use_custom_props, "path_mode": self.path_mode,
                         "embed_textures": self.embed_textures},
        }
        try:
            stats = export_fbx_advanced(context, self.filepath, opts)
        except Exception as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        for w in stats["warnings"][:8]:
            self.report({'WARNING'}, w)
        anim = ("%d animated" % stats["animated"]) if self.use_animation else "no animation"
        self.report({'INFO'}, "FBX Advanced: %d nodes (%s), %d dummies, %d skins in %.2f s"
                    % (stats["nodes"], anim, stats["dummies"], stats["skins"], stats["seconds"]))
        return {'FINISHED'}


def menu_func_export(self, context):
    self.layout.operator(EXPORT_SCENE_OT_fbx_advanced.bl_idname, text="FBX Advanced (.fbx)")


def register():
    bpy.utils.register_class(EXPORT_SCENE_OT_fbx_advanced)
    bpy.types.TOPBAR_MT_file_export.append(menu_func_export)


def unregister():
    bpy.types.TOPBAR_MT_file_export.remove(menu_func_export)
    bpy.utils.unregister_class(EXPORT_SCENE_OT_fbx_advanced)


if __name__ == "__main__":
    register()
