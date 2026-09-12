"""
ikfk_calibrate.py

Calibration-tab logic: build temporary locator pairs between a source
control and its destination counterpart, read the offset between them,
and clean the locators up once the offset is safely written to the JSON
config (handled by the caller, in ikfk_ui.py).
"""
import maya.cmds as cmds

from ikfk_io import ns_join, HOOK_ROTATE_ORDER


def build_calibration_locators(
        config,
        namespace,
        rotate_order=HOOK_ROTATE_ORDER):

    if not namespace:
        raise ValueError("A namespace is required.")

    created = []

    print("")
    print("=" * 60)
    print("Calibration Locator Build")
    print("Namespace: {0}".format(namespace))
    print("=" * 60)

    for limb_name, limb_data in config.items():
        print("")
        print("=== {0} ===".format(limb_name))

        _build_pairs_for_direction(
            limb_name=limb_name,
            pairs=limb_data.get("fk_match_ik", []),
            namespace=namespace,
            rotate_order=rotate_order,
            created=created,
            direction_label="FK match IK",
            destination_key="fk_ctrl",
            source_key="source"
        )

        _build_pairs_for_direction(
            limb_name=limb_name,
            pairs=limb_data.get("ik_match_fk", []),
            namespace=namespace,
            rotate_order=rotate_order,
            created=created,
            direction_label="IK match FK",
            destination_key="ik_ctrl",
            source_key="source",
            skip_types=("pole_vector",)
        )

    print("")
    print("=" * 60)
    print(
        "Build complete: {0} locator pairs created.".format(
            len(created)
        )
    )
    print("=" * 60)
    print("")

    return created


def _build_pairs_for_direction(
        limb_name,
        pairs,
        namespace,
        rotate_order,
        created,
        direction_label,
        destination_key,
        source_key,
        skip_types=()):

    if not isinstance(pairs, list):
        print("  [SKIPPED] {0}: pair list is invalid".format(direction_label))
        return

    print("  --- {0} ---".format(direction_label))

    for index, pair in enumerate(pairs):
        if not isinstance(pair, dict):
            print("  [SKIPPED] Pair {0}: pair data is invalid".format(index + 1))
            continue

        if pair.get("type") in skip_types:
            print(
                "  [SKIPPED] Pair {0}: {1} pairs are solved live".format(
                    index + 1,
                    pair.get("type")
                )
            )
            continue

        destination_name = pair.get(destination_key, "").strip()
        source_name = pair.get(source_key, "").strip()

        if not destination_name or not source_name:
            print("  [SKIPPED] Pair {0}: incomplete".format(index + 1))
            continue

        _create_calibration_pair(
            limb_name=limb_name,
            pair_index=index + 1,
            destination_name=destination_name,
            source_name=source_name,
            source_ctrl=ns_join(namespace, source_name),
            destination_ctrl=ns_join(namespace, destination_name),
            pair=pair,
            rotate_order=rotate_order,
            created=created
        )


