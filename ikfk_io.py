"""
ikfk_io.py

Shared constants, namespace helpers, and JSON load/save for the IK -> FK
tool. No UI code lives here - this module is safe to import from a
headless script or a unit test outside Maya's UI thread (it still needs
maya.cmds, so it needs to run inside Maya, but it never touches
cmds.window/formLayout/etc).
"""
import json
import os

import maya.cmds as cmds

# Default rotate order for calibration locators, and the fallback used
# when reading an older JSON entry that has no rotate_order recorded.
# Maya rotate order 2 = ZXY. Must stay in sync between build and switch,
# which is exactly why this is one shared constant now.
HOOK_ROTATE_ORDER = 2  # ZXY

DEFAULT_LIMBS = {
    "L_arm": [
        {"fk_ctrl": "L_shoulder_clavicle_CTRL", "ik_ctrl": "L_shoulder_clavicle_CTRL"},
        {"fk_ctrl": "L_arm_fk_000_CTRL", "ik_ctrl": "L_arm000_JNT"},
        {"fk_ctrl": "L_arm_fk_001_CTRL", "ik_ctrl": "L_arm001_JNT"},
        {"fk_ctrl": "L_arm_fk_002_CTRL", "ik_ctrl": "L_arm002_JNT"},
    ],
    "R_arm": [
        {"fk_ctrl": "R_shoulder_clavicle_CTRL", "ik_ctrl": "R_shoulder_clavicle_CTRL"},
        {"fk_ctrl": "R_arm_fk_000_CTRL", "ik_ctrl": "R_arm000_JNT"},
        {"fk_ctrl": "R_arm_fk_001_CTRL", "ik_ctrl": "R_arm001_JNT"},
        {"fk_ctrl": "R_arm_fk_002_CTRL", "ik_ctrl": "R_arm002_JNT"},
    ],
}

# FK -> IK direction. Unlike DEFAULT_LIMBS above (rotation-only, since FK
# controls just need to match a joint's rotation beneath their own rig
# hierarchy), this direction needs translate as well as rotate.
#
# Each pair has a "type":
#   "offset" - generic driver -> target snap with a fixed translate/rotate
#       offset, same con_loc/hook_loc technique as IK -> FK uses. Used
#       for the shoulder (self-match, all-zero offset - it's a single
#       shared control, not actually switched) and the IK handle (zero
#       translate offset, fixed rotation offset to account for the
#       handle's default orientation vs. the wrist control's).
#   "pole_vector" - special-cased: the pole vector's position is solved
#       live from the shoulder/elbow/wrist controls' current pose (see
#       compute_pole_vector_position in ikfk_switch_fk_to_ik.py) rather
#       than read from a static offset, since a fixed offset off the
#       elbow only stays correct at the pose it was measured at.
#       The distance the solved point sits off the elbow is NOT stored
#       here - it's a live value entered in a field on the IK/FK tab
#       at snap time (see _pole_distance_field in ikfk_ui.py), not
#       calibration data, since it's a per-session judgement call
#       rather than something measured once and reused.
#
# rotate_order is assumed to be HOOK_ROTATE_ORDER (ZXY) for the IK
# handle pairs since it wasn't specified - verify this in-scene if a
# snap looks rotated wrong.
#
# NOTE: Arm definitions are kept as defaults for backwards compatibility,
# but legs should be generated during calibration and saved to JSON
# (see generate_fk_to_ik_pairs in ikfk_calibrate.py).
DEFAULT_FK_TO_IK_LIMBS = {
    "L_arm": [
        {
            "type": "offset",
            "fk_ctrl": "L_shoulder_clavicle_CTRL",
            "ik_ctrl": "L_shoulder_clavicle_CTRL",
            "offset": {
                "rotate_order": HOOK_ROTATE_ORDER,
                "translate": {"x": 0, "y": 0, "z": 0},
                "rotate": {"x": 0, "y": 0, "z": 0},
            },
        },
        {
            "type": "pole_vector",
            "shoulder_ctrl": "L_shoulder_clavicle_CTRL",
            "elbow_ctrl": "L_arm_fk_001_CTRL",
            "wrist_ctrl": "L_arm_fk_002_CTRL",
            "ik_ctrl": "L_arm_ik_pole_CTRL",
        },
        {
            "type": "offset",
            "fk_ctrl": "L_arm_fk_002_CTRL",
            "ik_ctrl": "L_arm_ik_CTRL",
            "offset": {
                "rotate_order": HOOK_ROTATE_ORDER,
                "translate": {"x": 0, "y": 0, "z": 0},
                "rotate": {"x": 0, "y": -90, "z": 0},
            },
        },
    ],
    "R_arm": [
        {
            "type": "offset",
            "fk_ctrl": "R_shoulder_clavicle_CTRL",
            "ik_ctrl": "R_shoulder_clavicle_CTRL",
            "offset": {
                "rotate_order": HOOK_ROTATE_ORDER,
                "translate": {"x": 0, "y": 0, "z": 0},
                "rotate": {"x": 0, "y": 0, "z": 0},
            },
        },
        {
            "type": "pole_vector",
            "shoulder_ctrl": "R_shoulder_clavicle_CTRL",
            "elbow_ctrl": "R_arm_fk_001_CTRL",
            "wrist_ctrl": "R_arm_fk_002_CTRL",
            "ik_ctrl": "R_arm_ik_pole_CTRL",
        },
        {
            "type": "offset",
            "fk_ctrl": "R_arm_fk_002_CTRL",
            "ik_ctrl": "R_arm_ik_CTRL",
            "offset": {
                "rotate_order": HOOK_ROTATE_ORDER,
                "translate": {"x": 0, "y": 0, "z": 0},
                "rotate": {"x": 0, "y": 90, "z": -180},
            },
        },
    ],
}

