"""
ikfk_snap_common.py

Shared between both snap directions (IK -> FK and FK -> IK): reading a
calibrated offset out of a pair, keying the switch attribute with a
stepped tangent, keying a control's channels while still constrained,
checking for locked channels before constraining onto them, and
validating a limb's pairs against the live scene.

Nothing in here cares which direction is being snapped - that's the
job of ikfk_switch_ik_to_fk.py / ikfk_switch_fk_to_ik.py.
"""
import maya.cmds as cmds

from ikfk_io import ns_join, HOOK_ROTATE_ORDER


# ---------------------------------------------------------------------------
# Locked-channel handling
# ---------------------------------------------------------------------------
def rotate_skip_axes(node):
    """Axis letters ('x', 'y', 'z') whose rotate channel is locked on
    node. Pass the result to orientConstraint's -skip flag so a locked
    channel is excluded instead of the constraint erroring out."""
    axes = []

    for axis in ("x", "y", "z"):
        if cmds.getAttr("{0}.rotate{1}".format(node, axis.upper()), lock=True):
            axes.append(axis)

    return axes


def transform_skip_axes(node, channel):
    """Same idea as rotate_skip_axes but generic over 'translate' or
    'rotate', for parentConstraint's -skipTranslate/-skipRotate
    flags."""
    axes = []

    for axis in ("x", "y", "z"):
        if cmds.getAttr("{0}.{1}{2}".format(node, channel, axis.upper()), lock=True):
            axes.append(axis)

    return axes


# ---------------------------------------------------------------------------
# Offset reading
# ---------------------------------------------------------------------------
def read_pair_offset(pair):
    """
    Read calibration information from one JSON pair.

    Expected format:

        "offset": {
            "rotate_order": 2,
            "translate": {"x": 0.0, "y": 0.0, "z": 0.0},
            "rotate": {"x": 0.0, "y": 0.0, "z": 0.0}
        }

    "translate" is optional and defaults to zero - IK -> FK calibration
    data (built via the Calibration tab) has never recorded translate,
    since FK controls only ever needed a rotation offset, so existing
    calibration files keep working unchanged. FK -> IK pairs (see
    DEFAULT_FK_TO_IK_LIMBS) do use it, for the pole vector and IK
    handle offsets.

    A pair with no "offset" key at all has never been calibrated and is
    treated as an error rather than silently defaulting to zero.

    Returns (rotate_order, translate_values, rotate_values), where each
    *_values is an (x, y, z) tuple.
    """
    if "offset" not in pair:
        raise ValueError(
            "No calibration offset recorded for '{0}' - run Build "
            "Locators on the Calibration tab before switching.".format(
                pair.get("fk_ctrl", "<unknown>")
            )
        )

    offset = pair.get("offset", {})

    if not isinstance(offset, dict):
        raise ValueError(
            "Offset must be a dictionary for '{0}'.".format(
                pair.get("fk_ctrl", "<unknown>")
            )
        )

    rotate_data = offset.get("rotate", {})
    translate_data = offset.get("translate", {})

    if not isinstance(rotate_data, dict):
        raise ValueError(
            "Offset rotate data must be a dictionary for '{0}'.".format(
                pair.get("fk_ctrl", "<unknown>")
            )
        )

    if not isinstance(translate_data, dict):
        raise ValueError(
            "Offset translate data must be a dictionary for '{0}'.".format(
                pair.get("fk_ctrl", "<unknown>")
            )
        )

    rotate_order = int(
        offset.get(
            "rotate_order",
            HOOK_ROTATE_ORDER
        )
    )

    if rotate_order < 0 or rotate_order > 5:
        raise ValueError(
            "Invalid rotate order {0} for '{1}'.".format(
                rotate_order,
                pair.get("fk_ctrl", "<unknown>")
            )
        )

    translate_values = (
        float(translate_data.get("x", 0.0)),
        float(translate_data.get("y", 0.0)),
        float(translate_data.get("z", 0.0)),
    )

    rotate_values = (
        float(rotate_data.get("x", 0.0)),
        float(rotate_data.get("y", 0.0)),
        float(rotate_data.get("z", 0.0)),
    )

    return rotate_order, translate_values, rotate_values


# ---------------------------------------------------------------------------
# Keying
# ---------------------------------------------------------------------------
def key_channels(ctrl, current_frame, attributes):
    """
    Key the given control's attributes (e.g. ["rotate"], or
    ["translate", "rotate"]) while it is still constrained.

    This prevents an already-keyed control from reverting when the
    temporary constraint is removed.
    """
    for attr in attributes:
        cmds.setKeyframe(
            ctrl,
            attribute=attr,
            time=current_frame
        )


