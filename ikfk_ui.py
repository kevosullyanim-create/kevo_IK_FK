"""
ikfk_ui.py

Window, tabs, table, and callbacks. Everything here is thin glue over
ikfk_io / ikfk_calibrate / ikfk_switch_ik_to_fk / ikfk_switch_fk_to_ik -
if you're debugging pose or offset math, you almost certainly want one
of those modules instead.
"""
import os

import maya.cmds as cmds

from ikfk_io import (
    DEFAULT_JSON_PATH,
    error_dialog,
    fresh_default_config,
    get_scene_namespaces,
    get_selected_channel,
    get_selected_short_name,
    load_ik_controls,
    load_limbs,
    load_switch_settings,
    save_fk_to_ik_limbs,
    save_ik_controls,
    save_limbs,
    save_switch_settings,
)
from ikfk_calibrate import (
    build_calibration_locators,
    delete_calibration_locators,
    validate_calibration_pairs,
    validate_ik_controls,
    generate_fk_to_ik_pairs,
)

from ikfk_switch_ik_to_fk import snap_ik_to_fk
from ikfk_switch_fk_to_ik import snap_fk_to_ik

WINDOW_NAME = "ikfkToolUI"

# ---------------------------------------------------------------------------
# UI state (module-level, one window at a time)
# ---------------------------------------------------------------------------
_config = {}
_config_path = DEFAULT_JSON_PATH
_namespace_menu = None
_warning_label = None
_path_field = None
_table_layout = None

# Limb-frame collapse state, kept across _rebuild_table() calls so a
# limb the user closed doesn't pop back open every time a Set/Clear/
# Add/Remove button triggers a rebuild. Keyed by limb name.
_collapsed_state = {}
# Current frameLayout control name per limb, refreshed every rebuild -
# used to read collapse state back out just before the layout it
# belongs to is deleted.
_limb_frames = {}

# IK/FK tab
_switch_namespace_menu = None
_ikfk_path_field = None
_ikfk_warning_label = None
_key_before_cb = None
_key_after_cb = None
_ikfk_buttons_column = None

# Switch settings (object/attr_name/ik_value/fk_value per limb),
# loaded from the calibration JSON's "switch_settings" block. This
# drives which per-limb buttons appear on the IK/FK tab.
#
# Entries are populated by picking an attribute in the Channel Box
# and pressing Set on the limb's "Switch Attribute" row (Calibration
# tab) - see _set_switch_channel()/_clear_switch_channel() below. A
# limb with no entry here simply gets no button on the IK/FK tab,
# rather than falling back to a guessed value.
_switch_settings = {}

# Real FK -> IK controls per limb (the IK handle and pole vector
# control actually driven by a FK -> IK snap) - set via Set/Clear
# from viewport selection, same idea as the pairs' fk_ctrl/ik_ctrl
# rows but one value per limb rather than one per pair. Kept separate
# from _config because a limb has exactly one of each, not one per
# pair, and separate from _switch_settings because it's unrelated
# data that happens to follow the same per-limb Set/Clear pattern.
#
# Entries are populated via the "FK -> IK Controls" section on the
# Calibration tab (_set_limb_control()/_clear_limb_control() below).
# Loaded from / saved to the calibration JSON's "ik_controls" key
# (see load_ik_controls()/save_ik_controls() in ikfk_io.py).
_ik_controls = {}

# Live pole vector distance per limb, entered as a field next to each
# limb's FK -> IK snap button. Deliberately NOT persisted to JSON -
# always read fresh from the field at snap time. Per-limb because the
# sign/magnitude depends on the limb's bend direction (e.g. a knee
# bends the opposite way relative to hip/knee/ankle compared to how
# an elbow bends relative to shoulder/elbow/wrist, so arms and legs
# need opposite-sign values).
_pole_distance_values = {}  # limb_name -> float, session-only
_pole_distance_fields = {}  # limb_name -> floatField control, rebuilt each _rebuild_ikfk_buttons() call


# Consistent with the Complete/Incomplete/Empty pair colours in the
# Calibration tab's table.
_COLOUR_OK = (0.30, 0.45, 0.30)
_COLOUR_WARN = (0.48, 0.25, 0.25)


def _on_pole_distance_changed(limb_name, *_args):
    field = _pole_distance_fields.get(limb_name)
    if field:
        _pole_distance_values[limb_name] = cmds.floatField(
            field, query=True, value=True
        )



