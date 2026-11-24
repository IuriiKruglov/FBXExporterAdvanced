| | |
| :---: | :---: |
| [![Landing gear and hatches animated in Unity](images/06_Unity_Anim_preview.gif)](images/06_Unity_Anim_preview.gif) | [![Rear view in Unity: landing gear and skinned engine nozzles](images/07_Unity_Anim_preview.gif)](images/07_Unity_Anim_preview.gif) |

# FBX Exporter Advanced

A Blender add-on that exports the asset assembly to FBX the propper way. Its first target is **Unity** (Blender → FBX → Unity); other 3D applications that read FBX come second.

It exists because Blender's stock FBX exporter does a poor job on complex assets for Unity, such as long hierarchies with object animation. The scale is off, objects explode, and the keys and axes are wrong: see [Why not Blender's stock FBX exporter](#why-not-blenders-stock-fbx-exporter).

- **Geometry, normals, UVs, colours, materials and textures** are written by Blender's own FBX exporter, unchanged.
- **The assembly is rebuilt the 3ds Max way:**
  - hierarchy and local transforms, with Y-up through a −90° X `PreRotation` on the root nodes;
  - centimetres and Euler rotation orders;
  - keys on Blender's key frames with 3ds Max tangents, or no animation at all;
  - armatures as Null "dummies" (Z forward, Y up) with a Skin deformer and a bind pose, or as **exact bones**: skeleton bones with the Blender bones' own axes and keys. See [Exact bones](#exact-bones);
  - assemblies: a bone that follows an object with a *Child Of* hangs under that object, as the Blender → 3ds Max bridge 2.5 builds it. See [Assemblies](#assemblies-bones-linked-to-objects).

The animations above were exported from Blender with this add-on and played in Unity: landing gear and hatches (left), and the rear view with the skinned engine nozzles (right).

**Version:** 0.4.0 · **Blender:** 3.6 LTS to 5.2 · **License:** GPL-3.0-or-later

Created by Claude (Anthropic) under the leadership of Kruglov Iurii.

---

## Contents

- [Why not Blender's stock FBX exporter](#why-not-blenders-stock-fbx-exporter)
- [Installation](#installation)
- [Use](#use)
- [Options](#options)
- [Keys](#keys)
- [What is written](#what-is-written)
- [Assemblies: bones linked to objects](#assemblies-bones-linked-to-objects)
- [Exact bones](#exact-bones)
- [Example: skinned engine nozzles](#example-skinned-engine-nozzles)
- [Differences from 3ds Max's own FBX](#differences-from-3ds-maxs-own-fbx)
- [Verification](#verification)
- [Limitations](#limitations)
- [Changelog](#changelog)
- [License](#license)

---

## Why not Blender's stock FBX exporter

Blender's own FBX exporter (`io_scene_fbx`; its transform code is the same in Blender 3.6 and 5.x) does a poor job on complex assets for Unity, such as long hierarchies with object animation:

- **The scale is off.** With the default settings, root objects get scale 100. The *Apply Transform* option gives them scale 1, but then objects from the third level of a parent chain down (a child of a child) get the wrong size.
- **Objects explode.** With *Apply Transform*, those deeper objects also leave their place in the assembly, by metres.
- **The keys are wrong.** Every frame is re-sampled into XYZ Euler with linear keys and then simplified, so Unity does not get the keys you set, also they are baked every frame in range generating additional keys in all transform channels. With *Apply Transform*, the animation of the deeper objects is wrong as well.
- **The axes are wrong.** The Z-up → Y-up conversion is multiplied into the root objects' rotation, and every rotation is written in XYZ order.

| | Blender's stock exporter | This add-on |
|---|---|---|
| Scale in Unity | **100 on the root objects** (default *Apply Scalings: All Local*) | **1 on every object** |
| *Apply Transform* | Scale 1, but **breaks parent chains** from the third level down. The option is marked experimental and "known to be broken with armatures/animations" (its own tooltip) | Not needed |
| Axes | The Z-up → Y-up conversion is multiplied into the root objects' rotation; every rotation is in XYZ order | A −90° X `PreRotation` on the root nodes; `Lcl Rotation` keeps each object's own values, in its own rotation order |
| Animation | Every frame re-sampled to XYZ Euler, linear keys, then simplified | Blender's keys, with 3ds Max tangents |
| Bones | Armature node, bone-axis correction | Dummies + Skin, with a bind pose |

**Measured** on a chain of six parented boxes with 54 rotation keys, exported by the stock exporter of Blender 4.2 and 5.2:
- **Default settings:** the root box has scale 100. Its rotation is written as (−80.9°, 22.9°, −6.3°) instead of its own (11.5°, 5.7°, 22.9°). The 54 keys become 865.
- ***Apply Transform*:** boxes 1 and 2 are right. Boxes 3 and 4 are 100 times too small, and boxes 5 and 6 are 10,000 times too small. All four are up to 2.2 m out of place. The 54 keys become 1,417.
- ***Apply Transform* with *Apply Scalings: FBX All*:** from box 3 on, sizes change by up to 19% during the animation, and positions are off by up to 1.6 m.
- **This add-on:** every box has scale 1, its own rotation values and Blender's 54 keys, and matches Blender within 0.001 cm.

This add-on keeps Blender's exporter for the data and replaces only the assembly.

---

## Installation

The add-on comes in two forms with the same code inside. Install only one of them. Download it from the [latest release](https://github.com/IuriiKruglov/FBXExporterAdvanced/releases/latest), under *Assets*. The *Source code* archives there are the repository itself, not an add-on Blender can install. Also you can get addon on [Gumroad](https://tricky3d.gumroad.com/l/FbxExporterAdvanced).

| File | Blender | Installs as |
|---|---|---|
| `fbx_exporter_advanced-0.4.0.zip` | 4.2 and later | Extension |
| `fbx_exporter_advanced.py` | 3.6 and later | Legacy add-on |

**Blender 4.2 and later (4.5, 5.x)**
1. Open *Edit → Preferences → Add-ons*.
2. Click the drop-down arrow (⌄) in the top-right corner and choose **Install from Disk…**
3. Select the `.zip` (or the `.py`) and make sure **FBX Exporter Advanced** is enabled.

You can also drag the `.zip` from your file browser into the Blender window.

**Blender 3.6 – 4.1**
1. Open *Edit → Preferences → Add-ons* and click **Install…**
2. Select `fbx_exporter_advanced.py`, then tick the checkbox next to **Import-Export: FBX Exporter Advanced**. Keep the file's name: Blender uses it as the add-on's module name, so a renamed copy installs as a second add-on, and a name with dots in it doesn't load at all.

Blender's own FBX exporter is needed; it is enabled automatically if it was turned off.

**Updating.** Install the new file the same way.
- The extension is replaced in place and stays enabled.
- For the `.py`, turn the add-on off and on again (or restart Blender) so the new code is loaded.
- To switch from the `.py` to the `.zip`, first remove the old add-on: expand it in the Add-ons list and click **Uninstall**.

### Compatibility

| Blender | Status |
|---|---|
| 5.2 LTS | Tested with 5.2.4: exporting, both install forms |
| 4.2 LTS | Tested with 4.2.23: exporting, both install forms |
| 3.6 LTS | Supported with the `.py` file. The code is checked against the Blender 3.6 API and Python 3.10, but no test ran inside 3.6 |

---

## Use

*File → Export → FBX Advanced (.fbx)*. The scene is left exactly as it was.

<img src="images/02_exporter_console.png" alt="Export options" width="244">

The status bar reports the result:

```
FBX Advanced: 65 nodes (35 animated), 2 dummies, 1 skins in 0.35 s
FBX Advanced: 65 nodes (no animation), 2 dummies, 1 skins in 0.36 s
```

---

## Options

### Assembly

| Option | Default | Meaning |
|---|---|---|
| **Objects** | All Visible | *All Visible* or *Selected* |
| **Pose Frame** | 0 | Frame of the static pose (the bridge's import frame). Without animation, the assembly is written in this pose |
| **Bones** | Dummies | **Dummies:** the deform bones, their parents and the bones carrying objects become Null dummies (Z forward, Y up), as the bridge builds them by default. **Exact Bones:** every bone becomes a skeleton bone with the Blender bone's own axes, its rest pose as `PreRotation` and Blender's channel values as keys, as the bridge's *Exact bones* option builds them. See [Exact bones](#exact-bones) |
| **Export Animation** | On | *On*: the animated assembly. *Off*: the same assembly without animation. The file has no keys and no take, and every node holds its pose at *Pose Frame*, with the same values as in the animated file. The three options below apply only with animation (greyed out without it) |
| **Keys** | Exact Keys | **Exact Keys:** keys only on Blender's key frames, with 3ds Max auto tangents, like files exported from 3ds Max. See [Keys](#keys). **Bake Every Frame:** one key per frame on every animated node |
| **Only Animated Channels** | On | Channels whose value never changes get no keys. The node keeps their exact static value, so you do not need to delete dead keys in Blender |
| **Frame Rate** | Custom, 30 fps | *Custom*: keep Blender's frame numbers and write this frame rate. 30 fps gives the same timing as the bridge's 3ds Max files: a key on frame 24 is at 0.8 s, so an animation made at 24 fps plays 25% faster than in Blender. *Scene*: Blender's frame rate, the same speed as in Blender |

### Blender FBX data

These are passed to Blender's own exporter, with its defaults: *Apply Modifiers*, *Smoothing*, *Tangent Space*, *Triangulate Faces*, *Custom Properties*, *Path Mode*, *Embed Textures*.

---

## Keys

With **Exact Keys**, translation, rotation and scale each get keys only on the frames where Blender has location, rotation or scale keys:

- **Key values are exact.** Every node matches Blender on its key frames. 3ds Max shows the same key frames, with no extra keys.
- **Between keys**, the curve uses 3ds Max auto tangents, as 3ds Max writes them.
- **Bone dummies** take their tangent type from Blender's interpolation, as the bridge sets them: *Constant* → step, *Linear* → linear, flat Bézier handles → flat, anything else → auto.
- **Rotation keys keep Blender's own angle values** when the rotation converts channel for channel (26 of the 28 rotating nodes on the test asset), so a whole turn between two keys survives. Otherwise the angles come from the pose on each key frame, nearest to the previous key.
- **Quaternion and axis-angle rotation** is converted to Euler keys on its key frames.

Some animation cannot be written as keys on Blender's key frames. In those cases, the node (or the whole rig) gets one key per frame, exact on every frame:
- constraints, drivers and NLA strips (a *Child Of* that links a root bone to an object is no reason, see [Assemblies](#assemblies-bones-linked-to-objects));
- delta transforms, F-Curve modifiers and linear extrapolation;
- bones that do not inherit rotation or scale.

**Bake Every Frame** writes one linear key per frame on every animated node.

---

## What is written

| Item | Value |
|---|---|
| Axis | Y-up (`UpAxis` 1, `FrontAxis` +2, `CoordAxis` +0, `OriginalUpAxis` 2), as 3ds Max with *Up Axis: Y-up* |
| Units | Centimetres, `UnitScaleFactor` 1 (3ds Max and Unity *Units: Automatic* with cm system units) |
| Root nodes | `PreRotation` (−90, 0, 0) and `RotationActive` 1. Position written as (x, z, −y) |
| Other nodes | Local transform relative to the 3ds Max parent, in cm |
| Rotation order | The object's Blender rotation mode (`RotationOrder`, with `RotationActive` 1). Quaternion and axis-angle objects use XYZ. Bone dummies use XYZ, as the bridge creates them. Exact bones keep the bone's Euler order (quaternion and axis-angle bones: XYZ) |
| Every node | `InheritType` 1, `ScalingMax`, `DefaultAttributeIndex` |
| Hierarchy | As the bridge builds it in 3ds Max (see below) |
| Bones | *Dummies:* every deform bone, its parents and bones carrying objects become a Null node named like the bone, with the bone's axes turned +90° about its X axis (Z forward, Y up). In [assemblies](#assemblies-bones-linked-to-objects), the axes are turned in 90° steps so that Y points up and Z to the front. *Exact Bones:* every bone becomes a `LimbNode` (`TypeFlags: Skeleton`) named like the bone, with the bone's own axes and its rest pose as `PreRotation` (see [Exact bones](#exact-bones)) |
| User properties | `UDP3DSMAX` with `BlenderBoneAxes = …` on the dummies of [assemblies](#assemblies-bones-linked-to-objects), as the bridge sets it in 3ds Max. No other node gets one |
| Skin | `Skin` deformer on the mesh, one `Cluster` per deform bone with the weights normalized like Blender's Armature modifier. `TransformLink` = bone dummy in the rest pose, `Transform` = inverse(`TransformLink`) × mesh. Several Armature modifiers on one mesh give one Skin with the bones of all of them |
| Bind pose | One `BindPose`, named `BIND_POSES` as 3ds Max names it. It holds the skinned meshes, every bone dummy of their clusters and all their parents. Bone matrices are the clusters' `TransformLink`; meshes and other objects use the pose frame. 3ds Max's importer takes the Skin's initial pose from it, instead of warning that the *Initial Pose* is missing |
| Unweighted vertices | Bound to a `<mesh>__SkinStatic` Null under the mesh |
| Animation | One take, `Take 001`, layer `BaseLayer`. `T` / `R` / `S` curve nodes on `Lcl Translation` / `Lcl Rotation` / `Lcl Scaling`. With *Export Animation* off: no take, no animation stack, layer or curves |
| Geometry, materials | Blender's own exporter, Blender's layout (shared mesh data stays shared) |

**Hierarchy rules.** These follow the rules:
- An armature object becomes a node only when it is animated.
- Root bones are linked to the armature's parent.
- A root bone that follows an object (or a bone of another armature) with a plain *Child Of* is linked to that object's node (or that bone's dummy). See [Assemblies](#assemblies-bones-linked-to-objects).
- Objects parented to a bone are linked to that bone's dummy or exact bone (Game object, Empty object).
- Objects parented to an armature that is not a node are linked to its parent.

---

## Assemblies: bones linked to objects

In an assembly the bones of one skin hang under different objects. In the CSLM12 gun mount, the ammunition belt `CSLM12_Belt` is skinned by two bones: `CSLM12_Bone_gun` moves with the turret `CSLM12_Hull`, and `CSLM12_Bone_box` with `CSLM12_Base`. In Blender that is one armature whose root bones follow their objects with **Child Of** constraints (the 3ds Max → Blender bridge 0.9 builds it this way). The FBX holds it as 3ds Max does after the Blender → 3ds Max bridge 2.5:

```
Blender:  CSLM12_Base → CSLM12_Belt_Armature (CSLM12_Bone_gun, CSLM12_Bone_box)
          CSLM12_Bone_gun: Child Of CSLM12_Hull      CSLM12_Bone_box: Child Of CSLM12_Base
FBX:      CSLM12_Base → CSLM12_Arm → CSLM12_Hull → CSLM12_Bone_gun
          CSLM12_Base → CSLM12_Bone_box
          CSLM12_Belt: Skin (CSLM12_Bone_gun, CSLM12_Bone_box)
```

A root bone counts as linked when its only active constraint is a plain *Child Of*:
- full influence, every location / rotation / scale channel;
- owner space *Pose* and target space *World* (the defaults on a bone);
- no keys or drivers on the bone's constraints;
- the bone is where that constraint puts it;
- its target is exported: an object, or a bone of another armature.

What the FBX gets:
- **Node.** The bone's dummy is a child of the target (the object's node, or the other bone's dummy), with no armature node and no constraint. Its local transform is `inverse · rest · basis` (Blender's *Child Of*: bone world = target world · inverse · pose matrix). So it moves with the target, and with *Exact Keys* its own keys stay keys: a *Child Of* link alone does not bake the rig any more.
- **Dummy axes.** Every dummy of such a rig takes its bone's axes turned in 90° steps, so that **Y points up in the scene and Z to the front**, whatever way the bone points. The dummy carries the 3ds Max user property `BlenderBoneAxes`: the bone's X, Y and Z as dummy axes (`x,-z,y` would be the plain dummy). The 3ds Max → Blender bridge (0.9) reads it to give the bones back their own axes.
- **Skin.** Bound in the armature's rest pose, as in Blender. Several Armature modifiers on one mesh (for example one armature per bone, each masked by its bone's vertex group) become one Skin with all their bones.

A root bone that is not linked (a *Child Of* with less than full influence or keyed influence, another constraint, a target that is not exported or that hangs under the bone itself) is handled as before: its rig is sampled on every frame, and its dummies keep the plain axes.

---

## Exact bones

With **Bones: Exact Bones** every armature is written as the same bone rig as in Blender, like the bridge's *Exact bones* option builds it in 3ds Max:

| | Dummies (default) | Exact Bones |
|---|---|---|
| FBX node | `Null` | `LimbNode`, a skeleton bone (`TypeFlags: Skeleton`) |
| Which bones | Deform bones, their parents, bones with child objects | Every bone of the armature; the deform bones go into the Skin |
| Axes | Bone axes turned +90° about X (Z forward, Y up); in assemblies, Y up and Z to the front | The Blender bone's own axes: Y along the bone, X and Z as set by its roll |
| Rest pose relative to the parent | Part of the local transform | `PreRotation`, the FBX form of the bridge's *Freeze Transform* layers |
| Rotation keys | Blender's values where the rotation converts channel for channel, otherwise the pose on each key frame | Blender's own Euler values, in the bone's rotation order |
| Armature object | None, unless it is animated | None, unless it is animated |

```
Blender:  Engine  →  Armature  →  Bone_01  →  Bone_02        (parent → child)
FBX:      Engine  →  Bone_01  →  Bone_02                      (LimbNode)
```

- **Hierarchy.** As with dummies: child bones under their parent bones, root bones under the armature's parent (or under the object they follow with *Child Of*, see [Assemblies](#assemblies-bones-linked-to-objects)), and objects parented to a bone under that bone.
- **Keys are Blender's keys.** The bone's rest rotation relative to its parent is the node's `PreRotation`, so `Lcl Rotation` holds Blender's rotation channels: the same values on the same key frames, whole turns included (0° → 400° stays 400°). `Lcl Translation` holds the rest offset plus Blender's location in the parent's axes, and `Lcl Scaling` Blender's scale (times the armature's scale for root bones). Quaternion and axis-angle bones get Euler XYZ keys on their key frames.
- **Linked root bones** rest in the *Child Of* frame (`inverse · rest`), so their keys are Blender's keys as well.
- **Skin and bind pose** as with dummies: the cluster's `TransformLink` is the bone in its rest pose, with its own axes.
- **Baked rigs** (constraints, drivers, NLA, partial inheritance, or *Bake Every Frame*) get a key on every frame, exact on every frame.

---

## Example: skinned engine nozzles

The engine nozzles of the plane in the animations above are skinned. In Blender, each engine mesh has an armature with two bones as its child. The bones carry scale keys, and the mesh deforms through an Armature modifier bound to vertex groups:

![Blender: the armature Engine_Bones_L under the engine mesh, its scale keys in the Dope Sheet](images/00_Blender_Armature_setup.png)

![Blender: the engine mesh's modifiers, Triangulate and Armature](images/01_Blender_Armature_setup.JPG)

In Unity, the armature object is gone with keeping the bones one of which animated, arrive as plain transforms (Blender Empty object, 3ds Max dummies, Unity Game object) under the engine mesh object. The mesh has a *Skinned Mesh Renderer* whose *Root Bone* is the first bone:

![Unity: the engine mesh with its Skinned Mesh Renderer, the bone dummies under it](images/04_Unity.png)

![Unity: a bone dummy, a plain transform](images/05_Unity.png)

The asset's root in Unity has rotation 0 and scale 1:

![Unity: the imported asset and its root transform](images/03_Unity.png)

---

## Differences from 3ds Max's own FBX

Intentional:

- **Static rotation of non-XYZ nodes.** For nodes with a YXZ / ZXY / … rotation order, 3ds Max writes the static `Lcl Rotation` as XYZ angles. A channel without keys then falls back to a wrong value: on the test asset this moves two hatches by 2.35° and the air brakes by 0.67°. This exporter writes the correct value, so deleting keys never changes the orientation.
- **One tangent sign.** One mirrored strut has a wrong rotation slope sign in 3ds Max's file. This exporter writes the correct sign. All other slopes are written exactly as 3ds Max writes them, including its unit for rotation slopes (radians per second, while the values are in degrees).
- **Bind pose.** 3ds Max's file has none, so 3ds Max's own importer warns about the *Initial Pose*. This exporter writes one.
- **Exact bones.** No FBX that 3ds Max exported from the bridge's exact bones was available to compare with. In 3ds Max those bones hold their rest pose in *Freeze Transform* layers. This exporter writes it as the bone's `PreRotation`, the FBX equivalent, while 3ds Max's own files use `PreRotation` only for the axis conversion of root nodes.
- **Not written** (3ds Max bookkeeping): Physical-material parameters, `MaxHandle`, display layers. User properties (`UDP3DSMAX`) only on the dummies of [assemblies](#assemblies-bones-linked-to-objects), where they hold `BlenderBoneAxes`. After the bridge 2.5, 3ds Max also writes `BlenderBoneAxes = x,-z,y` on the plain dummies; without it the 3ds Max → Blender bridge takes the same plain axes.

---

## Verification

Test asset: Plane: 60 meshes, 3 empties, 2 skinned bones, 35 animated nodes, YXZ / ZXY rotation orders.

| Check | Result |
|---|---|
| World matrix of every node vs Blender on its key frames (frames 0 – 251) | ≤ 0.000064 cm, ≤ 0.000097° |
| Exact Keys at 30 fps with all channels, vs the file exported from 3ds Max 2024 (FBX 2020.3.2): node properties, parents, every key time / value / flag / slope, skin indexes, weights and matrices | Identical except the two 3ds Max quirks above |
| Keys | 254 keys on 42 curves with *Only Animated Channels*. Without it: 612 keys on 105 curves, as in 3ds Max's file |
| *Export Animation* off | The same file as with animation, minus the take and the animation objects; every node's values are identical. Every node matches Blender's pose at *Pose Frame* (tested at frames 0 and 30; stress scene at frame 25) within 0.00006 cm and 0.00003° |
| Bind pose | Passes the FBX SDK's bind-pose rules: every parent is in the pose, every cluster bone is in the pose with its parents, and bone matrices equal `TransformLink` (difference 0). The independent ufbx reader takes it as the bind pose of every node. Blender's importer gives the same result with and without it |
| Geometry vs 3ds Max's file | Same vertices, polygons, UVs, colours and edges; normals within 0.0009 (Blender 5.2 vs 3.6 weighted normals) |
| Blender 4.2 LTS and 5.2 | Same result. Code checked against Blender 3.6 APIs and Python 3.10 |
| Stress scene: quaternion bones, Euler bones, connected bones, constraints, Cycles modifier, object on a bone, unweighted vertices | Exact at every key frame; baked nodes exact on every frame |
| Assemblies: the CSLM12 gun mount (one armature with *Child Of*, and the hand-made version with two armatures), and test rigs with a bone linked to a bone of another armature, an armature moved after *Set Inverse*, an object parented to a bone of a linked rig, and links that must not be made (half influence, keyed influence, hidden target, a target that hangs under the bone itself) | Node world matrices vs Blender ≤ 0.00006 cm on key frames (*Exact Keys*) and on every frame (*Bake Every Frame*); the skinned mesh computed from the FBX clusters vs Blender's mesh ≤ 0.00011 cm; bind pose valid. Blender 4.2 and 5.2 |
| *Exact Bones*: a rig with Euler bones in four rotation orders, a whole turn (0° → 400°), location and scale keys, a non-deform control bone, an object on a bone, a scaled armature and a root bone linked with *Child Of*; the test asset, the stress scene, the CSLM12 assemblies and the test rigs above | Node world matrices vs Blender ≤ 0.0001 cm (on key frames with *Exact Keys*, on every frame with *Bake Every Frame*); the skinned mesh computed from the FBX clusters ≤ 0.0002 cm; `Lcl Rotation` keys equal Blender's Euler channel values (≤ 0.00002°); bind pose valid. The independent ufbx reader gives the same node world matrices (≤ 0.0001 cm). Blender 4.2 and 5.2 |
| Scenes without linked bones: the test asset, two more assets and the stress scenes, with and without animation, and with *Bake Every Frame* | The same file as 0.3.1: 27 exports compared, only the creation time and Blender's random document ID differ |

---

## Limitations

- **Between keys**, Exact Keys follows 3ds Max's auto tangents, not Blender's Bézier handles or easing modes. Use *Bake Every Frame* where the in-between motion must match Blender exactly.
- **Cameras and lights** are not exported.
- **Shape key animation** is not exported. Shape keys themselves are written by Blender's exporter.
- **Collection instances** are not converted (you get a warning).
- **Skinning:**
  - several Armature modifiers on one mesh become one Skin. Blender applies them one after another, so where a vertex has weights in more than one of them, its result differs from the Skin;
  - envelopes, the vertex-group mask and *Preserve Volume* are ignored (warnings);
  - B-Bone curvature is not converted.
- **Units:** centimetres only.
- **Assemblies:** a link is a fixed parent. A bone that is picked up and dropped (keyed *Child Of* influence) is not linked; it is baked under the armature's parent.

---

## Changelog

| Version | Changes |
|---|---|
| **0.4.0** | **Bones: Exact Bones** option: every bone becomes a skeleton bone (`LimbNode`) with the Blender bone's own axes, its rest pose as `PreRotation` and Blender's channel values as keys, as the bridge's *Exact bones* option builds them. **Assemblies:** a root bone that follows an object (or a bone of another armature) with a plain *Child Of* becomes a dummy under that object, with no constraint and its own keys, instead of a per-frame bake. The dummies of such rigs point Y up and Z to the front (the bone's axes turned in 90° steps) and carry the 3ds Max user property `BlenderBoneAxes`, as the Blender → 3ds Max bridge 2.5 builds them. Several Armature modifiers on one mesh become one Skin. Scenes without such links give the same file as 0.3.1 |
| **0.3.1** | *Frame Rate* defaults to *Custom*, 30 fps. First public release, also as a Blender 4.2+ extension |
| **0.3** | *Export Animation* checkbox: the assembly with animation, or the same assembly without animation in the pose of *Pose Frame* |
| **0.2** | Renamed to **FBX Exporter Advanced** (menu: *FBX Advanced (.fbx)*). **Exact Keys** (the former *3ds Max Tangents*) is the default. The *Blender Curves* mode is removed: 3ds Max's importer resampled its curves into extra keys. A bind pose is written for skinned meshes |
| **0.1** | First version, as *FBX Exporter (3ds Max style)*: 3ds Max assembly conventions, Blender curves, 3ds Max tangents and bake modes, only-animated channels, bones as dummies with Skin |

---

## License

GPL-3.0-or-later. See [LICENSE](LICENSE).

Blender is a registered trademark of the Blender Foundation. 3ds Max and FBX are registered trademarks of Autodesk, Inc. Unity is a trademark of Unity Technologies. This project is not affiliated with or endorsed by any of them.
