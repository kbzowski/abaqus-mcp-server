import glob, json, os, time, traceback

try:
    from abaqusGui import *
except ImportError:
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
    with open(os.path.join(spool, 'heartbeat'), 'w') as f:
        f.write(str(time.time()))
    for job in sorted(glob.glob(os.path.join(spool, 'job_*.py'))):
        base = job[:-3]
        run, out = base + '.run', base + '.json'
        try:
            os.rename(job, run)
        except OSError:
            continue
        try:
            run_job(run, out)
        except Exception:
            _write_error(out, 'Bridge error:\n' + traceback.format_exc())
        try:
            os.remove(run)
        except OSError:
            pass


def _reload_kernel_and_run(run, out):
    # a fresh dict per job, so the kernel's imports and helpers never shadow names in the CAE namespace
    sendCommand('(lambda ns: (execfile(%r, ns), ns["_mcp_run"](%r, %r)))({})' % (KERNEL, run, out))


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
                poll_once(SPOOL, _reload_kernel_and_run)
            except Exception:
                _log(traceback.format_exc())
            self.arm()
            return 1

    if not os.path.isdir(SPOOL):
        os.makedirs(SPOOL)
    _mcp_poller_keepalive = McpPoller()
    try:
        _mcp_poller_keepalive.arm()
        _log('bridge started')
    except Exception:
        _log('bridge failed to start:\n' + traceback.format_exc())
