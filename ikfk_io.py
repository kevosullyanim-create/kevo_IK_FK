"""
ikfk_io.py

Shared constants, namespace helpers, and JSON load/save for the IK/FK
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
    "L_arm": {
        "fk_match_ik": [
            {
                "fk_ctrl": "L_shoulder_clavicle_CTRL",
                "source": "L_shoulder_clavicle_CTRL",
                "use_for_pole_vector": False,
            },
            {
                "fk_ctrl": "L_arm_fk_000_CTRL",
                "source": "L_arm000_JNT",
                "use_for_pole_vector": True,
            },
            {
                "fk_ctrl": "L_arm_fk_001_CTRL",
                "source": "L_arm001_JNT",
                "use_for_pole_vector": True,
            },
            {
                "fk_ctrl": "L_arm_fk_002_CTRL",
                "source": "L_arm002_JNT",
                "use_for_pole_vector": True,
            },
        ],
        "ik_match_fk": [
            {
                "type": "offset",
                "role": "ik_handle",
                "ik_ctrl": "",
                "source": "",
            },
            {
                "type": "pole_vector",
                "role": "pole_vector",
                "ik_ctrl": "",
                "shoulder_ctrl": "",
                "elbow_ctrl": "",
                "wrist_ctrl": "",
            },
        ],
    },
    "R_arm": {
        "fk_match_ik": [
            {
                "fk_ctrl": "R_shoulder_clavicle_CTRL",
                "source": "R_shoulder_clavicle_CTRL",
                "use_for_pole_vector": False,
            },
            {
                "fk_ctrl": "R_arm_fk_000_CTRL",
                "source": "R_arm000_JNT",
                "use_for_pole_vector": True,
            },
            {
                "fk_ctrl": "R_arm_fk_001_CTRL",
                "source": "R_arm001_JNT",
                "use_for_pole_vector": True,
            },
            {
                "fk_ctrl": "R_arm_fk_002_CTRL",
                "source": "R_arm002_JNT",
                "use_for_pole_vector": True,
            },
        ],
        "ik_match_fk": [
            {
                "type": "offset",
                "role": "ik_handle",
                "ik_ctrl": "",
                "source": "",
            },
            {
                "type": "pole_vector",
                "role": "pole_vector",
                "ik_ctrl": "",
                "shoulder_ctrl": "",
                "elbow_ctrl": "",
                "wrist_ctrl": "",
            },
        ],
    },
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

# Old files may still contain these top-level keys. Only
# "switch_settings" survives in the new format; the rest are migrated
# into the nested per-limb structure on load and omitted on save.
LEGACY_RESERVED_TOP_LEVEL_KEYS = ("fk_to_ik", "switch_settings", "ik_controls")
RESERVED_TOP_LEVEL_KEYS = ("switch_settings",)


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


def _copy_pairs(pairs):
    return [
        dict(pair) if isinstance(pair, dict) else pair
        for pair in pairs
    ]


def _fresh_empty_limb_data():
    return {
        "fk_match_ik": [],
        "ik_match_fk": [
            {
                "type": "offset",
                "role": "ik_handle",
                "ik_ctrl": "",
                "source": "",
            },
            {
                "type": "pole_vector",
                "role": "pole_vector",
                "ik_ctrl": "",
                "shoulder_ctrl": "",
                "elbow_ctrl": "",
                "wrist_ctrl": "",
            },
        ],
    }


def _copy_limb_data(limb_data):
    copied = _fresh_empty_limb_data()

    copied["fk_match_ik"] = _copy_pairs(
        limb_data.get("fk_match_ik", [])
    )
    copied["ik_match_fk"] = _copy_pairs(
        limb_data.get("ik_match_fk", [])
    )

    _ensure_generated_ik_match_fk_entries(copied["ik_match_fk"])

    return copied


def fresh_default_config():
    """Deep-ish copy of DEFAULT_LIMBS so edits never mutate the module constant."""
    return {
        limb_name: _copy_limb_data(limb_data)
        for limb_name, limb_data in DEFAULT_LIMBS.items()
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


def _ensure_generated_ik_match_fk_entries(pairs):
    has_handle = False
    has_pole = False

    for pair in pairs:
        if not isinstance(pair, dict):
            continue

        if pair.get("role") == "ik_handle" and pair.get("type", "offset") == "offset":
            has_handle = True

        if pair.get("role") == "pole_vector" and pair.get("type") == "pole_vector":
            has_pole = True

    if not has_handle:
        pairs.insert(0, {
            "type": "offset",
            "role": "ik_handle",
            "ik_ctrl": "",
            "source": "",
        })

    if not has_pole:
        pairs.append({
            "type": "pole_vector",
            "role": "pole_vector",
            "ik_ctrl": "",
            "shoulder_ctrl": "",
            "elbow_ctrl": "",
            "wrist_ctrl": "",
        })


def _is_nested_limb_data(value):
    return (
        isinstance(value, dict) and
        ("fk_match_ik" in value or "ik_match_fk" in value)
    )


def _migrate_flat_fk_match_ik_pairs(pairs):
    migrated = []

    for pair in pairs:
        if not isinstance(pair, dict):
            continue

        migrated_pair = dict(pair)
        migrated_pair["source"] = migrated_pair.pop("ik_ctrl", migrated_pair.get("source", ""))
        migrated_pair["use_for_pole_vector"] = bool(
            migrated_pair.get("use_for_pole_vector", False)
        )
        migrated.append(migrated_pair)

    return migrated


def _migrate_legacy_ik_controls(ik_match_fk_pairs, ik_controls_entry):
    if not isinstance(ik_controls_entry, dict):
        return

    _ensure_generated_ik_match_fk_entries(ik_match_fk_pairs)

    handle_pair = None
    pole_pair = None

    for pair in ik_match_fk_pairs:
        if not isinstance(pair, dict):
            continue

        if pair.get("role") == "ik_handle" and pair.get("type", "offset") == "offset":
            handle_pair = pair

        if pair.get("role") == "pole_vector" and pair.get("type") == "pole_vector":
            pole_pair = pair

    if handle_pair and not handle_pair.get("ik_ctrl"):
        handle_pair["ik_ctrl"] = ik_controls_entry.get("ik_ctrl", "")

    if pole_pair and not pole_pair.get("ik_ctrl"):
        pole_pair["ik_ctrl"] = ik_controls_entry.get("pole_ctrl", "")


def _normalize_ik_match_fk_pairs(pairs):
    normalized = []

    for pair in pairs:
        if not isinstance(pair, dict):
            continue

        migrated_pair = dict(pair)

        if migrated_pair.get("type", "offset") == "offset":
            migrated_pair["type"] = "offset"
            migrated_pair["source"] = migrated_pair.pop(
                "fk_ctrl",
                migrated_pair.get("source", "")
            )

        normalized.append(migrated_pair)

    offset_pairs = [
        pair for pair in normalized
        if isinstance(pair, dict) and pair.get("type", "offset") == "offset"
    ]
    pole_pairs = [
        pair for pair in normalized
        if isinstance(pair, dict) and pair.get("type") == "pole_vector"
    ]

    if offset_pairs and not any(
            pair.get("role") == "ik_handle" for pair in offset_pairs):
        offset_pairs[-1]["role"] = "ik_handle"

    if pole_pairs and not any(
            pair.get("role") == "pole_vector" for pair in pole_pairs):
        pole_pairs[0]["role"] = "pole_vector"

    _ensure_generated_ik_match_fk_entries(normalized)

    return normalized


def _ik_match_fk_is_unconfigured(pairs):
    meaningful_pairs = [
        pair for pair in pairs
        if isinstance(pair, dict) and (
            pair.get("type") == "pole_vector"
            or pair.get("type", "offset") == "offset"
        )
    ]

    if not meaningful_pairs:
        return True

    for pair in meaningful_pairs:
        if pair.get("role") == "ik_handle" and pair.get("ik_ctrl"):
            return False

        if pair.get("role") == "pole_vector" and pair.get("ik_ctrl"):
            return False

        if pair.get("role") not in ("ik_handle", "pole_vector"):
            return False

    return True


def _normalize_limb_data(limb_name, value):
    if _is_nested_limb_data(value):
        limb_data = _fresh_empty_limb_data()

        fk_match_ik = value.get("fk_match_ik", [])
        ik_match_fk = value.get("ik_match_fk", [])

        if not isinstance(fk_match_ik, list):
            raise ValueError(
                "Limb '{0}' has a non-list 'fk_match_ik' entry.".format(
                    limb_name
                )
            )

        if not isinstance(ik_match_fk, list):
            raise ValueError(
                "Limb '{0}' has a non-list 'ik_match_fk' entry.".format(
                    limb_name
                )
            )

        limb_data["fk_match_ik"] = _migrate_flat_fk_match_ik_pairs(fk_match_ik)
        limb_data["ik_match_fk"] = _normalize_ik_match_fk_pairs(ik_match_fk)

        return limb_data

    if isinstance(value, list):
        limb_data = _fresh_empty_limb_data()
        limb_data["fk_match_ik"] = _migrate_flat_fk_match_ik_pairs(value)
        return limb_data

    raise ValueError(
        "Limb '{0}' must be a list or nested limb dictionary.".format(
            limb_name
        )
    )


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
    yet / it isn't a dict. Used so save helpers can merge into whatever
    is already on disk instead of overwriting unrelated keys."""
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
    """Load the nested per-limb calibration config from JSON.

    Accepts:
    - new nested format:
        {"L_arm": {"fk_match_ik": [...], "ik_match_fk": [...]}}
    - older wrapped format:
        {"limbs": {"L_arm": [...]}}
    - older flat format:
        {"L_arm": [{"fk_ctrl": "...", "ik_ctrl": "..."}]}

    Older FK match IK pairs are migrated by renaming their "ik_ctrl"
    field to "source". Older top-level "fk_to_ik" and "ik_controls"
    data is migrated into each limb's nested "ik_match_fk" list.
    """
    if not os.path.isfile(path):
        raise IOError(
            "Calibration JSON file was not found:\n{0}".format(path)
        )

    with open(path, "r") as f:
        data = json.load(f)

    if not isinstance(data, dict):
        raise ValueError("The JSON root must be an object containing limb names.")

    raw_limbs = data.get("limbs", data)

    if not isinstance(raw_limbs, dict):
        raise ValueError("Calibration JSON does not contain a valid limbs dictionary.")

    normalized = {}

    for limb_name, value in raw_limbs.items():
        if limb_name in LEGACY_RESERVED_TOP_LEVEL_KEYS:
            continue

        normalized[limb_name] = _normalize_limb_data(
            limb_name,
            value
        )

    legacy_ik_match_fk = data.get("fk_to_ik", {})
    if legacy_ik_match_fk and not isinstance(legacy_ik_match_fk, dict):
        raise ValueError(
            "Calibration JSON's legacy 'fk_to_ik' entry must be a dictionary."
        )

    for limb_name, pairs in legacy_ik_match_fk.items():
        normalized.setdefault(
            limb_name,
            _fresh_empty_limb_data()
        )
        if _ik_match_fk_is_unconfigured(
                normalized[limb_name]["ik_match_fk"]):
            normalized[limb_name]["ik_match_fk"] = _normalize_ik_match_fk_pairs(pairs)

    legacy_ik_controls = data.get("ik_controls", {})
    if legacy_ik_controls and not isinstance(legacy_ik_controls, dict):
        raise ValueError(
            "Calibration JSON's legacy 'ik_controls' entry must be a dictionary."
        )

    for limb_name, entry in legacy_ik_controls.items():
        normalized.setdefault(
            limb_name,
            _fresh_empty_limb_data()
        )
        _migrate_legacy_ik_controls(
            normalized[limb_name]["ik_match_fk"],
            entry
        )

    return normalized


