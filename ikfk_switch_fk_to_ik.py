"""
ikfk_switch_fk_to_ik.py

IK match FK snap: read the FK-driven controls' current pose, and move the
IK controls to match. Two pair types, handled differently (see
the saved IK match FK pairs in ikfk_io.py for the full explanation):

  "offset" pairs (shoulder, IK handle) - same con_loc/hook_loc
  technique as IK -> FK, just with the driver/target roles swapped
  ("the child becomes the parent") and translate included, since IK
  controls are free-floating rather than positioned by a rig
  hierarchy the way FK controls are.

  "pole_vector" pairs - special-cased. A pole vector's target position
  is solved live from the shoulder/elbow/wrist controls' current pose
  rather than read from a fixed offset, since a static offset off the
  elbow only stays correct at the exact pose it was measured at.
"""
import maya.cmds as cmds

from ikfk_io import (
    ns_join,
    get_switch_plug,
    load_ik_match_fk_pairs,
    load_switch_settings,
)
from ikfk_snap_common import (
    read_pair_offset,
    key_channels,
    key_switch_before,
    key_switch_after,
    validate_switch_data,
    transform_skip_axes,
)


# ---------------------------------------------------------------------------
# Pole vector solver
# ---------------------------------------------------------------------------
def compute_pole_vector_position(
        namespace,
        shoulder_ctrl_name,
        elbow_ctrl_name,
        wrist_ctrl_name,
        distance):
    """
    Solve a pole vector world position from the shoulder/elbow/wrist
    controls' current pose.

    Method:
    - Create two locators at the elbow.
    - Aim one at the shoulder and the other at the wrist.
    - Parent a fixed offset locator under each aimed locator.
    - Set each offset locator to local translate X = -25.
    - Point constrain the final pole-position locator to both offset
      locators.
    - Read the resulting world-space position.

    The offset locators provide a fixed distance from the elbow along
    each aimed axis. The midpoint between those two points gives the
    calculated pole-vector position.

    Returns an (x, y, z) world-space translate tuple.
    """
    shoulder_ctrl = ns_join(namespace, shoulder_ctrl_name)
    elbow_ctrl = ns_join(namespace, elbow_ctrl_name)
    wrist_ctrl = ns_join(namespace, wrist_ctrl_name)

    temp_nodes = []

    try:
        loc_from_shoulder = cmds.spaceLocator(
            name="TMP_pole_elbow_to_shoulder"
        )[0]

        loc_from_shoulder_offset = cmds.spaceLocator(
            name="TMP_pole_elbow_to_shoulder_offset"
        )[0]

        loc_from_wrist = cmds.spaceLocator(
            name="TMP_pole_elbow_to_wrist"
        )[0]

        loc_from_wrist_offset = cmds.spaceLocator(
            name="TMP_pole_elbow_to_wrist_offset"
        )[0]

        pole_position_loc = cmds.spaceLocator(
            name="TMP_pole_position"
        )[0]

        cmds.parent(
            loc_from_shoulder_offset,
            loc_from_shoulder
        )

        cmds.parent(
            loc_from_wrist_offset,
            loc_from_wrist
        )

        # Set explicit local transforms for the offset locators.
        cmds.setAttr(
            loc_from_shoulder_offset + ".translate",
            -distance, 0, 0
        )
        cmds.setAttr(
            loc_from_shoulder_offset + ".rotate",
            0, 0, 0
        )

        cmds.setAttr(
            loc_from_wrist_offset + ".translate",
            -distance, 0, 0
        )
        cmds.setAttr(
            loc_from_wrist_offset + ".rotate",
            0, 0, 0
        )

        temp_nodes.extend(
            [
                loc_from_shoulder,
                loc_from_shoulder_offset,
                loc_from_wrist,
                loc_from_wrist_offset,
                pole_position_loc,
            ]
        )

        # Both aim locators sit at the elbow to start.
        elbow_point_constraint_a = cmds.pointConstraint(
            elbow_ctrl,
            loc_from_shoulder,
            offset=(0, 0, 0),
            weight=1
        )

        elbow_point_constraint_b = cmds.pointConstraint(
            elbow_ctrl,
            loc_from_wrist,
            offset=(0, 0, 0),
            weight=1
        )

        cmds.delete(elbow_point_constraint_a, elbow_point_constraint_b)

        # Aim each locator along the bone it represents.
        aim_at_shoulder = cmds.aimConstraint(
            shoulder_ctrl,
            loc_from_shoulder,
            offset=(0, 0, 0),
            weight=1,
            aimVector=(1, 0, 0),
            upVector=(0, 1, 0),
            worldUpType="vector",
            worldUpVector=(0, 1, 0)
        )

        aim_at_wrist = cmds.aimConstraint(
            wrist_ctrl,
            loc_from_wrist,
            offset=(0, 0, 0),
            weight=1,
            aimVector=(1, 0, 0),
            upVector=(0, 1, 0),
            worldUpType="vector",
            worldUpVector=(0, 1, 0)
        )

        # Average the two extended points.
        pole_point_constraint = cmds.pointConstraint(
            loc_from_shoulder_offset,
            loc_from_wrist_offset,
            pole_position_loc
        )

        cmds.dgdirty(allPlugs=True)
        cmds.refresh(force=True)

        world_position = cmds.xform(
            pole_position_loc,
            query=True,
            worldSpace=True,
            translation=True
        )

        cmds.delete(pole_point_constraint)

    finally:
        for node in temp_nodes:
            if cmds.objExists(node):
                cmds.delete(node)
        pass

    return tuple(world_position)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------
