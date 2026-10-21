"""Headless bridge: abaqus cae noGUI=mcp_nogui.py. Serves jobs from the spool until the process is killed."""
import inspect, os, sys, time, traceback

# noGUI scripts run through execfile, so __file__ is not reliable
sys.path.insert(0, os.path.dirname(os.path.abspath(inspect.getfile(inspect.currentframe()))))
from mcp_bridge_plugin import SPOOL, POLL_MS, poll_once, _log
from mcp_kernel import _mcp_run

# same names as the CAE command line has preloaded; kept across jobs like the GUI namespace
_mcp_ns = {}
exec('from abaqus import *\nfrom abaqusConstants import *\nfrom caeModules import *\n', _mcp_ns)

if not os.path.isdir(SPOOL):
    os.makedirs(SPOOL)
_log('headless bridge started')
while True:
    try:
        poll_once(SPOOL, lambda run, out: _mcp_run(run, out, _mcp_ns))
    except Exception:
        _log(traceback.format_exc())
    time.sleep(POLL_MS / 1000.0)
