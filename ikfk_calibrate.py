"""
ikfk_calibrate.py

Calibration-tab logic: build temporary locator pairs between an
IK-driven source and its FK control, read the rotation offset between
them, and clean the locators up once the offset is safely written to
the JSON config (handled by the caller, in ikfk_ui.py).
"""
import maya.cmds as cmds

from ikfk_io import ns_join, HOOK_ROTATE_ORDER


def build_calibration_locators(
        limbs,
        source_namespace,
        target_namespace,
        rotate_order=HOOK_ROTATE_ORDER):

    if not source_namespace or not target_namespace:
        raise ValueError(
            "Both a source and target namespace are required."
        )

    created = []

    print("")
    print("=" * 60)
    print("IK -> FK Calibration Locator Build")
    print("Source namespace: {0}".format(source_namespace))
    print("Target namespace: {0}".format(target_namespace))
    print("=" * 60)

    for limb_name, pairs in limbs.items():
        print("")
        print("=== {0} ===".format(limb_name))

        for index, pair in enumerate(pairs):
            fk_name = pair.get("fk_ctrl", "").strip()
            source_name = pair.get("source", "").strip()

            if not fk_name or not source_name:
                print(
                    "  [SKIPPED] Pair {0}: incomplete".format(
                        index + 1
                    )
                )
                continue

            source = ns_join(source_namespace, source_name)
            fk_ctrl = ns_join(target_namespace, fk_name)

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

            # Snap the parent locator to the source rig.
            cmds.parentConstraint(
                source,
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

            # Store the calibration data in the configuration dictionary.
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


def validate_calibration_pairs(limbs, source_namespace, target_namespace):
    missing = []
    incomplete = []

    for limb_name, pairs in limbs.items():
        for index, pair in enumerate(pairs):
            fk_name = pair.get("fk_ctrl", "").strip()
            source_name = pair.get("source", "").strip()

            if not fk_name or not source_name:
                incomplete.append(
                    "{0} - Pair {1}".format(limb_name, index + 1)
                )
                continue

            source = ns_join(source_namespace, source_name)
            fk_ctrl = ns_join(target_namespace, fk_name)

            if not cmds.objExists(source):
                missing.append(
                    "Source: {0}".format(source)
                )

            if not cmds.objExists(fk_ctrl):
                missing.append(
                    "Target: {0}".format(fk_ctrl)
                )

    return incomplete, missing