def _sync_path_fields():
    """Update both tabs' read-only path display to the current
    _config_path, and colour-flag the IK/FK tab's copy red when there
    is no calibration file to snap from."""
    display_text = _config_path or "(unsaved)"

    if _path_field:
        cmds.textField(_path_field, edit=True, text=display_text)

    if _ikfk_path_field:
        if _config_path:
            cmds.textField(
                _ikfk_path_field,
                edit=True,
                text=display_text,
                enableBackground=True,
                backgroundColor=_COLOUR_OK
            )

        else:
            cmds.textField(
                _ikfk_path_field,
                edit=True,
                text="(no calibration file loaded)",
                enableBackground=True,
                backgroundColor=_COLOUR_WARN
            )

    if _ikfk_warning_label:
        no_file = not _config_path

        cmds.text(
            _ikfk_warning_label,
            edit=True,
            visible=no_file,
            label=(
                "Warning: no calibration file loaded. Load or save one "
                "on the Calibration tab before snapping."
            ) if no_file else ""
        )


def _refresh_namespaces(*_args):
    """Refresh the Calibration tab's namespace menu and the IK/FK
    tab's namespace menu together, since they all read from the
    same scene state."""
    namespaces = get_scene_namespaces()

    warn_text = ""
    if len(namespaces) > 2:
        warn_text = ("Warning: {0} namespaces found in scene ({1}). "
                      "Expected one rig.").format(len(namespaces), ", ".join(namespaces))
    elif len(namespaces) == 0:
        warn_text = "Warning: no namespaces found in scene."

    cmds.text(_warning_label, edit=True, label=warn_text, visible=bool(warn_text))

    current_ns = None
    if cmds.optionMenu(_namespace_menu, query=True, itemListLong=True):
        current_ns = cmds.optionMenu(_namespace_menu, query=True, value=True)

    for item in cmds.optionMenu(_namespace_menu, query=True, itemListLong=True) or []:
        cmds.deleteUI(item)
    for ns in namespaces:
        cmds.menuItem(label=ns, parent=_namespace_menu)

    if current_ns in namespaces:
        cmds.optionMenu(_namespace_menu, edit=True, value=current_ns)
    elif namespaces:
        cmds.optionMenu(_namespace_menu, edit=True, value=namespaces[0])

    # IK/FK tab's namespace menu.
    current_switch_ns = None
    if _switch_namespace_menu and cmds.optionMenu(_switch_namespace_menu, query=True, itemListLong=True):
        current_switch_ns = cmds.optionMenu(_switch_namespace_menu, query=True, value=True)

    if _switch_namespace_menu:
        for item in cmds.optionMenu(_switch_namespace_menu, query=True, itemListLong=True) or []:
            cmds.deleteUI(item)
        for ns in namespaces:
            cmds.menuItem(label=ns, parent=_switch_namespace_menu)

        if current_switch_ns in namespaces:
            cmds.optionMenu(_switch_namespace_menu, edit=True, value=current_switch_ns)
        elif namespaces:
            cmds.optionMenu(_switch_namespace_menu, edit=True, value=namespaces[0])


# ---------------------------------------------------------------------------
# Calibration tab: table
# ---------------------------------------------------------------------------
def _set_field(limb, index, key, *_args):
    value = get_selected_short_name()
    if value is None:
        return
    _config[limb][index][key] = value
    _rebuild_table()


def _clear_field(limb, index, key, *_args):
    _config[limb][index][key] = ""
    _rebuild_table()


def _set_limb_control(limb, key, *_args):
    """Set one value (ik_ctrl or pole_ctrl) in _ik_controls[limb] from
    the current viewport selection. Same idea as _set_field, but for
    a value stored once per limb rather than once per pair."""
    value = get_selected_short_name()
    if value is None:
        return
    entry = _ik_controls.get(limb, {})
    entry[key] = value
    _ik_controls[limb] = entry
    _rebuild_table()


def _clear_limb_control(limb, key, *_args):
    """Clear one value (ik_ctrl or pole_ctrl) in _ik_controls[limb]."""
    entry = _ik_controls.get(limb, {})
    entry[key] = ""
    _ik_controls[limb] = entry
    _rebuild_table()


def _set_switch_channel(limb, *_args):
    """Read the Channel Box's currently highlighted attribute and
    store it as this limb's switch attribute, preserving any existing
    ik_value/fk_value (or defaulting to 0/1 for a brand new entry)."""
    result = get_selected_channel()
    if result is None:
        return

    obj, attr_name = result
    existing = _switch_settings.get(limb, {})

    _switch_settings[limb] = {
        "object": obj,
        "attr_name": attr_name,
        "ik_value": existing.get("ik_value", 0),
        "fk_value": existing.get("fk_value", 1),
    }

    _rebuild_table()
    _rebuild_ikfk_buttons()


def _clear_switch_channel(limb, *_args):
    """Remove this limb's switch attribute entirely - the limb loses
    its IK/FK tab button until a new one is set."""
    _switch_settings.pop(limb, None)
    _rebuild_table()
    _rebuild_ikfk_buttons()


def _remove_pair(limb, index, *_args):
    del _config[limb][index]
    _rebuild_table()


def _remove_limb(limb, *_args):
    del _config[limb]
    _collapsed_state.pop(limb, None)
    _limb_frames.pop(limb, None)
    # Also remove from switch settings and real IK controls when
    # removing a limb.
    _switch_settings.pop(limb, None)
    _ik_controls.pop(limb, None)
    _rebuild_table()
    _rebuild_ikfk_buttons()


