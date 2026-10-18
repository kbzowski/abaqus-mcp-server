from mcp.server.fastmcp import FastMCP
import json
import os
import re
import time
import uuid

mcp = FastMCP(
    "ABAQUS Script Executor",
    instructions="Executes Python scripts inside a running Abaqus/CAE session through the mcp_bridge plug-in and returns their output."
)

# Shared with abaqus_plugin/mcp_bridge_plugin.py
SPOOL = os.path.join(os.path.expanduser("~"), ".abaqus_mcp")
PICKUP_TIMEOUT = 10.0  # seconds for the plug-in to take a job before we assume it is not running
POLL_INTERVAL = 0.2


def _bridge_alive() -> bool:
    try:
        return time.time() - os.path.getmtime(os.path.join(SPOOL, "heartbeat")) < 5.0
    except OSError:
        return False


@mcp.tool(
    name="execute_script",
    description="Executes Python 2.7 code in the running Abaqus/CAE session (kernel, same namespace as the CAE command line, so mdb, session and variables from earlier calls are available). Returns everything the script printed plus the traceback if it failed. Check unfamiliar API with the describe tool first."
)
def execute_script(python_code: str, timeout: float = 600.0) -> str:
    """
    Drops the code into the spool directory as job_<id>.py and waits for the
    Abaqus plug-in to write job_<id>.json with {"ok": bool, "output": str}.
    """
    os.makedirs(SPOOL, exist_ok=True)
    job_id = time.strftime("%Y%m%d_%H%M%S_") + uuid.uuid4().hex[:6]
    job = os.path.join(SPOOL, f"job_{job_id}.py")
    out = os.path.join(SPOOL, f"job_{job_id}.json")

    # write under another name first so the plug-in never reads a half-written file
    with open(job + ".tmp", "w", encoding="utf-8") as f:
        f.write(python_code)
    os.replace(job + ".tmp", job)

    start = time.time()
    while not os.path.exists(out):
        waited = time.time() - start
        if os.path.exists(job) and waited > PICKUP_TIMEOUT:
            try:
                os.remove(job)
            except FileNotFoundError:  # the plug-in picked it up just now
                continue
            if _bridge_alive():
                return f"Abaqus is busy (job not picked up within {PICKUP_TIMEOUT:.0f} s). Try again later."
            return ("Abaqus bridge is not running. Start Abaqus/CAE with mcp_bridge_plugin.py "
                    "installed in ~/abaqus_plugins (see README).")
        if waited > timeout:
            return f"Script still running after {timeout:.0f} s. Its result will be written to {out}."
        time.sleep(POLL_INTERVAL)

    with open(out, encoding="utf-8") as f:
        result = json.load(f)
    os.remove(out)
    status = "OK" if result["ok"] else "FAILED"
    return f"[{status}]\n{result['output']}".rstrip()


# Names, attribute access and [index] / ['key'] lookups only: no calls, so describe cannot change the model.
DESCRIBE_PATH = re.compile(r"""[A-Za-z_]\w*(\.[A-Za-z_]\w*|\[(\d+|'[^'\\]*'|"[^"\\]*")\])*""")

# Runs in the kernel (Python 2.7 or 3); wrapped in a function so it leaves no names in the CAE namespace.
DESCRIBE_CODE = """\
def _mcp_describe(o):
    print('TYPE: ' + type(o).__name__)
    doc = getattr(o, '__doc__', None)
    if doc:
        print('DOC: ' + str(doc).strip())
    members = [m for m in getattr(o, '__members__', None) or [] if not m.startswith('_')]
    if members:
        print('MEMBERS: ' + ', '.join(sorted(members)))
    if hasattr(o, 'keys'):
        try:
            print('KEYS: ' + ', '.join(str(k) for k in o.keys()))
        except Exception:
            pass
    print('METHODS: ' + ', '.join(n for n in dir(o) if not n.startswith('_') and n not in members and callable(getattr(o, n, None))))
try:
    _mcp_describe(%s)
finally:
    del _mcp_describe
"""


@mcp.tool(
    name="describe",
    description="Read-only lookup of the Abaqus scripting API in the running session: signature/docstring, attributes (__members__), repository keys and methods of an object. Takes a path like mdb.models['Model-1'].HeatTransferStep, mdb.models['Model-1'].steps or mesh.ElemType (no calls). Use it before calling a method you are not sure about: the installed Abaqus version is authoritative, not memory."
)
def describe(path: str) -> str:
    path = path.strip()
    if not DESCRIBE_PATH.fullmatch(path):
        return "Invalid path: use names, attributes and ['key'] / [index] lookups only, e.g. mdb.models['Model-1'].parts"
    return execute_script(DESCRIBE_CODE % path, timeout=60)


@mcp.prompt()
def abaqus_scripting_strategy() -> str:
    """Guidance for driving Abaqus/CAE through this server."""
    return """When working in Abaqus/CAE through this MCP server:

    1. Use `execute_script` with Abaqus Scripting Interface code. The interpreter is Abaqus's own
       Python 2.7: use `print 'x'` or `print('x')`, no f-strings, no Python 3-only syntax.
    2. Code runs in the live CAE session, like typing it in the CAE command line: changes appear in the
       GUI and variables persist between calls. `mdb`, `session` and abaqusConstants are already imported.
    3. The result starts with [OK] or [FAILED] followed by everything printed and the traceback.
       Print what you need to verify (counts, names, values) instead of assuming success.
    4. Do not rely on memory for method names and arguments: the API changed between Abaqus versions.
       Call `describe` on the method or object first (e.g. mdb.models['M'].HeatTransferStep).
    5. Build models in small steps and check each one; avoid scripts that wait for user input.
    6. Submitting a job (`mdb.jobs[...].submit()`) returns immediately; use
       `mdb.jobs[...].waitForCompletion()` when you need the results in the same call.
    """


if __name__ == "__main__":
    mcp.run()
