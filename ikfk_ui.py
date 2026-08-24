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
    fresh_default_switch_settings,
    get_scene_namespaces,
    get_selected_short_name,
    load_limbs,
    load_switch_settings,
    save_limbs,
    save_switch_settings,
)
from ikfk_calibrate import (
    build_calibration_locators,
    delete_calibration_locators,
    validate_calibration_pairs,
)
from ikfk_switch_ik_to_fk import snap_ik_to_fk
from ikfk_switch_fk_to_ik import snap_fk_to_ik

WINDOW_NAME = "ikfkToolUI"

# ---------------------------------------------------------------------------
# UI state (module-level, one window at a time)
# ---------------------------------------------------------------------------
_config = {}
_config_path = DEFAULT_JSON_PATH
_source_menu = None
_target_menu = None
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

# Switch settings (object/attr_name/ik_value/fk_value per limb),
# loaded from the calibration JSON's "switch_settings" block. This
# drives which per-limb buttons appear on the IK/FK tab.
_switch_settings = {}

# Consistent with the Complete/Incomplete/Empty pair colours in the
# Calibration tab's table.
_COLOUR_OK = (0.30, 0.45, 0.30)
_COLOUR_WARN = (0.48, 0.25, 0.25)


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
    """Refresh the Calibration tab's source/target menus and the IK/FK
    tab's single namespace menu together, since they all read from the
    same scene state."""
    namespaces = get_scene_namespaces()

    warn_text = ""
    if len(namespaces) > 2:
        warn_text = ("Warning: {0} namespaces found in scene ({1}). "
                      "Double check source/target above.").format(len(namespaces), ", ".join(namespaces))
    elif len(namespaces) == 0:
        warn_text = "Warning: no namespaces found in scene."

    cmds.text(_warning_label, edit=True, label=warn_text, visible=bool(warn_text))

    current_source = current_target = None
    if cmds.optionMenu(_source_menu, query=True, itemListLong=True):
        current_source = cmds.optionMenu(_source_menu, query=True, value=True)
        current_target = cmds.optionMenu(_target_menu, query=True, value=True)

    for menu in (_source_menu, _target_menu):
        for item in cmds.optionMenu(menu, query=True, itemListLong=True) or []:
            cmds.deleteUI(item)
        for ns in namespaces:
            cmds.menuItem(label=ns, parent=menu)

    if current_source in namespaces:
        cmds.optionMenu(_source_menu, edit=True, value=current_source)
    elif namespaces:
        cmds.optionMenu(_source_menu, edit=True, value=namespaces[0])

    if current_target in namespaces:
        cmds.optionMenu(_target_menu, edit=True, value=current_target)
    elif len(namespaces) > 1:
        cmds.optionMenu(_target_menu, edit=True, value=namespaces[1])

    # IK/FK tab's single namespace menu.
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


def _remove_pair(limb, index, *_args):
    del _config[limb][index]
    _rebuild_table()


def _remove_limb(limb, *_args):
    del _config[limb]
    _collapsed_state.pop(limb, None)
    _limb_frames.pop(limb, None)
    _rebuild_table()


def _add_pair(limb, *_args):
    _config[limb].append({"fk_ctrl": "", "source": ""})
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
    _rebuild_table()