# Shared by both UI tabs.
DEFAULT_JSON_PATH = os.path.join(
    "/jobs/generic/playground/kevin-o/IK_FK",
    "calibration_pairs.json"
)

IGNORED_NAMESPACES = {"UI", "shared"}

# Rig-specific switch attribute per limb. This is now the SEED/FALLBACK
# used only when a calibration JSON has no "switch_settings" block of
# its own (e.g. an older file, or a brand new one) - the live source of
# truth is the JSON itself once one has been saved, via
# load_switch_settings()/save_switch_settings() below. A limb only
# gets a button on the IK/FK tab once it has an entry here.
#
# NOTE: switch attribute entries are now set via the Channel Box
# picker in the UI (get_selected_channel() below), not typed in or
# guessed from a naming convention. This fallback exists only so an
# older JSON file without its own switch_settings block still loads
# without crashing - remove reliance on it once every limb in your
# working calibration file has been re-picked through the UI.
DEFAULT_SWITCH_SETTINGS = {
    "L_arm": {
        "object": "L_arm_CMP|input",
        "attr_name": "L_arm_ikfk_bl",
        "ik_value": 0,
        "fk_value": 1,
    },

    "R_arm": {
        "object": "R_arm_CMP|input",
        "attr_name": "R_arm_ikfk_bl",
        "ik_value": 0,
        "fk_value": 1,
    },
}

# Top-level JSON keys that are NOT limb-pair lists. load_limbs() (flat
# format) must skip these when reading, and save_limbs() must preserve
# them rather than blindly overwriting the whole file with just the
# limb pairs it knows about.
RESERVED_TOP_LEVEL_KEYS = ("fk_to_ik", "switch_settings", "ik_controls")


# ---------------------------------------------------------------------------
# Small shared helpers
# ---------------------------------------------------------------------------
def ns_join(namespace, name):
    """
    Prepend a namespace to a bare node name or a Maya DAG path.

    Handles simple names:
        ns_join("ddfemale_001", "L_arm000_JNT")
        -> "ddfemale_001:L_arm000_JNT"

    And DAG paths such as:
        ns_join("ddfemale_001", "L_arm_CMP|input")
        -> "ddfemale_001:L_arm_CMP|ddfemale_001:input"
    """
    if not namespace:
        return name

    if "|" in name:
        parts = name.split("|")

        namespaced_parts = [
            "{0}:{1}".format(namespace, part)
            for part in parts
            if part
        ]

        return "|".join(namespaced_parts)

    return "{0}:{1}".format(namespace, name)


