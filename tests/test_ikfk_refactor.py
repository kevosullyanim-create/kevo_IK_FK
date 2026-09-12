import json
import os
import sys
import tempfile
import types
import unittest
from unittest import mock


REPO_ROOT = os.path.dirname(os.path.dirname(__file__))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)


maya_module = types.ModuleType("maya")
cmds_stub = types.SimpleNamespace(confirmDialog=lambda **_kwargs: None)
maya_module.cmds = cmds_stub
sys.modules.setdefault("maya", maya_module)
sys.modules["maya.cmds"] = cmds_stub


import ikfk_calibrate  # noqa: E402
import ikfk_io  # noqa: E402


class LoadLimbsMigrationTests(unittest.TestCase):

    def test_load_limbs_migrates_flat_and_legacy_sections(self):
        legacy_data = {
            "L_arm": [
                {"fk_ctrl": "L_arm_fk_000_CTRL", "ik_ctrl": "L_arm000_JNT"},
                {"fk_ctrl": "L_arm_fk_001_CTRL", "ik_ctrl": "L_arm001_JNT"},
                {"fk_ctrl": "L_arm_fk_002_CTRL", "ik_ctrl": "L_arm002_JNT"},
            ],
            "fk_to_ik": {
                "L_arm": [
                    {
                        "type": "offset",
                        "fk_ctrl": "L_arm_fk_002_CTRL",
                        "ik_ctrl": "L_arm_ik_CTRL",
                        "offset": {
                            "rotate_order": 2,
                            "translate": {"x": 0, "y": 0, "z": 0},
                            "rotate": {"x": 0, "y": -90, "z": 0},
                        },
                    }
                ]
            },
            "ik_controls": {
                "L_arm": {
                    "ik_ctrl": "L_arm_ik_CTRL",
                    "pole_ctrl": "L_arm_ik_pole_CTRL",
                }
            },
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            path = os.path.join(temp_dir, "legacy.json")
            with open(path, "w") as handle:
                json.dump(legacy_data, handle)

            limbs = ikfk_io.load_limbs(path)

        self.assertIn("L_arm", limbs)
        self.assertEqual(
            "L_arm000_JNT",
            limbs["L_arm"]["fk_match_ik"][0]["source"]
        )
        self.assertNotIn(
            "ik_ctrl",
            limbs["L_arm"]["fk_match_ik"][0]
        )

        ik_match_fk = limbs["L_arm"]["ik_match_fk"]
        self.assertEqual("L_arm_fk_002_CTRL", ik_match_fk[0]["source"])
        self.assertEqual("L_arm_ik_CTRL", ik_match_fk[0]["ik_ctrl"])

        pole_pairs = [
            pair for pair in ik_match_fk
            if pair.get("type") == "pole_vector"
        ]
        self.assertEqual(1, len(pole_pairs))
        self.assertEqual(
            "L_arm_ik_pole_CTRL",
            pole_pairs[0]["ik_ctrl"]
        )

    def test_load_limbs_prefers_legacy_ik_control_match_for_handle_role(self):
        legacy_data = {
            "L_arm": [
                {"fk_ctrl": "L_arm_fk_000_CTRL", "ik_ctrl": "L_arm000_JNT"},
                {"fk_ctrl": "L_arm_fk_001_CTRL", "ik_ctrl": "L_arm001_JNT"},
                {"fk_ctrl": "L_arm_fk_002_CTRL", "ik_ctrl": "L_arm002_JNT"},
            ],
            "fk_to_ik": {
                "L_arm": [
                    {
                        "type": "offset",
                        "fk_ctrl": "L_arm_fk_002_CTRL",
                        "ik_ctrl": "L_arm_ik_CTRL",
                        "offset": {
                            "rotate_order": 2,
                            "translate": {"x": 0, "y": 0, "z": 0},
                            "rotate": {"x": 0, "y": -90, "z": 0},
                        },
                    },
                    {
                        "type": "offset",
                        "ik_ctrl": "L_arm_extra_ik_CTRL",
                        "source": "L_arm_extra_fk_CTRL",
                        "offset": {
                            "rotate_order": 2,
                            "translate": {"x": 1, "y": 2, "z": 3},
                            "rotate": {"x": 4, "y": 5, "z": 6},
                        },
                    },
                ]
            },
            "ik_controls": {
                "L_arm": {
                    "ik_ctrl": "L_arm_ik_CTRL",
                    "pole_ctrl": "L_arm_ik_pole_CTRL",
                }
            },
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            path = os.path.join(temp_dir, "legacy-extra.json")
            with open(path, "w") as handle:
                json.dump(legacy_data, handle)

            limbs = ikfk_io.load_limbs(path)

        ik_match_fk = limbs["L_arm"]["ik_match_fk"]
        handle_pairs = [
            pair for pair in ik_match_fk
            if pair.get("role") == "ik_handle"
        ]
        extra_pairs = [
            pair for pair in ik_match_fk
            if pair.get("ik_ctrl") == "L_arm_extra_ik_CTRL"
        ]

        self.assertEqual(1, len(handle_pairs))
        self.assertEqual("L_arm_ik_CTRL", handle_pairs[0]["ik_ctrl"])
        self.assertEqual(1, len(extra_pairs))
        self.assertNotEqual("ik_handle", extra_pairs[0].get("role"))

    def test_load_limbs_dedupes_legacy_pole_vector_entries(self):
        legacy_data = {
            "L_arm": [
                {"fk_ctrl": "L_arm_fk_000_CTRL", "ik_ctrl": "L_arm000_JNT"},
                {"fk_ctrl": "L_arm_fk_001_CTRL", "ik_ctrl": "L_arm001_JNT"},
                {"fk_ctrl": "L_arm_fk_002_CTRL", "ik_ctrl": "L_arm002_JNT"},
            ],
            "fk_to_ik": {
                "L_arm": [
                    {
                        "type": "offset",
                        "fk_ctrl": "L_arm_fk_002_CTRL",
                        "ik_ctrl": "L_arm_ik_CTRL",
                    },
                    {
                        "ik_ctrl": "L_arm_ik_pole_CTRL",
                        "role": "pole_vector",
                        "shoulder_ctrl": "L_arm_fk_000_CTRL",
                        "elbow_ctrl": "L_arm_fk_001_CTRL",
                        "wrist_ctrl": "L_arm_fk_002_CTRL",
                    },
                ]
            },
            "ik_controls": {
                "L_arm": {
                    "ik_ctrl": "L_arm_ik_CTRL",
                    "pole_ctrl": "L_arm_ik_pole_CTRL",
                }
            },
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            path = os.path.join(temp_dir, "legacy-pole.json")
            with open(path, "w") as handle:
                json.dump(legacy_data, handle)

            limbs = ikfk_io.load_limbs(path)

        pole_pairs = [
            pair for pair in limbs["L_arm"]["ik_match_fk"]
            if pair.get("role") == "pole_vector"
        ]

        self.assertEqual(1, len(pole_pairs))
        self.assertEqual("L_arm_ik_pole_CTRL", pole_pairs[0]["ik_ctrl"])

    def test_save_helpers_write_nested_structure(self):
        fk_match_ik = {
            "L_arm": [
                {
                    "fk_ctrl": "L_arm_fk_000_CTRL",
                    "source": "L_arm000_JNT",
                    "use_for_pole_vector": True,
                }
            ]
        }
        ik_match_fk = {
            "L_arm": [
                {
                    "type": "offset",
                    "role": "ik_handle",
                    "ik_ctrl": "L_arm_ik_CTRL",
                    "source": "L_arm_fk_002_CTRL",
                    "offset": {
                        "rotate_order": 2,
                        "translate": {"x": 0, "y": 0, "z": 0},
                        "rotate": {"x": 0, "y": -90, "z": 0},
                    },
                },
                {
                    "type": "pole_vector",
                    "role": "pole_vector",
                    "ik_ctrl": "L_arm_ik_pole_CTRL",
                    "shoulder_ctrl": "L_arm_fk_000_CTRL",
                    "elbow_ctrl": "L_arm_fk_001_CTRL",
                    "wrist_ctrl": "L_arm_fk_002_CTRL",
                },
            ]
        }
        switch_settings = {
            "L_arm": {
                "object": "L_arm_CMP|input",
                "attr_name": "L_arm_ikfk_bl",
                "ik_value": 0,
                "fk_value": 1,
            }
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            path = os.path.join(temp_dir, "nested.json")
            with open(path, "w") as handle:
                json.dump(
                    {
                        "R_arm": {
                            "fk_match_ik": [{"fk_ctrl": "stale_fk", "source": "stale_jnt"}],
                            "ik_match_fk": [{"type": "offset", "ik_ctrl": "stale_ik", "source": "stale_fk"}],
                        }
                    },
                    handle
                )
            ikfk_io.save_fk_match_ik_pairs(fk_match_ik, path)
            ikfk_io.save_ik_match_fk_pairs(ik_match_fk, path)
            ikfk_io.save_switch_settings(switch_settings, path)

            with open(path, "r") as handle:
                raw_data = json.load(handle)

            loaded = ikfk_io.load_limbs(path)

        self.assertIn("L_arm", raw_data)
        self.assertIn("switch_settings", raw_data)
        self.assertIn("R_arm", raw_data)
        self.assertNotIn("fk_to_ik", raw_data)
        self.assertNotIn("ik_controls", raw_data)
        self.assertEqual(
            fk_match_ik["L_arm"][0]["source"],
            raw_data["L_arm"]["fk_match_ik"][0]["source"]
        )
        self.assertEqual(
            "L_arm_ik_CTRL",
            loaded["L_arm"]["ik_match_fk"][0]["ik_ctrl"]
        )

    def test_save_limbs_writes_complete_current_limb_set(self):
        limbs = {
            "L_arm": {
                "fk_match_ik": [
                    {
                        "fk_ctrl": "L_arm_fk_000_CTRL",
                        "source": "L_arm000_JNT",
                        "use_for_pole_vector": True,
                    }
                ],
                "ik_match_fk": [
                    {
                        "type": "offset",
                        "role": "ik_handle",
                        "ik_ctrl": "L_arm_ik_CTRL",
                        "source": "L_arm_fk_002_CTRL",
                    },
                    {
                        "type": "pole_vector",
                        "role": "pole_vector",
                        "ik_ctrl": "L_arm_ik_pole_CTRL",
                    },
                ],
            }
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            path = os.path.join(temp_dir, "limbs.json")
            with open(path, "w") as handle:
                json.dump({"R_arm": {"fk_match_ik": [], "ik_match_fk": []}}, handle)

            ikfk_io.save_limbs(limbs, path)

            with open(path, "r") as handle:
                raw_data = json.load(handle)

        self.assertIn("L_arm", raw_data)
        self.assertNotIn("R_arm", raw_data)


class GenerateIkMatchFkPairsTests(unittest.TestCase):

    def test_generate_ik_match_fk_pairs_uses_marked_pv_triplet(self):
        config = {
            "L_arm": {
                "fk_match_ik": [
                    {"fk_ctrl": "clav", "source": "clav_jnt", "use_for_pole_vector": False},
                    {"fk_ctrl": "shoulder_fk", "source": "shoulder_jnt", "use_for_pole_vector": True},
                    {"fk_ctrl": "elbow_fk", "source": "elbow_jnt", "use_for_pole_vector": True},
                    {"fk_ctrl": "wrist_fk", "source": "wrist_jnt", "use_for_pole_vector": True},
                ],
                "ik_match_fk": [
                    {
                        "type": "offset",
                        "ik_ctrl": "extra_ik",
                        "source": "extra_fk",
                        "offset": {
                            "rotate_order": 2,
                            "translate": {"x": 1, "y": 2, "z": 3},
                            "rotate": {"x": 4, "y": 5, "z": 6},
                        },
                    },
                    {"type": "offset", "role": "ik_handle", "ik_ctrl": "ik_handle", "source": ""},
                    {
                        "type": "pole_vector",
                        "role": "pole_vector",
                        "ik_ctrl": "pv_ctrl",
                        "shoulder_ctrl": "",
                        "elbow_ctrl": "",
                        "wrist_ctrl": "",
                    },
                ],
            }
        }

        with mock.patch.object(
                ikfk_calibrate,
                "_measure_ik_handle_offset",
                return_value=(10.0, 20.0, 30.0)) as measure_mock:
            generated = ikfk_calibrate.generate_ik_match_fk_pairs(
                config,
                "char"
            )

        self.assertIn("L_arm", generated)
        limb_pairs = generated["L_arm"]
        self.assertEqual("extra_fk", limb_pairs[0]["source"])
        self.assertEqual("ik_handle", limb_pairs[1]["ik_ctrl"])
        self.assertEqual("wrist_fk", limb_pairs[1]["source"])
        self.assertEqual(20.0, limb_pairs[1]["offset"]["rotate"]["y"])
        self.assertEqual("pv_ctrl", limb_pairs[2]["ik_ctrl"])
        self.assertEqual("shoulder_fk", limb_pairs[2]["shoulder_ctrl"])
        self.assertEqual("elbow_fk", limb_pairs[2]["elbow_ctrl"])
        self.assertEqual("wrist_fk", limb_pairs[2]["wrist_ctrl"])

        measure_mock.assert_called_once_with(
            source_ctrl="char:wrist_fk",
            ik_ctrl="char:ik_handle",
            rotate_order=ikfk_io.HOOK_ROTATE_ORDER
        )

    def test_generate_ik_match_fk_pairs_requires_exactly_three_pv_pairs(self):
        config = {
            "L_arm": {
                "fk_match_ik": [
                    {"fk_ctrl": "shoulder_fk", "source": "shoulder_jnt", "use_for_pole_vector": True},
                    {"fk_ctrl": "elbow_fk", "source": "elbow_jnt", "use_for_pole_vector": True},
                ],
                "ik_match_fk": [
                    {"type": "offset", "role": "ik_handle", "ik_ctrl": "ik_handle", "source": ""},
                    {"type": "pole_vector", "role": "pole_vector", "ik_ctrl": "pv_ctrl"},
                ],
            }
        }

        with self.assertRaises(ValueError) as error:
            ikfk_calibrate.generate_ik_match_fk_pairs(config, "char")

        self.assertIn("exactly 3 FK match IK pairs must be marked PV", str(error.exception))


if __name__ == "__main__":
    unittest.main()