def _add_pair(limb, *_args):
    _config[limb].append({"fk_ctrl": "", "ik_ctrl": ""})
    _rebuild_table()


def _add_limb(*_args):
    result = cmds.promptDialog(
        title="Add Limb", message="Limb name:", button=["Add", "Cancel"],
        defaultButton="Add", cancelButton="Cancel", dismissString="Cancel")
    if result != "Add":
        return
    name = cmds.promptDialog(query=True, text=True).strip()
    if not name:
        return
    if name in _config:
        cmds.warning("Limb '{0}' already exists.".format(name))
        return

    _config[name] = []

    # Switch attribute and real IK controls are intentionally left
    # unset here - pick them via selection/Channel Box (Set buttons)
    # rather than guessing from a naming convention. The limb has no
    # IK/FK tab button until a switch attribute is set.

    _rebuild_table()
    _rebuild_ikfk_buttons()


def _build_field_row(parent, label, value, limb_name, idx, key):
    """One labeled row with a read-only textField + Set/Clear buttons.
    Used for both the 'FK Control' and 'IK Control' rows on a pair."""
    cmds.text(
        label=label,
        align="left",
        parent=parent
    )

    row = cmds.rowLayout(
        numberOfColumns=3,
        adjustableColumn=1,
        columnWidth=[
            (2, 50),
            (3, 50)
        ],
        columnAttach=[
            (1, "both", 0),
            (2, "both", 3),
            (3, "both", 3)
        ],
        parent=parent
    )

    cmds.textField(
        text=value,
        editable=False,
        annotation=label,
        parent=row
    )

    cmds.button(
        label="Set",
        command=lambda *_args, l=limb_name, i=idx, k=key:
            _set_field(l, i, k),
        parent=row
    )

    cmds.button(
        label="Clear",
        command=lambda *_args, l=limb_name, i=idx, k=key:
            _clear_field(l, i, k),
        parent=row
    )


def _build_limb_field_row(parent, label, value, limb_name, key):
    """Like _build_field_row, but for a value stored once per limb (in
    _ik_controls) rather than once per pair (in _config). Used for the
    real FK -> IK IK Control and Pole Vector Control rows."""
    cmds.text(
        label=label,
        align="left",
        parent=parent
    )

    row = cmds.rowLayout(
        numberOfColumns=3,
        adjustableColumn=1,
        columnWidth=[
            (2, 50),
            (3, 50)
        ],
        columnAttach=[
            (1, "both", 0),
            (2, "both", 3),
            (3, "both", 3)
        ],
        parent=parent
    )

    cmds.textField(
        text=value,
        editable=False,
        annotation=label,
        parent=row
    )

    cmds.button(
        label="Set",
        command=lambda *_args, l=limb_name, k=key:
            _set_limb_control(l, k),
        parent=row
    )

    cmds.button(
        label="Clear",
        command=lambda *_args, l=limb_name, k=key:
            _clear_limb_control(l, k),
        parent=row
    )


def _button_pair_row(parent, buttons):
    """A row of buttons, e.g. (Add Pair/Remove Limb).
    buttons is a list of (label, command) tuples."""
    row = cmds.rowLayout(
        numberOfColumns=len(buttons),
        adjustableColumn=1,
        columnWidth=[(i + 1, 120) for i in range(1, len(buttons))],
        columnAttach=[(i + 1, "both", 2) for i in range(len(buttons))],
        parent=parent
    )

    for label, command in buttons:
        cmds.button(label=label, command=command, parent=row)

    return row


def _on_limb_collapse_changed(limb_name, *_args):
    """Record the limb frame's current collapse state so the next
    _rebuild_table() call can restore it instead of defaulting back
    open."""
    frame_ctrl = _limb_frames.get(limb_name)

    if frame_ctrl and cmds.frameLayout(frame_ctrl, exists=True):
        _collapsed_state[limb_name] = cmds.frameLayout(
            frame_ctrl,
            query=True,
            collapse=True
        )


