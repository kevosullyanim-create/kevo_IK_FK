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


def generate_fk_to_ik_pairs(limbs, namespace, rotate_order=HOOK_ROTATE_ORDER):
    """
    Generate FK -> IK pair definitions from IK -> FK limb pairs.
    
    For each limb, creates three pair types:
    1. Hip/root (offset) - self-match with zero offset
    2. Pole vector - solved live from first/second/third FK controls
    3. Ankle/end (offset) - measured rotation offset between FK and IK
    
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
                # Infer IK pole vector name from limb name (e.g., L_arm -> L_arm_ik_pole_CTRL)
                limb_base = limb_name.rsplit("_", 1)[0] if "_" in limb_name else limb_name
                ik_pole = "{0}_ik_pole_CTRL".format(limb_base)
                
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
            end_ik_name = pairs[-1].get("ik_ctrl", "").strip()
            
            if end_fk and end_ik_name:
                # Measure the rotation offset between FK end and IK end
                fk_ctrl = ns_join(namespace, end_fk)
                ik_ctrl = ns_join(namespace, end_ik_name)
                
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