def _build_field_row(parent, label, value, limb_name, idx, key):
    """One labeled row with a read-only textField + Set/Clear buttons.
    Used for both the 'Target FK Control' and 'Source Object' rows,
    which were previously duplicated blocks differing only in the key."""
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
            source_value = pair.get("source", "").strip()

            if fk_value and source_value:
                pair_colour = (0.30, 0.45, 0.30)
                status_text = "Complete"
            elif fk_value or source_value:
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
                pair_column, "Target FK Control", fk_value,
                limb_name, idx, "fk_ctrl"
            )

            _build_field_row(
                pair_column, "Source Object", source_value,
                limb_name, idx, "source"
            )

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
# Calibration tab: Load / Save / Build
# ---------------------------------------------------------------------------
def _do_load(*_args):
    global _config, _config_path, _switch_settings

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

    except Exception as exc:
        error_dialog("Load Failed", exc)
        return

    _config = loaded_config
    _config_path = path
    _switch_settings = loaded_switch_settings

    # Collapse state is tied to limb names in the previous file - stale
    # entries are harmless (just unused keys) but start clean so a
    # limb name reused across files doesn't inherit an unrelated state.
    _collapsed_state.clear()
    _limb_frames.clear()

    _sync_path_fields()
    _rebuild_table()


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
        # Always write the current switch settings alongside the limb
        # pairs, so the file is a complete, self-contained record that
        # a future session (or the IK/FK tab on reload) can read the
        # per-limb switch mapping from, rather than relying on the
        # DEFAULT_SWITCH_SETTINGS fallback in ikfk_io.py.
        save_switch_settings(_switch_settings, path)

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
    source_ns = cmds.optionMenu(
        _source_menu,
        query=True,
        value=True
    )

    target_ns = cmds.optionMenu(
        _target_menu,
        query=True,
        value=True
    )

    # --------------------------------------------------------------
    # Check namespace selections
    # --------------------------------------------------------------
    if not source_ns or not target_ns:
        cmds.confirmDialog(
            title="Missing Selection",
            message=(
                "Pick both a source and target namespace."
            ),
            button=["OK"]
        )
        return

    if source_ns == target_ns:
        proceed = cmds.confirmDialog(
            title="Same Namespace",
            message=(
                "Source and target namespaces are the same "
                "({0}). Continue anyway?"
            ).format(source_ns),
            button=["Continue", "Cancel"],
            defaultButton="Cancel",
            cancelButton="Cancel",
            dismissString="Cancel"
        )

        if proceed != "Continue":
            return

    # --------------------------------------------------------------
    # Validate all configured pairs before building
    # --------------------------------------------------------------
    incomplete, missing = validate_calibration_pairs(
        _config,
        source_ns,
        target_ns
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

    if problems:
        error_dialog("Cannot Build", "\n\n".join(problems))
        return

    # --------------------------------------------------------------
    # Build locators and capture offset data
    # --------------------------------------------------------------
    try:
        created = build_calibration_locators(
            _config,
            source_ns,
            target_ns
        )

    except Exception as exc:
        error_dialog("Build Failed", exc)

        # Keep this while troubleshooting for the full traceback.
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

            save_succeeded = True

            print(
                "Calibration offsets saved to: {0}".format(
                    _config_path
                )
            )

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

    try:
        snap_func(
            namespace=namespace,
            limb_name=limb_name,
            calibration_path=_config_path,
            key_before=key_before,
            key_after=key_after
        )

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

        try:
            snap_func(
                namespace=namespace,
                limb_name=limb_name,
                calibration_path=_config_path,
                key_before=key_before,
                key_after=key_after
            )

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
    global _source_menu, _target_menu, _warning_label, _path_field, _table_layout

    calib_form = cmds.formLayout(parent=parent)

    top_column = cmds.columnLayout(
        adjustableColumn=True,
        rowSpacing=8,
        columnAttach=("both", 12),
        parent=calib_form
    )

    cmds.text(label="", height=2)

    cmds.text(
        label="Source namespace (IK rig, posed):",
        align="left"
    )

    _source_menu = cmds.optionMenu()

    cmds.text(
        label="Target namespace (FK rig, matched):",
        align="left"
    )

    _target_menu = cmds.optionMenu()

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
            "row where you want the object stored."
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
    global _key_before_cb, _key_after_cb

    ikfk_column = cmds.columnLayout(
        adjustableColumn=True,
        rowSpacing=10,
        columnAttach=("both", 12),
        parent=parent
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

    for limb_name in sorted(_switch_settings.keys()):
        cmds.button(
            label="{0} IK match FK".format(limb_name),
            command=lambda *_args, l=limb_name: _do_snap(l, "fk_to_ik"),
            height=28
        )

    cmds.button(
        label="All Limbs IK Match FK",
        height=32,
        command=lambda *_args: _do_snap_all("fk_to_ik")
    )

    return ikfk_column


def show_ui():
    global _config
    global _config_path
    global _switch_settings

    if cmds.window(WINDOW_NAME, exists=True):
        cmds.deleteUI(WINDOW_NAME)

    _collapsed_state.clear()
    _limb_frames.clear()

    if os.path.isfile(DEFAULT_JSON_PATH):
        try:
            _config = load_limbs(DEFAULT_JSON_PATH)
            _switch_settings = load_switch_settings(DEFAULT_JSON_PATH)
            _config_path = DEFAULT_JSON_PATH

        except Exception as exc:
            cmds.warning(
                "Could not load default calibration JSON: {0}".format(exc)
            )

            _config = fresh_default_config()
            _switch_settings = fresh_default_switch_settings()
            _config_path = None

    else:
        _config = fresh_default_config()
        _switch_settings = fresh_default_switch_settings()
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