def _rebuild_table(*_args):
    # Capture the current collapse state of every limb frame before
    # wiping the layout - otherwise every Set/Clear/Add/Remove action
    # (which all call this) pops every limb frame back open.
    for limb_name, frame_ctrl in list(_limb_frames.items()):
        if cmds.frameLayout(frame_ctrl, exists=True):
            _collapsed_state[limb_name] = cmds.frameLayout(
                frame_ctrl,
                query=True,
                collapse=True
            )

    # Clear the existing contents.
    for child in cmds.layout(
        _table_layout,
        query=True,
        childArray=True
    ) or []:
        cmds.deleteUI(child)

    _limb_frames.clear()

    cmds.setParent(_table_layout)

    for limb_name, pairs in _config.items():

        limb_frame = cmds.frameLayout(
            label=limb_name,
            collapsable=True,
            collapse=_collapsed_state.get(limb_name, False),
            collapseCommand=lambda *_args, l=limb_name: _on_limb_collapse_changed(l),
            expandCommand=lambda *_args, l=limb_name: _on_limb_collapse_changed(l),
            marginWidth=8,
            marginHeight=8,
            parent=_table_layout
        )

        _limb_frames[limb_name] = limb_frame

        limb_column = cmds.columnLayout(
            adjustableColumn=True,
            rowSpacing=8,
            parent=limb_frame
        )

        if not pairs:
            cmds.text(
                label="No calibration pairs.",
                align="left",
                parent=limb_column
            )

        for idx, pair in enumerate(pairs):
            fk_value = pair.get("fk_ctrl", "").strip()
            ik_value = pair.get("ik_ctrl", "").strip()

            if fk_value and ik_value:
                pair_colour = (0.30, 0.45, 0.30)
                status_text = "Complete"
            elif fk_value or ik_value:
                pair_colour = (0.50, 0.42, 0.20)
                status_text = "Incomplete"
            else:
                pair_colour = (0.48, 0.25, 0.25)
                status_text = "Empty"

            pair_frame = cmds.frameLayout(
                label="",
                labelVisible=False,
                collapsable=False,
                marginWidth=6,
                marginHeight=6,
                parent=limb_column
            )

            pair_column = cmds.columnLayout(
                adjustableColumn=True,
                rowSpacing=5,
                parent=pair_frame
            )

            header_row = cmds.rowLayout(
                numberOfColumns=2,
                adjustableColumn=1,
                columnWidth=[(2, 60)],
                columnAttach=[
                    (1, "both", 0),
                    (2, "both", 3)
                ],
                parent=pair_column
            )

            cmds.text(
                label="Pair {0} - {1}".format(idx + 1, status_text),
                align="left",
                font="boldLabelFont",
                enableBackground=True,
                backgroundColor=pair_colour,
                parent=header_row
            )

            cmds.button(
                label="Delete",
                command=lambda *_args, l=limb_name, i=idx:
                    _remove_pair(l, i),
                parent=header_row
            )

            cmds.setParent(pair_column)

            _build_field_row(
                pair_column, "FK Control", fk_value,
                limb_name, idx, "fk_ctrl"
            )

            _build_field_row(
                pair_column, "IK Control", ik_value,
                limb_name, idx, "ik_ctrl"
            )

        # ----------------------------------------------------------
        # FK -> IK controls (real IK handle + pole vector control)
        # ----------------------------------------------------------
        cmds.separator(
            height=6,
            style="none",
            parent=limb_column
        )

        cmds.text(
            label="FK -> IK Controls",
            align="left",
            font="boldLabelFont",
            parent=limb_column
        )

        limb_controls = _ik_controls.get(limb_name, {})

        _build_limb_field_row(
            limb_column, "IK Control", limb_controls.get("ik_ctrl", ""),
            limb_name, "ik_ctrl"
        )

        _build_limb_field_row(
            limb_column, "Pole Vector Control", limb_controls.get("pole_ctrl", ""),
            limb_name, "pole_ctrl"
        )

        # ----------------------------------------------------------
        # Switch attribute
        # ----------------------------------------------------------
        cmds.separator(
            height=6,
            style="none",
            parent=limb_column
        )

        switch_data = _switch_settings.get(limb_name)

        switch_display = (
            "{0}.{1}".format(switch_data["object"], switch_data["attr_name"])
            if switch_data else "(not set - select in Channel Box)"
        )

        cmds.text(
            label="Switch Attribute",
            align="left",
            parent=limb_column
        )

        switch_row = cmds.rowLayout(
            numberOfColumns=3,
            adjustableColumn=1,
            columnWidth=[
                (2, 50),
                (3, 50)
            ],
            columnAttach=[
                (1, "both", 0),
                (2, "both", 3),
                (3, "both", 3)
            ],
            parent=limb_column
        )

        cmds.textField(
            text=switch_display,
            editable=False,
            annotation="Switch Attribute",
            parent=switch_row
        )

        cmds.button(
            label="Set",
            command=lambda *_args, l=limb_name: _set_switch_channel(l),
            parent=switch_row
        )

        cmds.button(
            label="Clear",
            command=lambda *_args, l=limb_name: _clear_switch_channel(l),
            parent=switch_row
        )

        cmds.setParent(limb_column)

        # ----------------------------------------------------------
        # Limb controls
        # ----------------------------------------------------------
        cmds.separator(
            height=6,
            style="none",
            parent=limb_column
        )

        _button_pair_row(
            limb_column,
            [
                ("Add Pair", lambda *_args, l=limb_name: _add_pair(l)),
                ("Remove Limb", lambda *_args, l=limb_name: _remove_limb(l)),
            ]
        )

    cmds.setParent("..")