def save_limbs(limbs, path):
    """Write the nested limb config to path, preserving only the new
    reserved top-level keys (currently switch_settings). Old legacy keys
    are intentionally dropped because their data now lives in the per-limb
    nested structure."""
    data = _read_json_root(path)

    for key in list(data.keys()):
        if key not in RESERVED_TOP_LEVEL_KEYS:
            del data[key]

    for limb_name, limb_data in limbs.items():
        data[limb_name] = _copy_limb_data(limb_data)

    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def load_fk_match_ik_pairs(path):
    """Load just the FK match IK pairs, keyed by limb name."""
    limbs = load_limbs(path)

    return {
        limb_name: _copy_pairs(limb_data.get("fk_match_ik", []))
        for limb_name, limb_data in limbs.items()
    }


def save_fk_match_ik_pairs(fk_match_ik_pairs, path):
    """Write only the FK match IK lists into the nested limb structure,
    preserving each limb's IK match FK data and top-level switch settings."""
    current = load_limbs(path) if os.path.isfile(path) else {}

    for limb_name, pairs in fk_match_ik_pairs.items():
        current.setdefault(limb_name, _fresh_empty_limb_data())
        current[limb_name]["fk_match_ik"] = _copy_pairs(pairs)

    save_limbs(current, path)


def load_ik_match_fk_pairs(path):
    """Load just the IK match FK pairs, keyed by limb name."""
    limbs = load_limbs(path)

    return {
        limb_name: _copy_pairs(limb_data.get("ik_match_fk", []))
        for limb_name, limb_data in limbs.items()
    }