def _validate_pole_vector_pair(namespace, limb_name, index, pair):
    problems = []

    required_keys = (
        "shoulder_ctrl",
        "elbow_ctrl",
        "wrist_ctrl",
        "ik_ctrl",
    )

    for key in required_keys:
        if key not in pair:
            problems.append(
                "{0}, Pair {1}: pole_vector pair is missing '{2}'.".format(
                    limb_name,
                    index + 1,
                    key
                )
            )

    if problems:
        # Can't usefully check node existence without all the keys.
        return problems

    for key in ("shoulder_ctrl", "elbow_ctrl", "wrist_ctrl", "ik_ctrl"):
        node = ns_join(namespace, pair[key].strip())

        if not cmds.objExists(node):
            problems.append(
                "{0}, Pair {1}: {2} missing: {3}".format(
                    limb_name,
                    index + 1,
                    key,
                    node
                )
            )

    return problems


def _validate_ik_match_fk_limb(namespace, limb_name, limb_data, switch_data):
    """validate_switch_data (shared/common) handles "offset" pairs
    correctly as-is, since it only cares about driver_key/target_key
    and read_pair_offset - but it doesn't know about "pole_vector"
    pairs, which have a different shape entirely. Split the limb's
    pairs by type and validate each with the right logic."""
    offset_pairs = [
        pair for pair in limb_data
        if isinstance(pair, dict) and pair.get("type", "offset") == "offset"
    ]

    pole_vector_pairs = [
        (index, pair) for index, pair in enumerate(limb_data)
        if isinstance(pair, dict) and pair.get("type") == "pole_vector"
    ]

    problems = validate_switch_data(
        namespace,
        limb_name,
        offset_pairs,
        switch_data,
        driver_key="source",
        target_key="ik_ctrl"
    )

    for index, pair in pole_vector_pairs:
        problems.extend(
            _validate_pole_vector_pair(namespace, limb_name, index, pair)
        )

    return problems