# ---------------------------------------------------------------------------
# IK/FK tab: rebuild buttons
# ---------------------------------------------------------------------------
def _rebuild_ikfk_buttons(*_args):
    """Rebuild the per-limb snap buttons on the IK/FK tab based on current
    _switch_settings. Called whenever switch settings change."""
    if not _ikfk_buttons_column:
        return
    
    # Delete all children from the "Snap IK -> FK" section onwards.
    # Identify this by finding the last separator (the one before the buttons section)
    # and delete everything after it.
    children = cmds.layout(_ikfk_buttons_column, query=True, childArray=True) or []
    
    # Find the index of the LAST separator before our button content.
    # Separators mark structural boundaries in the UI.
    last_separator_index = -1
    for i, child in enumerate(children):
        if cmds.objExists(child):
            try:
                # Check if this is a separator by trying to query it as one
                if cmds.separator(child, query=True, exists=True):
                    last_separator_index = i
            except:
                pass
    
    # Delete everything after the last separator
    if last_separator_index >= 0:
        for child_to_delete in children[last_separator_index + 1:]:
            if cmds.objExists(child_to_delete):
                cmds.deleteUI(child_to_delete)
    
    # Only rebuild button section if there are switch settings
    if not _switch_settings:
        return
    
    # Rebuild the buttons section
    cmds.setParent(_ikfk_buttons_column)
    
    cmds.separator(height=8, style="in")
    
    cmds.text(
        label="Snap IK -> FK, per limb:",
        align="left",
        font="boldLabelFont"
    )

    for limb_name in sorted(_switch_settings.keys()):
        cmds.button(
            label="{0} FK match IK".format(limb_name),
            command=lambda *_args, l=limb_name: _do_snap(l, "ik_to_fk"),
            height=28
        )

    cmds.button(
        label="All Limbs FK Match IK",
        height=32,
        command=lambda *_args: _do_snap_all("ik_to_fk")
    )

    cmds.separator(height=8, style="in")

    cmds.text(
        label="Snap FK -> IK, per limb:",
        align="left",
        font="boldLabelFont"
    )


        row = cmds.rowLayout(
            numberOfColumns=3,
            adjustableColumn=1,
            columnWidth=[(2, 60), (3, 130)],
            columnAttach=[(1, "both", 0), (2, "both", 3), (3, "both", 3)]
        )

        cmds.button(
            label="{0} IK match FK".format(limb_name),
            command=lambda *_args, l=limb_name: _do_snap(l, "fk_to_ik"),
            height=28,
            parent=row
        )

        cmds.text(label="Pole Dist:", align="left", parent=row)

        field = cmds.floatField(
            value=_pole_distance_values.get(limb_name, -25.0),
            changeCommand=lambda *_args, l=limb_name: _on_pole_distance_changed(l),
            parent=row
        )

        _pole_distance_fields[limb_name] = field

        cmds.setParent(_ikfk_buttons_column)

    cmds.button(
        label="All Limbs IK Match FK",
        height=32,
        command=lambda *_args: _do_snap_all("fk_to_ik")
    )


# ---------------------------------------------------------------------------
# Calibration tab: Load / Save / Build
# ---------------------------------------------------------------------------
def _do_load(*_args):
    global _config, _config_path, _switch_settings, _ik_controls

    result = cmds.fileDialog2(
        fileMode=1,
        caption="Load Calibration Pairs JSON",
        fileFilter="JSON (*.json)"
    )

    if not result:
        return

    path = result[0]

    try:
        loaded_config = load_limbs(path)
        loaded_switch_settings = load_switch_settings(path)
        loaded_ik_controls = load_ik_controls(path)

    except Exception as exc:
        error_dialog("Load Failed", exc)
        return

    _config = loaded_config
    _config_path = path
    _switch_settings = loaded_switch_settings
    _ik_controls = loaded_ik_controls

    # Collapse state is tied to limb names in the previous file - stale
    # entries are harmless (just unused keys) but start clean so a
    # limb name reused across files doesn't inherit an unrelated state.
    _collapsed_state.clear()
    _limb_frames.clear()

    _sync_path_fields()
    _rebuild_table()
    _rebuild_ikfk_buttons()


def _do_save(save_as=False, *_args):
    global _config_path

    path = _config_path

    if save_as or not path:
        result = cmds.fileDialog2(
            fileMode=0,
            caption="Save Calibration Pairs JSON",
            fileFilter="JSON (*.json)"
        )

        if not result:
            return

        path = result[0]

        if not path.lower().endswith(".json"):
            path += ".json"

    try:
        save_limbs(_config, path)
        # Always write the current switch settings and real IK
        # controls alongside the limb pairs, so the file is a
        # complete, self-contained record that a future session (or
        # the IK/FK tab on reload) can read from, rather than relying
        # on the DEFAULT_SWITCH_SETTINGS fallback in ikfk_io.py.
        save_switch_settings(_switch_settings, path)
        save_ik_controls(_ik_controls, path)

    except Exception as exc:
        error_dialog("Save Failed", exc)
        return

    _config_path = path

    _sync_path_fields()

    cmds.inViewMessage(
        amg="Saved calibration pairs to <hl>{0}</hl>".format(path),
        pos="topCenter",
        fade=True
    )