def _create_calibration_pair(
        limb_name,
        pair_index,
        destination_name,
        source_name,
        source_ctrl,
        destination_ctrl,
        pair,
        rotate_order,
        created):
    con_loc = cmds.spaceLocator(
        name="CALIB_con_" + destination_name
    )[0]

    hook_loc = cmds.spaceLocator(
        name="CALIB_hook_" + destination_name
    )[0]

    cmds.setAttr(con_loc + ".rotateOrder", rotate_order)
    cmds.setAttr(hook_loc + ".rotateOrder", rotate_order)
    cmds.setAttr(con_loc + ".rotateOrder", channelBox=True)
    cmds.setAttr(hook_loc + ".rotateOrder", channelBox=True)

    cmds.parent(hook_loc, con_loc)

    cmds.parentConstraint(
        source_ctrl,
        con_loc,
        maintainOffset=False
    )

    cmds.parentConstraint(
        destination_ctrl,
        hook_loc,
        maintainOffset=False
    )

    cmds.dgdirty(allPlugs=True)
    cmds.refresh(force=True)

    rotate_x = round(cmds.getAttr(hook_loc + ".rotateX"), 3)
    rotate_y = round(cmds.getAttr(hook_loc + ".rotateY"), 3)
    rotate_z = round(cmds.getAttr(hook_loc + ".rotateZ"), 3)

    pair["offset"] = {
        "rotate_order": rotate_order,
        "rotate": {
            "x": rotate_x,
            "y": rotate_y,
            "z": rotate_z
        }
    }

    created.append(
        (
            limb_name,
            destination_name,
            con_loc,
            hook_loc,
            pair["offset"]
        )
    )

    print("  [OK] Pair {0}: {1}".format(pair_index, destination_name))
    print("       Source: {0}".format(source_name))
    print(
        "       Rotate offset: "
        "X={0:.3f}, Y={1:.3f}, Z={2:.3f}".format(
            rotate_x,
            rotate_y,
            rotate_z
        )
    )


def delete_calibration_locators(created):
    """Remove the temporary CALIB_con_/CALIB_hook_ locators.

    created is the list returned by build_calibration_locators. Deleting
    each con_loc also removes its hook_loc child, since hook is parented
    under con. Returns the number of locators actually deleted (a pair
    may already be gone if the user manually cleaned up mid-session)."""
    deleted = 0

    for entry in created:
        con_loc = entry[2]

        if cmds.objExists(con_loc):
            cmds.delete(con_loc)
            deleted += 1

    return deleted


def _validate_pair_list(
        pairs,
        namespace,
        limb_name,
        destination_key,
        source_key):
    missing = []
    incomplete = []

    for index, pair in enumerate(pairs):
        if not isinstance(pair, dict):
            incomplete.append("{0} - Pair {1}".format(limb_name, index + 1))
            continue

        destination_name = pair.get(destination_key, "").strip()
        source_name = pair.get(source_key, "").strip()

        if not destination_name or not source_name:
            incomplete.append("{0} - Pair {1}".format(limb_name, index + 1))
            continue

        destination_node = ns_join(namespace, destination_name)
        source_node = ns_join(namespace, source_name)

        if not cmds.objExists(destination_node):
            missing.append(
                "{0}: {1}".format(destination_key, destination_node)
            )

        if not cmds.objExists(source_node):
            missing.append(
                "{0}: {1}".format(source_key, source_node)
            )

    return incomplete, missing


def validate_calibration_pairs(limbs, namespace):
    incomplete = []
    missing = []

    for limb_name, limb_data in limbs.items():
        limb_incomplete, limb_missing = _validate_pair_list(
            pairs=limb_data.get("fk_match_ik", []),
            namespace=namespace,
            limb_name=limb_name,
            destination_key="fk_ctrl",
            source_key="source"
        )
        incomplete.extend(limb_incomplete)
        missing.extend(limb_missing)

    return incomplete, missing


def _find_generated_ik_match_fk_pair(pairs, role, pair_type):
    for pair in pairs:
        if not isinstance(pair, dict):
            continue

        if pair.get("role") == role and pair.get("type", "offset") == pair_type:
            return pair

    return None


