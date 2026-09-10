"""
ikfk_calibrate.py

Calibration-tab logic: build temporary locator pairs between an
IK-driven control and its FK counterpart, read the rotation offset
between them, and clean the locators up once the offset is safely
written to the JSON config (handled by the caller, in ikfk_ui.py).
"""
import maya.cmds as cmds

from ikfk_io import ns_join, HOOK_ROTATE_ORDER


def build_calibration_locators(
        limbs,
        namespace,
        rotate_order=HOOK_ROTATE_ORDER):

    if not namespace:
        raise ValueError("A namespace is required.")

    created = []

    print("")
    print("=" * 60)
    print("IK -> FK Calibration Locator Build")
    print("Namespace: {0}".format(namespace))
    print("=" * 60)

    for limb_name, pairs in limbs.items():
        print("")
        print("=== {0} ===".format(limb_name))

        for index, pair in enumerate(pairs):
            fk_name = pair.get("fk_ctrl", "").strip()
            ik_name = pair.get("ik_ctrl", "").strip()

            if not fk_name or not ik_name:
                print(
                    "  [SKIPPED] Pair {0}: incomplete".format(
                        index + 1
                    )
                )
                continue

            fk_ctrl = ns_join(namespace, fk_name)
            ik_ctrl = ns_join(namespace, ik_name)

            con_loc = cmds.spaceLocator(
                name="CALIB_con_" + fk_name
            )[0]

            hook_loc = cmds.spaceLocator(
                name="CALIB_hook_" + fk_name
            )[0]

            # Set both locator rotate orders before constraining.
            cmds.setAttr(
                con_loc + ".rotateOrder",
                rotate_order
            )

            cmds.setAttr(
                hook_loc + ".rotateOrder",
                rotate_order
            )

            cmds.setAttr(
                con_loc + ".rotateOrder",
                channelBox=True
            )

            cmds.setAttr(
                hook_loc + ".rotateOrder",
                channelBox=True
            )

            cmds.parent(
                hook_loc,
                con_loc
            )

            # Snap the parent locator to the IK control.
            cmds.parentConstraint(
                ik_ctrl,
                con_loc,
                maintainOffset=False
            )

            # Snap the hook locator to the FK control.
            cmds.parentConstraint(
                fk_ctrl,
                hook_loc,
                maintainOffset=False
            )

            # Force Maya to evaluate the constrained transforms.
            cmds.dgdirty(allPlugs=True)
            cmds.refresh(force=True)

            # Read and round the local rotation of the hook.
            rotate_x = round(
                cmds.getAttr(hook_loc + ".rotateX"),
                3
            )

            rotate_y = round(
                cmds.getAttr(hook_loc + ".rotateY"),
                3
            )

            rotate_z = round(
                cmds.getAttr(hook_loc + ".rotateZ"),
                3
            )

            print(
                "  [DEBUG] pair object id: {0}, pair contents: {1}".format(id(pair), pair)
            )
            print(
                "  [DEBUG] Read rotations: X={0}, Y={1}, Z={2}".format(
                    rotate_x, rotate_y, rotate_z
                )
            )

            # Store the calibration data in the configuration dictionary.
            pair["offset"] = {
                "rotate_order": rotate_order,
                "rotate": {
                    "x": rotate_x,
                    "y": rotate_y,
                    "z": rotate_z
                }
            }

            print(
                "  [DEBUG] Stored to pair: {0}".format(
                    pair["offset"]
                )
            )

            created.append(
                (
                    limb_name,
                    fk_name,
                    con_loc,
                    hook_loc,
                    pair["offset"]
                )
            )

            print(
                "  [OK] Pair {0}: {1}".format(
                    index + 1,
                    fk_name
                )
            )

            print(
                "       Rotate offset: "
                "X={0:.3f}, Y={1:.3f}, Z={2:.3f}".format(
                    rotate_x,
                    rotate_y,
                    rotate_z
                )
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


def validate_calibration_pairs(limbs, namespace):
    missing = []
    incomplete = []

    for limb_name, pairs in limbs.items():
        for index, pair in enumerate(pairs):
            fk_name = pair.get("fk_ctrl", "").strip()
            ik_name = pair.get("ik_ctrl", "").strip()

            if not fk_name or not ik_name:
                incomplete.append(
                    "{0} - Pair {1}".format(limb_name, index + 1)
                )
                continue

            fk_ctrl = ns_join(namespace, fk_name)
            ik_ctrl = ns_join(namespace, ik_name)

            if not cmds.objExists(fk_ctrl):
                missing.append(
                    "FK Control: {0}".format(fk_ctrl)
                )

            if not cmds.objExists(ik_ctrl):
                missing.append(
                    "IK Control: {0}".format(ik_ctrl)
                )

    return incomplete, missing


def validate_ik_controls(limbs, ik_controls):
    """
    Check that every limb in `limbs` has a complete _ik_controls entry
    (both ik_ctrl and pole_ctrl filled in) before FK -> IK pairs can
    be generated. Returns a list of human-readable problem strings,
    empty if everything is complete.
    """
    incomplete = []

    for limb_name in limbs:
        entry = ik_controls.get(limb_name, {})
        ik_ctrl = entry.get("ik_ctrl", "").strip()
        pole_ctrl = entry.get("pole_ctrl", "").strip()

        if not ik_ctrl or not pole_ctrl:
            missing_parts = []
            if not ik_ctrl:
                missing_parts.append("IK Control")
            if not pole_ctrl:
                missing_parts.append("Pole Vector Control")

            incomplete.append(
                "{0} - missing {1}".format(
                    limb_name, " and ".join(missing_parts)
                )
            )

    return incomplete


def generate_fk_to_ik_pairs(limbs, ik_controls, namespace, rotate_order=HOOK_ROTATE_ORDER):

    """
    Generate FK -> IK pair definitions from IK -> FK limb pairs.
    
    For each limb, creates three pair types:
    1. Hip/root (offset) - self-match with zero offset
    2. Pole vector - solved live from first/second/third FK controls
    3. Ankle/end (offset) - measured rotation offset between FK and IK
    
    ik_controls is the _ik_controls dict from the UI (per-limb real
    "ik_ctrl"/"pole_ctrl" values) - the real IK handle and pole
    vector control to write into the generated pairs, rather than
    guessing the pole name from limb_name or reusing the IK -> FK
    joint name.
    
    Returns a dict keyed by limb_name with generated pair lists.
    """
    fk_to_ik_limbs = {}
    
    print("")
    print("=" * 60)
    print("Generating FK -> IK Pair Definitions")
    print("=" * 60)
    
    for limb_name, pairs in limbs.items():
        if not pairs or len(pairs) < 2:
            print("")
            print("  [SKIPPED] {0}: requires at least 2 IK->FK pairs (hip, mid, end)".format(
                limb_name
            ))
            continue
        
        print("")
        print("=== {0} ===".format(limb_name))
        
        fk_to_ik_limbs[limb_name] = []
        
        # First pair: hip/root (self-match, zero offset)
        hip_pair = pairs[0]
        hip_fk = hip_pair.get("fk_ctrl", "").strip()
        
        if hip_fk:
            fk_to_ik_limbs[limb_name].append({
                "type": "offset",
                "fk_ctrl": hip_fk,
                "ik_ctrl": hip_fk,
                "offset": {
                    "rotate_order": rotate_order,
                    "translate": {"x": 0, "y": 0, "z": 0},
                    "rotate": {"x": 0, "y": 0, "z": 0},
                },
            })
            print("  [OK] Hip/root (self-match, zero offset): {0}".format(hip_fk))
        
        # Middle pair: pole vector (solved live from hip/mid/end)
        if len(pairs) >= 3:
            hip_fk = pairs[0].get("fk_ctrl", "").strip()
            mid_fk = pairs[1].get("fk_ctrl", "").strip()
            end_fk = pairs[2].get("fk_ctrl", "").strip()
            
            if hip_fk and mid_fk and end_fk:
                ik_pole = ik_controls.get(limb_name, {}).get("pole_ctrl", "").strip()

                if not ik_pole:
                    print(
                        "  [SKIPPED] Pole vector: no Pole Vector "
                        "Control set for {0}".format(limb_name)
                    )
                else:
                    fk_to_ik_limbs[limb_name].append({
                        "type": "pole_vector",
                        "shoulder_ctrl": hip_fk,
                        "elbow_ctrl": mid_fk,
                        "wrist_ctrl": end_fk,
                        "ik_ctrl": ik_pole,
                    })
                    print("  [OK] Pole vector (solved live): {0}".format(ik_pole))

        
        # Last pair: end effector (ankle/wrist) with measured rotation offset
        if len(pairs) >= 2:
            end_fk = pairs[-1].get("fk_ctrl", "").strip()
            end_ik_name = ik_controls.get(limb_name, {}).get("ik_ctrl", "").strip()
           
            if end_fk and end_ik_name:
                # Measure the rotation offset between FK end and IK end
                # end_ik_name is the real IK handle control (from
                # ik_controls), not the IK -> FK joint - the offset
                # needs to be measured against, and later used to
                # drive, the actual control that gets snapped.
                fk_ctrl = ns_join(namespace, end_fk)
                ik_ctrl = ns_join(namespace, end_ik_name)
            elif end_fk and not end_ik_name:
                print(
                    "  [SKIPPED] End effector: no IK Control set "
                    "for {0}".format(limb_name)
                )            
                rotate_x = 0
                rotate_y = 0
                rotate_z = 0
                
                try:
                    # Create temporary locators to measure the offset
                    con_loc = cmds.spaceLocator(name="TMP_measure_con")[0]
                    hook_loc = cmds.spaceLocator(name="TMP_measure_hook")[0]
                    
                    cmds.setAttr(con_loc + ".rotateOrder", rotate_order)
                    cmds.setAttr(hook_loc + ".rotateOrder", rotate_order)
                    cmds.parent(hook_loc, con_loc)
                    
                    # Snap to IK, then hook to FK
                    cmds.parentConstraint(ik_ctrl, con_loc, maintainOffset=False)
                    cmds.parentConstraint(fk_ctrl, hook_loc, maintainOffset=False)
                    
                    cmds.dgdirty(allPlugs=True)
                    cmds.refresh(force=True)
                    
                    # Read the offset
                    rotate_x = round(cmds.getAttr(hook_loc + ".rotateX"), 3)
                    rotate_y = round(cmds.getAttr(hook_loc + ".rotateY"), 3)
                    rotate_z = round(cmds.getAttr(hook_loc + ".rotateZ"), 3)
                    
                    cmds.delete(con_loc)
                    
                except Exception as exc:
                    print("  [WARNING] Could not measure IK rotation offset: {0}".format(exc))
                
                fk_to_ik_limbs[limb_name].append({
                    "type": "offset",
                    "fk_ctrl": end_fk,
                    "ik_ctrl": end_ik_name,
                    "offset": {
                        "rotate_order": rotate_order,
                        "translate": {"x": 0, "y": 0, "z": 0},
                        "rotate": {
                            "x": rotate_x,
                            "y": rotate_y,
                            "z": rotate_z,
                        },
                    },
                })
                print("  [OK] End effector (measured offset): {0}".format(end_fk))
                print("       Rotation offset: X={0:.3f}, Y={1:.3f}, Z={2:.3f}".format(
                    rotate_x, rotate_y, rotate_z
                ))
    
    print("")
    print("=" * 60)
    print("Generated {0} limbs".format(len(fk_to_ik_limbs)))
    print("=" * 60)
    print("")
    
    return fk_to_ik_limbs