def _do_build(*_args):
    namespace = cmds.optionMenu(
        _namespace_menu,
        query=True,
        value=True
    )

    # --------------------------------------------------------------
    # Check namespace selection
    # --------------------------------------------------------------
    if not namespace:
        cmds.confirmDialog(
            title="Missing Selection",
            message="Pick a namespace.",
            button=["OK"]
        )
        return

    # --------------------------------------------------------------
    # Validate all configured pairs before building
    # --------------------------------------------------------------
    incomplete, missing = validate_calibration_pairs(
        _config,
        namespace
    )

    problems = []

    if incomplete:
        problems.append(
            "Incomplete pairs:\n{0}".format(
                "\n".join(incomplete)
            )
        )

    if missing:
        problems.append(
            "Objects not found:\n{0}".format(
                "\n".join(missing)
            )
        )

    ik_control_problems = validate_ik_controls(_config, _ik_controls)

    if ik_control_problems:
        problems.append(
            "Incomplete FK -> IK Controls:\n{0}".format(
                "\n".join(ik_control_problems)
            )
        )

    if problems:
        error_dialog("Cannot Build", "\n\n".join(problems))
        return

    # --------------------------------------------------------------
    # Build locators and capture offset data
    # --------------------------------------------------------------
    try:
        created = build_calibration_locators(
            _config,
            namespace
        )

    except Exception as exc:
        error_dialog("Build Failed", exc)

        # Keep this while troubleshooting for the full traceback.
        raise

    try:
        fk_to_ik_config = generate_fk_to_ik_pairs(
            _config,
            _ik_controls,
            namespace
        )

    except Exception as exc:
        error_dialog("FK -> IK Generation Failed", exc)
        raise

    # --------------------------------------------------------------
    # Save the updated configuration, including offset_rotate values
    # --------------------------------------------------------------
    save_succeeded = False

    if _config_path:
        try:
            save_limbs(
                _config,
                _config_path
            )

            save_switch_settings(
                _switch_settings,
                _config_path
            )

            save_ik_controls(
                _ik_controls,
                _config_path
            )
            
            save_fk_to_ik_limbs(
                fk_to_ik_config,
                _config_path
            )
            
            save_succeeded = True

            print(
                "Calibration offsets saved to: {0}".format(
                    _config_path
                )
            )

            # NOTE: known bug (flagged, not yet fixed) - a verification
            # read-back used to live in the "no path" branch below,
            # where _config_path is always falsy, so it could never
            # actually verify anything. If you want a real verify
            # step, it belongs here, e.g.:
            #     verification = load_limbs(_config_path)
            #     print("  [VERIFY] Saved config: {0}".format(verification))

        except Exception as exc:
            error_dialog(
                "Offset Save Failed",
                "The locators were built successfully, but the "
                "offset data could not be saved. The CALIB locators "
                "have been left in the scene.\n\n{0}".format(exc)
            )

    else:
        error_dialog(
            "Offsets Not Saved",
            "The locators were built and the offsets were recorded "
            "in the tool, but there is no current JSON file path.\n\n"
            "Use Save As to save the offset data. The CALIB locators "
            "have been left in the scene until the offsets are saved."
        )

    # --------------------------------------------------------------
    # Clean up the temporary locators, but only once the offsets are
    # safely written to JSON - if the save failed or there's no path
    # yet, leave them in the scene so nothing is lost.
    # --------------------------------------------------------------
    deleted_count = 0

    if save_succeeded:
        deleted_count = delete_calibration_locators(created)

    # Refresh the table in case offset information is displayed later.
    _rebuild_table()

    # --------------------------------------------------------------
    # Completion message
    # --------------------------------------------------------------
    if save_succeeded:
        message = (
            "Built <hl>{0}</hl> calibration locator pairs, saved, "
            "and cleaned up <hl>{1}</hl> temporary locators."
        ).format(len(created), deleted_count)
    else:
        message = (
            "Built <hl>{0}</hl> calibration locator pairs."
        ).format(len(created))

    cmds.inViewMessage(
        amg=message,
        pos="topCenter",
        fade=True
    )


# ---------------------------------------------------------------------------
# IK/FK tab: snap actions
# ---------------------------------------------------------------------------
def _on_key_before_changed(*_args):
    """key_before/key_after are mutually exclusive - checking one
    clears the other."""
    if cmds.checkBox(_key_before_cb, query=True, value=True):
        cmds.checkBox(_key_after_cb, edit=True, value=False)


def _on_key_after_changed(*_args):
    """key_before/key_after are mutually exclusive - checking one
    clears the other."""
    if cmds.checkBox(_key_after_cb, query=True, value=True):
        cmds.checkBox(_key_before_cb, edit=True, value=False)