# ---------------------------------------------------------------------------
# FK -> IK snap
# ---------------------------------------------------------------------------
def snap_fk_to_ik(
        namespace,
        limb_name,
        calibration_path,
        key_before=False,
        key_after=False,
        pole_distance=None):
    """
    Snap one IK limb to its current FK-driven pose.

    namespace, limb_name, calibration_path: same as snap_ik_to_fk.

    key_before / key_after: same shape as snap_ik_to_fk, but mirrored -
    key_before holds the switch at fk_value one frame before the
    switch, then pops to ik_value on the current frame; key_after pops
    to ik_value at the current frame, then holds fk_value one frame
    later.

    pole_distance: required whenever the limb has a pole_vector pair -
    the live distance value entered on the IK/FK tab, used for every
    pole vector solved during this snap. Not read from JSON; this is
    a per-session value, not calibration data.
    
    If neither key_before nor key_after is set, the IK controls are
    still snapped to match the FK pose, but the switch attribute
    itself is left exactly where it started - it's flipped internally
    so the genuine FK pose can be read and the IK controls posed
    against it, then restored to its original value before returning.
    """
    if not calibration_path:
        raise ValueError(
            "No calibration JSON file is set. Load or save one on the "
            "Calibration tab first."
        )

    if pole_distance is None:
        raise ValueError(
            "No pole vector distance set. Enter one in the field on "
            "the IK/FK tab before snapping."
        )
    limbs = load_ik_match_fk_pairs(calibration_path)

    if limb_name not in limbs:
        available = ", ".join(sorted(limbs.keys())) or "<none>"

        raise KeyError(
            "Limb '{0}' has no IK match FK pairs defined.\n\n"
            "Available limbs: {1}".format(
                limb_name,
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

    problems = _validate_ik_match_fk_limb(
        namespace,
        limb_name,
        limb_data,
        switch_data
    )

    if problems:
        raise RuntimeError(
            "Cannot perform IK match FK switch:\n\n{0}".format(
                "\n".join(problems)
            )
        )

    switch_plug = get_switch_plug(namespace, switch_data)

    current_frame = cmds.currentTime(query=True)
    original_value = cmds.getAttr(switch_plug)
    temp_locators = []

    print("")
    print("=" * 60)
    print("IK Match FK Switch")
    print("Namespace: {0}".format(namespace))
    print("Limb: {0}".format(limb_name))
    print("Calibration: {0}".format(calibration_path))
    print("=" * 60)

    cmds.undoInfo(openChunk=True)

    try:
        # key_before/key_after are mutually exclusive, so only one of
        # these runs. Mirrored from IK -> FK: from_value/to_value are
        # swapped since we're driving toward ik_value here.
        if key_before:
            key_switch_before(
                switch_plug,
                switch_data["fk_value"],
                switch_data["ik_value"],
                current_frame
            )

        elif key_after:
            key_switch_after(
                switch_plug,
                switch_data["fk_value"],
                switch_data["ik_value"],
                current_frame
            )

        # Ensure all FK controls represent the genuine FK-driven pose.
        cmds.setAttr(
            switch_plug,
            switch_data["fk_value"]
        )

        # Force Maya to evaluate the FK pose.
        cmds.dgdirty(allPlugs=True)
        cmds.refresh(force=True)

        matched_count = 0

        for index, pair in enumerate(limb_data):
            pair_type = pair.get("type", "offset")

            if pair_type == "pole_vector":
                shoulder_name = pair["shoulder_ctrl"].strip()
                elbow_name = pair["elbow_ctrl"].strip()
                wrist_name = pair["wrist_ctrl"].strip()
                ik_name = pair["ik_ctrl"].strip()
                distance = pole_distance

                ik_ctrl = ns_join(namespace, ik_name)

                world_position = compute_pole_vector_position(
                    namespace,
                    shoulder_name,
                    elbow_name,
                    wrist_name,
                    distance
                )

                translate_skip = transform_skip_axes(ik_ctrl, "translate")

                if translate_skip:
                    print(
                        "  [WARNING] Pair {0}: {1} has locked translate "
                        "axis/axes ({2}) - those axes were left "
                        "untouched.".format(
                            index + 1,
                            ik_name,
                            ", ".join(translate_skip)
                        )
                    )

                # Only write the unlocked translate axes - cmds.xform
                # (like setAttr) errors on a locked channel.
                current_position = cmds.xform(
                    ik_ctrl,
                    query=True,
                    worldSpace=True,
                    translation=True
                )

                final_position = [
                    current_position[axis_index]
                    if "xyz"[axis_index] in translate_skip
                    else world_position[axis_index]
                    for axis_index in range(3)
                ]

                cmds.xform(
                    ik_ctrl,
                    worldSpace=True,
                    translation=final_position
                )

                key_channels(
                    ik_ctrl,
                    current_frame,
                    ["translate"]
                )

                print(
                    "  [OK] Pair {0}: pole vector - {1}".format(
                        index + 1,
                        ik_name
                    )
                )

                print(
                    "       Solved from: {0} / {1} / {2}".format(
                        shoulder_name,
                        elbow_name,
                        wrist_name
                    )
                )

                print(
                    "       Position: X={0:.3f}, Y={1:.3f}, Z={2:.3f}".format(
                        *world_position
                    )
                )

                matched_count += 1
                continue

            # -----------------------------------------------------
            # "offset" pairs: shoulder (self-match, no-op) and the
            # IK handle. Same con_loc/hook_loc technique as
            # IK -> FK, but the child becomes the parent - con_loc
            # is now constrained to the FK driver, and the offset
            # (translate + rotate) is applied on top of that before
            # driving the IK target.
            # -----------------------------------------------------
            fk_name = pair["source"].strip()
            ik_name = pair["ik_ctrl"].strip()

            fk_ctrl = ns_join(namespace, fk_name)
            ik_ctrl = ns_join(namespace, ik_name)

            if fk_ctrl == ik_ctrl:
                # Shared control (e.g. the shoulder/clavicle) - not
                # actually switched, and Maya can't constrain a node
                # to itself.
                print(
                    "  [SKIPPED] Pair {0}: {1} is a shared control, "
                    "no snap needed".format(
                        index + 1,
                        fk_name
                    )
                )
                continue

            rotate_order, translate_offset, rotate_offset = read_pair_offset(pair)

            con_loc = cmds.spaceLocator(
                name="TMP_con_" + fk_name
            )[0]

            hook_loc = cmds.spaceLocator(
                name="TMP_hook_" + fk_name
            )[0]

            temp_locators.append(con_loc)

            cmds.setAttr(con_loc + ".rotateOrder", rotate_order)
            cmds.setAttr(hook_loc + ".rotateOrder", rotate_order)

            cmds.parent(hook_loc, con_loc)

            # Snap the parent locator to the live FK driver, then freeze it.
            driver_constraint = cmds.parentConstraint(
                fk_ctrl,
                con_loc,
                maintainOffset=False
            )

            cmds.delete(driver_constraint)

            cmds.setAttr(hook_loc + ".translate", *translate_offset)
            cmds.setAttr(hook_loc + ".rotate", *rotate_offset)

            # Match both translate and rotate - unlike FK controls, IK
            # controls are free-floating rather than positioned by a
            # rig hierarchy, so translate has to be driven explicitly.
            # Skip any axis that's locked on the target rather than
            # erroring out.
            translate_skip = transform_skip_axes(ik_ctrl, "translate")
            rotate_skip = transform_skip_axes(ik_ctrl, "rotate")

            if translate_skip or rotate_skip:
                print(
                    "  [INFO] Pair {0}: {1} - skipping locked "
                    "channel(s) translate:{2} rotate:{3}".format(
                        index + 1,
                        ik_name,
                        translate_skip or "-",
                        rotate_skip or "-"
                    )
                )

            constraint_kwargs = {"maintainOffset": False}

            if translate_skip:
                constraint_kwargs["skipTranslate"] = translate_skip

            if rotate_skip:
                constraint_kwargs["skipRotate"] = rotate_skip

            ik_constraint = cmds.parentConstraint(
                hook_loc,
                ik_ctrl,
                **constraint_kwargs
            )

            key_channels(
                ik_ctrl,
                current_frame,
                ["translate", "rotate"]
            )

            cmds.delete(ik_constraint)

            matched_count += 1

            print(
                "  [OK] Pair {0}: {1}".format(
                    index + 1,
                    fk_name
                )
            )

            print(
                "       Target: {0}".format(ik_ctrl)
            )

            print(
                "       Offset: "
                "T=({0:.3f}, {1:.3f}, {2:.3f}) "
                "R=({3:.3f}, {4:.3f}, {5:.3f}) order={6}".format(
                    translate_offset[0], translate_offset[1], translate_offset[2],
                    rotate_offset[0], rotate_offset[1], rotate_offset[2],
                    rotate_order
                )
            )

        if key_before or key_after:
            # Keyed already puts the correct value at current_frame
            # via key_switch_before/after above - land the live
            # attribute there too so the scene matches the keyed pose.
            cmds.setAttr(
                switch_plug,
                switch_data["ik_value"]
            )

        else:
            # No key requested - the IK controls are posed, but the
            # switch itself goes back to whatever it was before the
            # snap, rather than committing to IK.
            cmds.setAttr(
                switch_plug,
                original_value
            )

        print("")
        print(
            "Switch complete: {0} IK controls matched.".format(
                matched_count
            )
        )

    finally:
        for locator in temp_locators:
            if cmds.objExists(locator):
                cmds.delete(locator)

        cmds.undoInfo(closeChunk=True)

        print("=" * 60)
        print("")
