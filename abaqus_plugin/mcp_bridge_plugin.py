# Abaqus/CAE GUI plug-in (Python 2.7): runs scripts dropped by mcp_server.py into
# ~/.abaqus_mcp inside this CAE session and writes their output back as JSON.
# Install: copy this file and mcp_kernel.py to ~/abaqus_plugins/ and restart Abaqus/CAE.
import glob, json, os, time, traceback

try:
    from abaqusGui import *
except ImportError:  # outside Abaqus (tests): only poll_once is used
    FXObject = None

SPOOL = os.path.join(os.path.expanduser('~'), '.abaqus_mcp')
KERNEL = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'mcp_kernel.py')
POLL_MS = 500


def _log(msg):
    try:
        with open(os.path.join(SPOOL, 'bridge.log'), 'a') as f:
            f.write('%s %s\n' % (time.strftime('%H:%M:%S'), msg))
    except Exception:
        pass


def _write_error(out, msg):
    if not os.path.exists(out):
        with open(out + '.tmp', 'w') as f:
            json.dump({'ok': False, 'output': msg}, f)
        os.rename(out + '.tmp', out)


def poll_once(spool, run_job):
    """Refresh the heartbeat and run every pending job_<id>.py via run_job(run_path, out_path)."""
    with open(os.path.join(spool, 'heartbeat'), 'w') as f:
        f.write(str(time.time()))
    for job in sorted(glob.glob(os.path.join(spool, 'job_*.py'))):
        base = job[:-3]
        run, out = base + '.run', base + '.json'
        try:
            os.rename(job, run)  # marks the job as picked up for mcp_server.py
        except OSError:  # mcp_server.py gave up on it in the meantime
            continue
        try:
            run_job(run, out)
        except Exception:
            _write_error(out, 'Bridge error:\n' + traceback.format_exc())
        try:
            os.remove(run)
        except OSError:
            pass


def _run_in_kernel(run, out):
    # execfile on every job, so edits to mcp_kernel.py apply without restarting CAE
    sendCommand('execfile(%r); _mcp_run(%r, %r)' % (KERNEL, run, out))


if FXObject is not None:
    class McpPoller(FXObject):
        ID_TICK = 1

        def __init__(self):
            FXObject.__init__(self)
            FXMAPFUNC(self, SEL_TIMEOUT, self.ID_TICK, McpPoller.onTick)

        def arm(self):
            # FOX 1.x order used by Abaqus: (ms, target, selector)
            getAFXApp().addTimeout(POLL_MS, self, self.ID_TICK)

        def onTick(self, sender, sel, ptr):
            try:
                poll_once(SPOOL, _run_in_kernel)
            except Exception:
                _log(traceback.format_exc())
            self.arm()
            return 1

    if not os.path.isdir(SPOOL):
        os.makedirs(SPOOL)
    _mcp_poller = McpPoller()  # module global keeps it alive
    try:
        _mcp_poller.arm()
        _log('bridge started')
    except Exception:
        _log('bridge failed to start:\n' + traceback.format_exc())
