"""
ikfk_switch_ik_to_fk.py

IK -> FK snap: read the IK-driven joint's current pose, and rotate the
FK control to match it, using the offset calibrated on the
Calibration tab. Rotation-only - FK controls stay positioned by their
own rig hierarchy, so translate is never touched here.
"""
import maya.cmds as cmds

from ikfk_io import ns_join, load_limbs, load_switch_settings
from ikfk_snap_common import (
    read_pair_offset,
    key_channels,
    key_switch_before,
    key_switch_after,
    validate_switch_data,
    rotate_skip_axes,
)


def snap_ik_to_fk(
        namespace,
        limb_name,
        calibration_path,
        key_before=False,
        key_after=False):
    """
    Snap one FK limb to its current IK-driven pose.

    namespace:
        Character namespace, for example "ddfemale_001".

    limb_name:
        Limb key from the JSON file, for example "L_arm".

    calibration_path:
        JSON file containing IK controls, FK controls, offsets, and
        the switch_settings block for this limb.

    key_before:
        Hold the switch attribute at ik_value one frame before the
        current frame, then key it to fk_value exactly on the current
        frame, with a stepped tangent so the transition doesn't
        interpolate. Done before the snap itself runs.

    key_after:
        Mirror image of key_before: key fk_value at the current frame,
        then hold ik_value one frame later, again with a stepped
        tangent. Done before the snap itself runs.

    key_before and key_after are mutually exclusive - only one (or
    neither) should be set. FK rotations are always keyed during the
    snap regardless of either option.

    If neither key_before nor key_after is set, the FK controls are
    still snapped to match the IK pose, but the switch attribute
    itself is left exactly where it started - it's flipped internally
    so the genuine IK pose can be read, then restored to its original
    value before returning.
    """
    if not calibration_path:
        raise ValueError(
            "No calibration JSON file is set. Load or save one on the "
            "Calibration tab first."
        )

    limbs = load_limbs(calibration_path)

    if limb_name not in limbs:
        available = ", ".join(sorted(limbs.keys())) or "<none>"

        raise KeyError(
            "Limb '{0}' was not found in:\n{1}\n\n"
            "Available limbs: {2}".format(
                limb_name,
                calibration_path,
                available
            )
        )

    switch_settings = load_switch_settings(calibration_path)

    if limb_name not in switch_settings:
        raise KeyError(
            "No switch settings are defined for limb '{0}' in "
            "{1}'s 'switch_settings' block.".format(
                limb_name,
                calibration_path
            )
        )

    limb_data = limbs[limb_name]
    switch_data = switch_settings[limb_name]

    problems = validate_switch_data(
        namespace,
        limb_name,
        limb_data,
        switch_data,
        driver_key="fk_ctrl",
        target_key="ik_ctrl"
    )

    if problems:
        raise RuntimeError(
            "Cannot perform IK -> FK switch:\n\n{0}".format(
                "\n".join(problems)
            )
        )

    switch_node = ns_join(
        namespace,
        switch_data["object"]
    )

    switch_plug = "{0}.{1}".format(
        switch_node,
        switch_data["attr_name"]
    )

    current_frame = cmds.currentTime(query=True)
    original_value = cmds.getAttr(switch_plug)
    temp_locators = []

    print("")
    print("=" * 60)
    print("IK -> FK Switch")
    print("Namespace: {0}".format(namespace))
    print("Limb: {0}".format(limb_name))
    print("Calibration: {0}".format(calibration_path))
    print("=" * 60)

    cmds.undoInfo(openChunk=True)

    try:
        # key_before/key_after are mutually exclusive, so only one of
        # these runs.
        if key_before:
            key_switch_before(
                switch_plug,
                switch_data["ik_value"],
                switch_data["fk_value"],
                current_frame
            )

        elif key_after:
            key_switch_after(
                switch_plug,
                switch_data["ik_value"],
                switch_data["fk_value"],
                current_frame
            )

        # Ensure all IK controls represent the genuine IK-driven pose.
        cmds.setAttr(
            switch_plug,
            switch_data["ik_value"]
        )

        # Force Maya to evaluate the IK pose.
        cmds.dgdirty(allPlugs=True)
        cmds.refresh(force=True)

        for index, pair in enumerate(limb_data):
            fk_name = pair["fk_ctrl"].strip()
            ik_name = pair["ik_ctrl"].strip()

            fk_ctrl = ns_join(namespace, fk_name)
            ik_ctrl = ns_join(namespace, ik_name)

            rotate_order, _translate_offset, rotate_offset = read_pair_offset(pair)

            con_loc = cmds.spaceLocator(
                name="TMP_con_" + fk_name
            )[0]

            hook_loc = cmds.spaceLocator(
                name="TMP_hook_" + fk_name
            )[0]

            temp_locators.append(con_loc)

            # Rotate order must match the order used during calibration.
            cmds.setAttr(
                con_loc + ".rotateOrder",
                rotate_order
            )

            cmds.setAttr(
                hook_loc + ".rotateOrder",
                rotate_order
            )

            cmds.parent(
                hook_loc,
                con_loc
            )

            # Snap the parent locator to the live IK control, then freeze it.
            ik_constraint = cmds.parentConstraint(
                ik_ctrl,
                con_loc,
                maintainOffset=False
            )

            cmds.delete(ik_constraint)

            # Translation offset is always zero for this direction - FK
            # controls stay positioned by their own rig hierarchy, so
            # only rotation is ever matched.
            cmds.setAttr(
                hook_loc + ".translate",
                0.0,
                0.0,
                0.0
            )

            cmds.setAttr(
                hook_loc + ".rotate",
                *rotate_offset
            )

            # Match only rotation so the FK control remains positioned
            # correctly beneath its rig hierarchy. Skip any rotate
            # axis that's locked on the target rather than erroring.
            skip_axes = rotate_skip_axes(fk_ctrl)

            if skip_axes:
                print(
                    "  [INFO] Pair {0}: {1} - skipping locked rotate "
                    "axis/axes: {2}".format(
                        index + 1,
                        fk_name,
                        ", ".join(skip_axes)
                    )
                )

            constraint_kwargs = {"maintainOffset": False}

            if skip_axes:
                constraint_kwargs["skip"] = skip_axes

            fk_constraint = cmds.orientConstraint(
                hook_loc,
                fk_ctrl,
                **constraint_kwargs
            )

            # Key while still constrained.
            key_channels(
                fk_ctrl,
                current_frame,
                ["rotate"]
            )

            cmds.delete(fk_constraint)

            print(
                "  [OK] Pair {0}: {1}".format(
                    index + 1,
                    fk_name
                )
            )

            print(
                "       IK Control: {0}".format(ik_ctrl)
            )

            print(
                "       Offset: "
                "X={0:.3f}, Y={1:.3f}, Z={2:.3f}, order={3}".format(
                    rotate_offset[0],
                    rotate_offset[1],
                    rotate_offset[2],
                    rotate_order
                )
            )

        if key_before or key_after:
            # Keyed already puts the correct value at current_frame
            # via key_switch_before/after above - land the live
            # attribute there too so the scene matches the keyed pose.
            cmds.setAttr(
                switch_plug,
                switch_data["fk_value"]
            )

        else:
            # No key requested - the FK controls are posed, but the
            # switch itself goes back to whatever it was before the
            # snap, rather than committing to FK.
            cmds.setAttr(
                switch_plug,
                original_value
            )

        print("")
        print(
            "Switch complete: {0} FK controls matched.".format(
                len(limb_data)
            )
        )

    finally:
        # Delete all temporary locator hierarchies even if an error occurs.
        for locator in temp_locators:
            if cmds.objExists(locator):
                cmds.delete(locator)

        cmds.undoInfo(closeChunk=True)

        print("=" * 60)
        print("")