_SNAP_FUNCTIONS = {
    "ik_to_fk": snap_ik_to_fk,
    "fk_to_ik": snap_fk_to_ik,
}

_SNAP_LABELS = {
    "ik_to_fk": "IK -> FK",
    "fk_to_ik": "FK -> IK",
}


def _do_snap(limb_name, direction, *_args):
    namespace = None

    if _switch_namespace_menu and cmds.optionMenu(_switch_namespace_menu, query=True, itemListLong=True):
        namespace = cmds.optionMenu(_switch_namespace_menu, query=True, value=True)

    if not namespace:
        error_dialog(
            "Missing Namespace",
            "Pick a namespace to snap on the IK / FK tab."
        )
        return

    key_before = cmds.checkBox(_key_before_cb, query=True, value=True)
    key_after = cmds.checkBox(_key_after_cb, query=True, value=True)

    snap_func = _SNAP_FUNCTIONS[direction]

    kwargs = dict(
        namespace=namespace,
        limb_name=limb_name,
        calibration_path=_config_path,
        key_before=key_before,
        key_after=key_after
    )
    if direction == "fk_to_ik":
        kwargs["pole_distance"] = _pole_distance_values.get(limb_name, -25.0)

    try:
        snap_func(**kwargs)

    except Exception as exc:
        error_dialog("Snap Failed", exc)
        return

    cmds.inViewMessage(
        amg="Snapped <hl>{0}</hl> {1} on <hl>{2}</hl>.".format(
            limb_name, _SNAP_LABELS[direction], namespace
        ),
        pos="topCenter",
        fade=True
    )


def _do_snap_all(direction, *_args):
    """Convenience: snap every limb that has switch settings, in
    order, in the given direction. Stops and reports on the first
    failure rather than partially applying the rest silently."""
    snap_func = _SNAP_FUNCTIONS[direction]

    for limb_name in sorted(_switch_settings.keys()):
        namespace = None

        if _switch_namespace_menu and cmds.optionMenu(_switch_namespace_menu, query=True, itemListLong=True):
            namespace = cmds.optionMenu(_switch_namespace_menu, query=True, value=True)

        if not namespace:
            error_dialog(
                "Missing Namespace",
                "Pick a namespace to snap on the IK / FK tab."
            )
            return

        key_before = cmds.checkBox(_key_before_cb, query=True, value=True)
        key_after = cmds.checkBox(_key_after_cb, query=True, value=True)

        kwargs = dict(
            namespace=namespace,
            limb_name=limb_name,
            calibration_path=_config_path,
            key_before=key_before,
            key_after=key_after
        )
        if direction == "fk_to_ik":
            kwargs["pole_distance"] = _pole_distance_values.get(limb_name, -25.0)
        try:
            snap_func(**kwargs)

        except Exception as exc:
            error_dialog(
                "Snap All Stopped",
                "Failed on limb '{0}':\n\n{1}".format(limb_name, exc)
            )
            return

    cmds.inViewMessage(
        amg="Snapped all limbs {0}.".format(_SNAP_LABELS[direction]),
        pos="topCenter",
        fade=True
    )


# ---------------------------------------------------------------------------
# Window
# ---------------------------------------------------------------------------
def _build_calibration_tab(parent):
    global _namespace_menu, _warning_label, _path_field, _table_layout

    calib_form = cmds.formLayout(parent=parent)

    top_column = cmds.columnLayout(
        adjustableColumn=True,
        rowSpacing=8,
        columnAttach=("both", 12),
        parent=calib_form
    )

    cmds.text(label="", height=2)

    cmds.text(
        label="Namespace (single rig):",
        align="left"
    )

    _namespace_menu = cmds.optionMenu()

    _warning_label = cmds.text(
        label="",
        align="left",
        visible=False,
        wordWrap=True,
        height=32
    )

    cmds.button(
        label="Refresh Namespaces",
        command=_refresh_namespaces
    )

    cmds.separator(height=8, style="in")

    cmds.text(
        label="Calibration pairs file:",
        align="left"
    )

    cmds.rowLayout(
        numberOfColumns=3,
        adjustableColumn=1,
        columnWidth=[
            (2, 60),
            (3, 60)
        ]
    )

    _path_field = cmds.textField(
        text=_config_path or "(unsaved)",
        editable=False
    )

    cmds.button(
        label="Load",
        command=_do_load
    )

    cmds.button(
        label="Save",
        command=lambda *_args: _do_save(False)
    )

    cmds.setParent(top_column)

    cmds.button(
        label="Save As...",
        command=lambda *_args: _do_save(True)
    )

    cmds.separator(height=8, style="in")

    cmds.text(
        label=(
            "Select an object in the viewport, then press Set on the "
            "row where you want it stored. For the switch attribute, "
            "highlight it in the Channel Box first, then press Set."
        ),
        align="left",
        wordWrap=True
    )

    cmds.button(
        label="Add Limb",
        command=_add_limb
    )

    bottom_column = cmds.columnLayout(
        adjustableColumn=True,
        rowSpacing=6,
        columnAttach=("both", 12),
        parent=calib_form
    )

    cmds.separator(height=8, style="in")

    cmds.button(
        label="Build Locators",
        height=32,
        command=_do_build
    )

    cmds.text(label="", height=4)

    _table_layout = cmds.scrollLayout(
        childResizable=True,
        parent=calib_form
    )

    cmds.formLayout(
        calib_form,
        edit=True,
        attachForm=[
            (top_column, "top", 0),
            (top_column, "left", 0),
            (top_column, "right", 0),

            (_table_layout, "left", 12),
            (_table_layout, "right", 12),

            (bottom_column, "left", 0),
            (bottom_column, "right", 0),
            (bottom_column, "bottom", 0),
        ],
        attachControl=[
            (_table_layout, "top", 8, top_column),
            (_table_layout, "bottom", 8, bottom_column),
        ]
    )

    return calib_form


