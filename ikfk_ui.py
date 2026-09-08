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