def _step_all_keys(switch_plug):
    """Force every existing key on switch_plug to a step tangent, not
    just whichever pair was just created. A 0/1 blend attribute like
    this should never interpolate anywhere on its curve - if earlier
    keys from a previous snap are still on the curve with a different
    tangent type, stepping only the new segment leaves the rest of the
    curve blending, which reads as the switch "easing" through poses
    it should be popping between."""
    all_times = cmds.keyframe(switch_plug, query=True, timeChange=True) or []

    if not all_times:
        return

    cmds.keyTangent(
        switch_plug,
        time=(min(all_times), max(all_times)),
        inTangentType="step",
        outTangentType="step"
    )


def key_switch_before(switch_plug, from_value, to_value, current_frame):
    """Hold from_value one frame before the switch, then pop to
    to_value exactly at the current frame. Explicit values are used
    (not whatever the attribute currently reads) so this is correct
    even if the attribute is already sitting somewhere odd when the
    button is pressed."""
    cmds.setKeyframe(
        switch_plug,
        time=current_frame - 1,
        value=from_value
    )

    cmds.setKeyframe(
        switch_plug,
        time=current_frame,
        value=to_value
    )

    # Step every key on the curve, not just this new pair.
    _step_all_keys(switch_plug)


def key_switch_after(switch_plug, from_value, to_value, current_frame):
    """Mirror of key_switch_before: pop to to_value at the current
    frame, then hold from_value one frame later."""
    cmds.setKeyframe(
        switch_plug,
        time=current_frame,
        value=to_value
    )

    cmds.setKeyframe(
        switch_plug,
        time=current_frame + 1,
        value=from_value
    )

    # Step every key on the curve, not just this new pair.
    _step_all_keys(switch_plug)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------
def validate_switch_data(
        namespace,
        limb_name,
        limb_data,
        switch_data,
        driver_key="fk_ctrl",
        target_key="source"):
    """
    Validate a limb's pairs and switch attribute against the live
    scene. driver_key/target_key let this be reused for both
    directions: IK -> FK pairs use ("fk_ctrl", "source"), FK -> IK
    pairs use ("fk_ctrl", "ik_ctrl").
    """
    problems = []

    switch_node = ns_join(
        namespace,
        switch_data["object"]
    )

    switch_plug = "{0}.{1}".format(
        switch_node,
        switch_data["attr_name"]
    )

    if not cmds.objExists(switch_node):
        problems.append(
            "Switch node missing: {0}".format(switch_node)
        )

    elif not cmds.objExists(switch_plug):
        problems.append(
            "Switch attribute missing: {0}".format(switch_plug)
        )

    if not isinstance(limb_data, list):
        problems.append(
            "Limb '{0}' must contain a list of pairs.".format(
                limb_name
            )
        )

        return problems

    for index, pair in enumerate(limb_data):
        if not isinstance(pair, dict):
            problems.append(
                "{0}, Pair {1}: pair data is not a dictionary.".format(
                    limb_name,
                    index + 1
                )
            )
            continue

        driver_name = pair.get(driver_key, "").strip()
        target_name = pair.get(target_key, "").strip()

        if not driver_name:
            problems.append(
                "{0}, Pair {1}: {2} is empty.".format(
                    limb_name,
                    index + 1,
                    driver_key
                )
            )

        if not target_name:
            problems.append(
                "{0}, Pair {1}: {2} is empty.".format(
                    limb_name,
                    index + 1,
                    target_key
                )
            )

        if not driver_name or not target_name:
            continue

        driver_node = ns_join(namespace, driver_name)
        target_node = ns_join(namespace, target_name)

        if not cmds.objExists(driver_node):
            problems.append(
                "{0}, Pair {1}: {2} missing: {3}".format(
                    limb_name,
                    index + 1,
                    driver_key,
                    driver_node
                )
            )

        if not cmds.objExists(target_node):
            problems.append(
                "{0}, Pair {1}: {2} missing: {3}".format(
                    limb_name,
                    index + 1,
                    target_key,
                    target_node
                )
            )

        try:
            read_pair_offset(pair)

        except Exception as exc:
            problems.append(
                "{0}, Pair {1}: {2}".format(
                    limb_name,
                    index + 1,
                    exc
                )
            )

    return problems
