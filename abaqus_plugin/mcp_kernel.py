# Runs one job inside the Abaqus/CAE kernel (where mdb and session live).
# Loaded with execfile by mcp_bridge_plugin.py, so it must work on Abaqus's
# Python 2.7 as well as on Python 3 (tests).
import json, os, sys, traceback
try:
    from StringIO import StringIO
except ImportError:
    from io import StringIO


class _McpTee(object):
    """Captures into buf and echoes to the CAE message area (echo is best effort)."""

    def __init__(self, echo, buf):
        self.echo, self.buf = echo, buf

    def write(self, s):
        self.buf.write(s)
        try:
            self.echo.write(s)
        except Exception:  # e.g. a character the console encoding cannot represent
            pass

    def flush(self):
        try:
            self.echo.flush()
        except Exception:
            pass


def _mcp_run(job, out, namespace=None):
    """Execute job (UTF-8 source file), write {"ok", "output"} JSON to out."""
    if namespace is None:
        import __main__
        # same namespace as the CAE command line, so variables persist between jobs
        namespace = __main__.__dict__
    buf = StringIO()
    old = sys.stdout, sys.stderr
    sys.stdout = _McpTee(old[0], buf)
    sys.stderr = _McpTee(old[1], buf)
    ok = True
    try:
        with open(job, 'rb') as f:
            code = f.read().decode('utf-8')
        exec(compile(code, job, 'exec'), namespace)
    except:  # anything the script raises, including SystemExit, is its result
        ok = False
        traceback.print_exc()
    finally:
        sys.stdout, sys.stderr = old
    with open(out + '.tmp', 'w') as f:
        json.dump({'ok': ok, 'output': buf.getvalue()}, f)
    os.rename(out + '.tmp', out)