def get_switch_plug(namespace, switch_data):
    """
    Build the full namespaced plug ('ns:node.attr') for a limb's IK/FK
    switch attribute from its switch_data dict (object, attr_name).

    Single source of truth for turning switch_data into a plug string -
    do not reconstruct this inline elsewhere. Anything that resolves
    the switch attribute (validation, both snap directions) should
    call this instead.
    """
    switch_node = ns_join(namespace, switch_data["object"])

    return "{0}.{1}".format(
        switch_node,
        switch_data["attr_name"]
    )


def fresh_default_config():
    """Deep-ish copy of DEFAULT_LIMBS so edits never mutate the module constant."""
    return {
        limb_name: [dict(pair) for pair in pairs]
        for limb_name, pairs in DEFAULT_LIMBS.items()
    }


def fresh_default_switch_settings():
    """Deep-ish copy of DEFAULT_SWITCH_SETTINGS so edits never mutate
    the module constant."""
    return {
        limb_name: dict(settings)
        for limb_name, settings in DEFAULT_SWITCH_SETTINGS.items()
    }


def error_dialog(title, message):
    cmds.confirmDialog(title=title, message=str(message), button=["OK"])


# ---------------------------------------------------------------------------
# Namespace helpers
# ---------------------------------------------------------------------------
def get_scene_namespaces():
    all_ns = cmds.namespaceInfo(listOnlyNamespaces=True, recurse=True) or []
    return sorted(ns for ns in all_ns if ns not in IGNORED_NAMESPACES and ns != "")


def strip_namespace(name):
    return name.split(":")[-1]


def get_selected_short_name():
    sel = cmds.ls(selection=True)
    if not sel:
        cmds.warning("Nothing selected.")
        return None
    return strip_namespace(sel[0].split("|")[-1])


def get_selected_channel():
    """
    Return (object, attr_name) for the attribute currently highlighted
    in the Channel Box, with namespace stripped from the object (same
    convention as get_selected_short_name()), or None if nothing
    usable is selected.

    object may still contain '|' path separators if the selected node
    has one (e.g. 'L_arm_CMP|input') - ns_join()/get_switch_plug()
    already know how to handle that. attr_name is the bare attribute
    name, not 'node.attr'.
    """
    sel = cmds.ls(selection=True)
    if not sel:
        cmds.warning("Nothing selected.")
        return None

    attrs = cmds.channelBox(
        "mainChannelBox",
        query=True,
        selectedMainAttributes=True
    )

    if not attrs:
        cmds.warning(
            "No attribute is highlighted in the Channel Box. Click "
            "the switch attribute there, then press Set."
        )
        return None

    node = sel[0]

    stripped_parts = [
        strip_namespace(part)
        for part in node.split("|")
        if part
    ]

    object_name = "|".join(stripped_parts)

    return object_name, attrs[0]


# ---------------------------------------------------------------------------
# JSON load/save
# ---------------------------------------------------------------------------
def _read_json_root(path):
    """Read the raw JSON root object at path, or {} if there's no file
    yet / it isn't a dict. Used so save_limbs/save_switch_settings can
    merge into whatever's already on disk instead of overwriting it."""
    if not path or not os.path.isfile(path):
        return {}

    try:
        with open(path, "r") as f:
            data = json.load(f)

    except Exception:
        return {}

    if not isinstance(data, dict):
        return {}

    return data


def load_limbs(path):
    """Load a limbs config dict from JSON. Accepts either the plain
    {"L_arm": [...]} format or the older wrapped {"limbs": {...}} format.
    Reserved top-level keys (fk_to_ik, switch_settings, ik_controls)
    are excluded from the flat format so they're never mistaken for a
    limb."""
    if not os.path.isfile(path):
        raise IOError(
            "Calibration JSON file was not found:\n{0}".format(path)
        )

    with open(path, "r") as f:
        data = json.load(f)

    if not isinstance(data, dict):
        raise ValueError("The JSON root must be an object containing limb names.")

    # Backwards compatibility with the wrapped format.
    if "limbs" in data:
        limbs = data["limbs"]
    else:
        limbs = {
            key: value for key, value in data.items()
            if key not in RESERVED_TOP_LEVEL_KEYS
        }

    if not isinstance(limbs, dict):
        raise ValueError("Calibration JSON does not contain a valid limbs dictionary.")

    return limbs