def validate_ik_match_fk_pairs(limbs, namespace):
    problems = []

    for limb_name, limb_data in limbs.items():
        pairs = limb_data.get("ik_match_fk", [])

        if not isinstance(pairs, list):
            problems.append(
                "{0} - IK match FK pairs must be a list".format(limb_name)
            )
            continue

        handle_pair = _find_generated_ik_match_fk_pair(
            pairs,
            role="ik_handle",
            pair_type="offset"
        )
        pole_pair = _find_generated_ik_match_fk_pair(
            pairs,
            role="pole_vector",
            pair_type="pole_vector"
        )

        if not handle_pair or not handle_pair.get("ik_ctrl", "").strip():
            problems.append(
                "{0} - missing IK Handle Control".format(limb_name)
            )
        else:
            ik_handle = ns_join(namespace, handle_pair["ik_ctrl"].strip())
            if not cmds.objExists(ik_handle):
                problems.append(
                    "{0} - IK Handle Control missing: {1}".format(
                        limb_name,
                        ik_handle
                    )
                )

        if not pole_pair or not pole_pair.get("ik_ctrl", "").strip():
            problems.append(
                "{0} - missing Pole Vector Control".format(limb_name)
            )
        else:
            pole_ctrl = ns_join(namespace, pole_pair["ik_ctrl"].strip())
            if not cmds.objExists(pole_ctrl):
                problems.append(
                    "{0} - Pole Vector Control missing: {1}".format(
                        limb_name,
                        pole_ctrl
                    )
                )

        for index, pair in enumerate(pairs):
            if not isinstance(pair, dict):
                problems.append(
                    "{0} - IK match FK Pair {1} is invalid".format(
                        limb_name,
                        index + 1
                    )
                )
                continue

            if pair.get("type") == "pole_vector" or pair.get("role") == "ik_handle":
                continue

            ik_name = pair.get("ik_ctrl", "").strip()
            source_name = pair.get("source", "").strip()

            if not ik_name or not source_name:
                problems.append(
                    "{0} - IK match FK Pair {1} is incomplete".format(
                        limb_name,
                        index + 1
                    )
                )
                continue

            ik_ctrl = ns_join(namespace, ik_name)
            source_ctrl = ns_join(namespace, source_name)

            if not cmds.objExists(ik_ctrl):
                problems.append(
                    "{0} - IK match FK Pair {1} missing ik_ctrl: {2}".format(
                        limb_name,
                        index + 1,
                        ik_ctrl
                    )
                )

            if not cmds.objExists(source_ctrl):
                problems.append(
                    "{0} - IK match FK Pair {1} missing source: {2}".format(
                        limb_name,
                        index + 1,
                        source_ctrl
                    )
                )

    return problems


def _measure_ik_handle_offset(source_ctrl, ik_ctrl, rotate_order):
    rotate_x = 0
    rotate_y = 0
    rotate_z = 0
    con_loc = None

    try:
        con_loc = cmds.spaceLocator(
            name="TMP_measure_con"
        )[0]

        hook_loc = cmds.spaceLocator(
            name="TMP_measure_hook"
        )[0]

        cmds.setAttr(con_loc + ".rotateOrder", rotate_order)
        cmds.setAttr(hook_loc + ".rotateOrder", rotate_order)
        cmds.parent(hook_loc, con_loc)

        cmds.parentConstraint(
            ik_ctrl,
            con_loc,
            maintainOffset=False
        )

        cmds.parentConstraint(
            source_ctrl,
            hook_loc,
            maintainOffset=False
        )

        cmds.dgdirty(allPlugs=True)
        cmds.refresh(force=True)

        rotate_x = round(cmds.getAttr(hook_loc + ".rotateX"), 3)
        rotate_y = round(cmds.getAttr(hook_loc + ".rotateY"), 3)
        rotate_z = round(cmds.getAttr(hook_loc + ".rotateZ"), 3)

    finally:
        if con_loc and cmds.objExists(con_loc):
            cmds.delete(con_loc)

    return rotate_x, rotate_y, rotate_z


