import json, os, sys, traceback
try:
    from StringIO import StringIO
except ImportError:
    from io import StringIO


class _McpTee(object):
    def __init__(self, echo, buf):
        self.echo, self.buf = echo, buf

    def write(self, s):
        self.buf.write(s)
        try:
            self.echo.write(s)
        except Exception:
            pass

    def flush(self):
        try:
            self.echo.flush()
        except Exception:
            pass


def _mcp_run(job, out, namespace=None):
    if namespace is None:
        import __main__
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
    except BaseException:
        ok = False
        traceback.print_exc()
    finally:
        sys.stdout, sys.stderr = old
    with open(out + '.tmp', 'w') as f:
        json.dump({'ok': ok, 'output': buf.getvalue()}, f)
    os.rename(out + '.tmp', out)
