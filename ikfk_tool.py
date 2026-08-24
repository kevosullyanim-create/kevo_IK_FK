"""
ikfk_tool.py

Entry point. Run this from Maya's Script Editor (Python tab).

Everything else in this folder (ikfk_io.py, ikfk_calibrate.py,
ikfk_snap_common.py, ikfk_switch_ik_to_fk.py, ikfk_switch_fk_to_ik.py,
ikfk_ui.py) needs to be importable, i.e. on sys.path. The two lines
below add this file's own folder to sys.path so it works regardless of
where the folder lives on disk - drop the whole ikfk_tool/ directory
anywhere and run this file.

NOTE: this replaces the old single ikfk_switch.py - delete that file
from your scripts folder if it's still there, it's been split into
ikfk_snap_common.py / ikfk_switch_ik_to_fk.py / ikfk_switch_fk_to_ik.py.
"""
import os
import sys

# __file__ is only set when this script is run via a real import (or
# Maya's "Source Script" / execfile-style loading). Running it via
# exec(open(path).read()) from the Script Editor does NOT set it, so
# fall back to a hardcoded path in that case - update this if you move
# the folder.
try:
    _THIS_DIR = os.path.dirname(os.path.abspath(__file__))
except NameError:
    _THIS_DIR = "/home/kevin-o/maya/scripts/IK_FK"

if _THIS_DIR not in sys.path:
    sys.path.insert(0, _THIS_DIR)

import importlib
import ikfk_io, ikfk_calibrate, ikfk_snap_common
import ikfk_switch_ik_to_fk, ikfk_switch_fk_to_ik, ikfk_ui

# Re-running this in the same Maya session after editing one of the
# other files would otherwise use Python's cached OLD version - force
# a reload every time so edits always take effect without restarting
# Maya. Cheap enough to leave on permanently. Order matters: modules
# that get imported BY other modules here (io, common) reload first,
# so the ones that depend on them (the switch modules, then ui) pick
# up the fresh versions rather than binding to stale ones.
for _mod in (
        ikfk_io,
        ikfk_calibrate,
        ikfk_snap_common,
        ikfk_switch_ik_to_fk,
        ikfk_switch_fk_to_ik,
        ikfk_ui):
    importlib.reload(_mod)

ikfk_ui.show_ui()