def save_ik_match_fk_pairs(ik_match_fk_pairs, path):
    """Write only the IK match FK lists into the nested limb structure,
    preserving each limb's FK match IK data and top-level switch settings."""
    current = load_limbs(path) if os.path.isfile(path) else {}

    for limb_name, pairs in ik_match_fk_pairs.items():
        current.setdefault(limb_name, _fresh_empty_limb_data())
        current[limb_name]["ik_match_fk"] = _copy_pairs(pairs)
        _ensure_generated_ik_match_fk_entries(
            current[limb_name]["ik_match_fk"]
        )

    save_limbs(current, path)


def load_switch_settings(path):
    """Load the per-limb switch attribute settings (object, attr_name,
    ik_value, fk_value) from the calibration JSON's "switch_settings"
    key. Falls back to DEFAULT_SWITCH_SETTINGS if the file has no such
    key yet."""
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
    "switch_settings" key, preserving the nested limb config."""
    data = _read_json_root(path)
    data["switch_settings"] = switch_settings

    limbs = {}
    if os.path.isfile(path):
        try:
            limbs = load_limbs(path)
        except Exception:
            limbs = {}

    for key in list(data.keys()):
        if key not in RESERVED_TOP_LEVEL_KEYS:
            del data[key]

    data["switch_settings"] = switch_settings

    for limb_name, limb_data in limbs.items():
        data[limb_name] = _copy_limb_data(limb_data)

    with open(path, "w") as f:
        json.dump(data, f, indent=2)