def _build_ikfk_tab(parent):
    global _switch_namespace_menu, _ikfk_path_field, _ikfk_warning_label
    global _key_before_cb, _key_after_cb, _ikfk_buttons_column

    ikfk_scroll = cmds.scrollLayout(
        childResizable=True,
        parent=parent
    )
    
    _ikfk_buttons_column = cmds.columnLayout(
        adjustableColumn=True,
        rowSpacing=10,
        columnAttach=("both", 12),
        parent=ikfk_scroll
    )

    cmds.text(label="", height=2)

    cmds.text(
        label="Calibration file (shared with Calibration tab):",
        align="left"
    )

    _ikfk_path_field = cmds.textField(
        text=_config_path or "(unsaved)",
        editable=False
    )

    _ikfk_warning_label = cmds.text(
        label="",
        align="left",
        visible=False,
        wordWrap=True,
        height=32
    )

    cmds.separator(height=8, style="in")

    cmds.text(
        label="Namespace to snap:",
        align="left"
    )

    _switch_namespace_menu = cmds.optionMenu()

    cmds.button(
        label="Refresh Namespaces",
        command=_refresh_namespaces
    )

    cmds.separator(height=8, style="in")

    _key_before_cb = cmds.checkBox(
        label="Key IK/FK switch before current frame",
        value=False,
        changeCommand=_on_key_before_changed
    )

    _key_after_cb = cmds.checkBox(
        label="Key IK/FK switch after current frame",
        value=False,
        changeCommand=_on_key_after_changed
    )

    cmds.text(
        label=(
            "Before/after are mutually exclusive - checking one clears "
            "the other. FK controls are always keyed on snap, "
            "regardless of these two options - those only affect the "
            "switch attribute. With neither ticked, the switch is left "
            "at whatever value it started at."
        ),
        align="left",
        wordWrap=True
    )

    return ikfk_scroll


def show_ui():
    global _config
    global _config_path
    global _switch_settings
    global _ik_controls

    if cmds.window(WINDOW_NAME, exists=True):
        cmds.deleteUI(WINDOW_NAME)

    _collapsed_state.clear()
    _limb_frames.clear()

    # Start with empty switch settings and real IK controls - only
    # populate when a calibration file is loaded, not from defaults.
    _switch_settings.clear()
    _ik_controls.clear()

    if os.path.isfile(DEFAULT_JSON_PATH):
        try:
            _config = load_limbs(DEFAULT_JSON_PATH)
            _switch_settings = load_switch_settings(DEFAULT_JSON_PATH)
            _ik_controls = load_ik_controls(DEFAULT_JSON_PATH)
            _config_path = DEFAULT_JSON_PATH

        except Exception as exc:
            cmds.warning(
                "Could not load default calibration JSON: {0}".format(exc)
            )

            _config = fresh_default_config()
            _switch_settings = {}  # Empty, not defaults
            _ik_controls = {}  # Empty, not defaults
            _config_path = None

    else:
        _config = fresh_default_config()
        _switch_settings = {}  # Empty, not defaults
        _ik_controls = {}  # Empty, not defaults
        _config_path = None

    window = cmds.window(
        WINDOW_NAME,
        title="IK -> FK Tool",
        widthHeight=(650, 760),
        sizeable=True
    )

    tabs = cmds.tabLayout(tabsVisible=True)

    ikfk_tab = _build_ikfk_tab(tabs)
    calib_tab = _build_calibration_tab(tabs)

    cmds.tabLayout(
        tabs,
        edit=True,
        tabLabel=[
            (ikfk_tab, "IK / FK"),
            (calib_tab, "Calibration"),
        ]
    )

    cmds.showWindow(window)

    _refresh_namespaces()
    _rebuild_table()
    _sync_path_fields()
    _rebuild_ikfk_buttons()