def generate_ik_match_fk_pairs(
        limbs,
        namespace,
        rotate_order=HOOK_ROTATE_ORDER):

    """
    Generate per-limb IK match FK data from the FK match IK pair list.

    For each limb:
    - exactly three FK match IK pairs must be marked use_for_pole_vector
    - the first/second/third marked FK controls become shoulder/elbow/wrist
    - the generated IK handle pair uses the last marked FK control as its
      source
    - the generated pole-vector pair uses the stored pole-vector control
      and the three marked FK controls

    Existing manual IK match FK offset pairs are preserved.
    """
    generated = {}

    print("")
    print("=" * 60)
    print("Generating IK Match FK Pairs")
    print("=" * 60)

    for limb_name, limb_data in limbs.items():
        fk_match_ik_pairs = limb_data.get("fk_match_ik", [])
        ik_match_fk_pairs = limb_data.get("ik_match_fk", [])

        handle_pair = _find_generated_ik_match_fk_pair(
            ik_match_fk_pairs,
            role="ik_handle",
            pair_type="offset"
        )
        pole_pair = _find_generated_ik_match_fk_pair(
            ik_match_fk_pairs,
            role="pole_vector",
            pair_type="pole_vector"
        )

        if not handle_pair or not handle_pair.get("ik_ctrl", "").strip():
            raise ValueError(
                "{0}: set an IK Handle Control before building.".format(
                    limb_name
                )
            )

        if not pole_pair or not pole_pair.get("ik_ctrl", "").strip():
            raise ValueError(
                "{0}: set a Pole Vector Control before building.".format(
                    limb_name
                )
            )

        pv_pairs = [
            pair for pair in fk_match_ik_pairs
            if isinstance(pair, dict) and pair.get("use_for_pole_vector")
        ]

        if len(pv_pairs) != 3:
            raise ValueError(
                "{0}: exactly 3 FK match IK pairs must be marked PV; "
                "found {1}.".format(limb_name, len(pv_pairs))
            )

        shoulder_fk = pv_pairs[0].get("fk_ctrl", "").strip()
        elbow_fk = pv_pairs[1].get("fk_ctrl", "").strip()
        wrist_fk = pv_pairs[2].get("fk_ctrl", "").strip()

        if not shoulder_fk or not elbow_fk or not wrist_fk:
            raise ValueError(
                "{0}: all 3 PV-marked pairs need FK controls.".format(
                    limb_name
                )
            )

        rotate_x, rotate_y, rotate_z = _measure_ik_handle_offset(
            source_ctrl=ns_join(namespace, wrist_fk),
            ik_ctrl=ns_join(namespace, handle_pair["ik_ctrl"].strip()),
            rotate_order=rotate_order
        )

        manual_offset_pairs = [
            dict(pair) for pair in ik_match_fk_pairs
            if isinstance(pair, dict)
            and pair.get("type", "offset") == "offset"
            and pair.get("role") != "ik_handle"
        ]

        generated_pairs = manual_offset_pairs + [
            {
                "type": "offset",
                "role": "ik_handle",
                "ik_ctrl": handle_pair["ik_ctrl"].strip(),
                "source": wrist_fk,
                "offset": {
                    "rotate_order": rotate_order,
                    "translate": {"x": 0, "y": 0, "z": 0},
                    "rotate": {
                        "x": rotate_x,
                        "y": rotate_y,
                        "z": rotate_z,
                    },
                },
            },
            {
                "type": "pole_vector",
                "role": "pole_vector",
                "shoulder_ctrl": shoulder_fk,
                "elbow_ctrl": elbow_fk,
                "wrist_ctrl": wrist_fk,
                "ik_ctrl": pole_pair["ik_ctrl"].strip(),
            },
        ]

        generated[limb_name] = generated_pairs

        print("")
        print("=== {0} ===".format(limb_name))
        print(
            "  [OK] IK handle: {0} <- {1}".format(
                handle_pair["ik_ctrl"].strip(),
                wrist_fk
            )
        )
        print(
            "       Rotation offset: X={0:.3f}, Y={1:.3f}, Z={2:.3f}".format(
                rotate_x,
                rotate_y,
                rotate_z
            )
        )
        print(
            "  [OK] Pole vector: {0} from {1} / {2} / {3}".format(
                pole_pair["ik_ctrl"].strip(),
                shoulder_fk,
                elbow_fk,
                wrist_fk
            )
        )

    print("")
    print("=" * 60)
    print("Generated {0} limbs".format(len(generated)))
    print("=" * 60)
    print("")

    return generated