def save_limbs(limbs, path):
    """Write limbs to path in the flat top-level format, preserving
    any reserved keys (fk_to_ik, switch_settings, ik_controls) already
    saved to that file rather than wiping them out."""
    data = _read_json_root(path)

    # Drop anything that used to be a limb (including a stale "limbs"
    # wrapper key), but keep the reserved keys untouched.
    for key in list(data.keys()):
        if key not in RESERVED_TOP_LEVEL_KEYS:
            del data[key]

    data.update(limbs)

    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def fresh_default_fk_to_ik_config():
    """Deep-ish copy of DEFAULT_FK_TO_IK_LIMBS so edits never mutate the
    module constant."""
    return {
        limb_name: [dict(pair) for pair in pairs]
        for limb_name, pairs in DEFAULT_FK_TO_IK_LIMBS.items()
    }


def load_fk_to_ik_limbs(path):
    """Load the FK -> IK pairs from the same calibration JSON file used
    for IK -> FK, under a separate top-level "fk_to_ik" key so the two
    directions don't collide. If the file doesn't have that key yet
    (e.g. an older calibration file, or one that's only ever had
    IK -> FK data saved to it), fall back to DEFAULT_FK_TO_IK_LIMBS -
    there's no Calibration-tab workflow for this direction yet, so
    these seeded values are the only source until one exists."""
    data = _read_json_root(path)

    if "fk_to_ik" not in data:
        return fresh_default_fk_to_ik_config()

    fk_to_ik = data["fk_to_ik"]

    if not isinstance(fk_to_ik, dict):
        raise ValueError(
            "Calibration JSON's 'fk_to_ik' entry must be a dictionary."
        )

    return fk_to_ik


def save_fk_to_ik_limbs(fk_to_ik_limbs, path):
    """Write FK -> IK pairs into the calibration JSON's "fk_to_ik" key,
    preserving everything else already in the file (limb pairs,
    switch_settings, ik_controls)."""
    data = _read_json_root(path)
    data["fk_to_ik"] = fk_to_ik_limbs

    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def load_switch_settings(path):
    """Load the per-limb switch attribute settings (object, attr_name,
    ik_value, fk_value) from the calibration JSON's "switch_settings"
    key. Falls back to DEFAULT_SWITCH_SETTINGS if the file has no such
    key yet - matches the same back-compat pattern as
    load_fk_to_ik_limbs."""
    data = _read_json_root(path)

    if "switch_settings" not in data:
        return fresh_default_switch_settings()

    switch_settings = data["switch_settings"]

    if not isinstance(switch_settings, dict):
        raise ValueError(
            "Calibration JSON's 'switch_settings' entry must be a "
            "dictionary."
        )

    return switch_settings


def save_switch_settings(switch_settings, path):
    """Write switch_settings into the calibration JSON's
    "switch_settings" key, preserving everything else already in the
    file (limb pairs, fk_to_ik, ik_controls)."""
    data = _read_json_root(path)
    data["switch_settings"] = switch_settings

    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def load_ik_controls(path):
    """Load per-limb real FK -> IK controls (the IK handle and pole
    vector control actually driven by a FK -> IK snap) from the
    calibration JSON's "ik_controls" key.

    Expected shape:
        "ik_controls": {
            "L_arm": {"ik_ctrl": "L_arm_ik_CTRL", "pole_ctrl": "L_arm_ik_pole_CTRL"}
        }

    Unlike switch_settings, there is no legacy default to fall back
    to - this is a new key, so a file without it simply has no
    ik_controls recorded yet and this returns an empty dict rather
    than a seeded fallback."""
    data = _read_json_root(path)

    if "ik_controls" not in data:
        return {}

    ik_controls = data["ik_controls"]

    if not isinstance(ik_controls, dict):
        raise ValueError(
            "Calibration JSON's 'ik_controls' entry must be a "
            "dictionary."
        )

    return ik_controls


def save_ik_controls(ik_controls, path):
    """Write ik_controls into the calibration JSON's "ik_controls"
    key, preserving everything else already in the file (limb pairs,
    fk_to_ik, switch_settings)."""
    data = _read_json_root(path)
    data["ik_controls"] = ik_controls

    with open(path, "w") as f:
        json.dump(data, f, indent=2)
