from mcp.server.fastmcp import FastMCP
import asyncio
import glob
import json
import os
import re
import shutil
import subprocess
import time
import uuid

mcp = FastMCP(
    "ABAQUS Script Executor",
    instructions="Executes Python scripts inside a running Abaqus/CAE session through the mcp_bridge plug-in and returns their output."
)

SPOOL = os.path.join(os.path.expanduser("~"), ".abaqus_mcp")
PLUGIN_SOURCE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "abaqus_plugin")
PLUGIN_TARGET = os.path.join(os.path.expanduser("~"), "abaqus_plugins")
PICKUP_TIMEOUT_S = 10.0
POLL_INTERVAL = 0.05
# Where Abaqus installers put the per-version launchers abq<version>.bat (6.x: SIMULIA/Abaqus/Commands, 2016+: SIMULIA/Commands)
COMMANDS_GLOBS = ["[C-Z]:/SIMULIA/Commands", "[C-Z]:/SIMULIA/*/Commands"]
VERSION_LAUNCHER = re.compile(r"abq(\d\w*)\.bat", re.IGNORECASE)


def _bridge_alive() -> bool:
    try:
        return time.time() - os.path.getmtime(os.path.join(SPOOL, "heartbeat")) < 5.0
    except OSError:
        return False


def _running_job() -> str | None:
    """The heartbeat stops while a job runs (the plug-in waits for the kernel), so a claimed job means busy, not dead."""
    running = glob.glob(os.path.join(SPOOL, "job_*.run"))
    return running[0] if running else None


def _write_whole_before_visible(path: str, text: str) -> None:
    with open(path + ".tmp", "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(path + ".tmp", path)


@mcp.tool(
    name="execute_script",
    description="Executes Python 2.7 code in the running Abaqus/CAE session (kernel, same namespace as the CAE command line, so mdb, session and variables from earlier calls are available). Returns everything the script printed plus the traceback if it failed. Check unfamiliar API with the describe tool first."
)
async def execute_script(python_code: str, timeout: float = 600.0) -> str:
    os.makedirs(SPOOL, exist_ok=True)
    job_id = time.strftime("%Y%m%d_%H%M%S_") + uuid.uuid4().hex[:6]
    job = os.path.join(SPOOL, f"job_{job_id}.py")
    out = os.path.join(SPOOL, f"job_{job_id}.json")

    _write_whole_before_visible(job, python_code)

    start = time.time()
    while not os.path.exists(out):
        waited = time.time() - start
        if os.path.exists(job) and waited > PICKUP_TIMEOUT_S:
            try:
                os.remove(job)
            except FileNotFoundError:
                continue
            running = _running_job()
            if running:
                return f"Abaqus is busy with {running}. Try again later (if Abaqus was closed, delete that file)."
            if _bridge_alive():
                return f"Abaqus is busy (job not picked up within {PICKUP_TIMEOUT_S:.0f} s). Try again later."
            return "Abaqus bridge is not running. Start Abaqus/CAE with the launch_abaqus tool."
        if waited > timeout:
            return f"Script still running after {timeout:.0f} s. Its result will be written to {out}."
        await asyncio.sleep(POLL_INTERVAL)

    with open(out, encoding="utf-8") as f:
        result = json.load(f)
    os.remove(out)
    status = "OK" if result["ok"] else "FAILED"
    return f"[{status}]\n{result['output']}".rstrip()


def find_installations() -> dict[str, str]:
    dirs = [d for g in COMMANDS_GLOBS for d in glob.glob(g)]
    on_path = shutil.which("abaqus")
    if on_path:
        dirs.append(os.path.dirname(on_path))
    found = {}
    for d in dirs:
        for f in glob.glob(os.path.join(d, "abq*.bat")):
            m = VERSION_LAUNCHER.fullmatch(os.path.basename(f))
            if m:
                found.setdefault(m.group(1), os.path.normpath(f))
    return found


@mcp.tool(
    name="list_abaqus_installations",
    description="Lists the Abaqus versions installed on this computer with their launchers (abq<version>.bat). Use the version with launch_abaqus."
)
def list_abaqus_installations() -> str:
    return _format_installations(find_installations())


def _format_installations(found: dict[str, str]) -> str:
    if not found:
        return "No Abaqus installation found (looked for abq<version>.bat in SIMULIA/Commands folders and next to abaqus on PATH)."
    return "\n".join(f"{v}: {path}" for v, path in sorted(found.items()))


@mcp.tool(
    name="launch_abaqus",
    description="Starts Abaqus/CAE and waits until the mcp_bridge plug-in answers, so execute_script can be used right after. version is a key from list_abaqus_installations (optional when only one is installed). work_dir is where Abaqus writes its files (abaqus.rpy, jobs); default is the user's home folder."
)
async def launch_abaqus(version: str = "", work_dir: str = "", wait: float = 180.0) -> str:
    if _bridge_alive() or _running_job():
        return "Abaqus/CAE with the bridge is already running."
    found = find_installations()
    if not found:
        return _format_installations(found)
    if not version and len(found) == 1:
        version = next(iter(found))
    if version not in found:
        return "Choose a version:\n" + _format_installations(found)
    work_dir = work_dir or os.path.expanduser("~")
    os.makedirs(PLUGIN_TARGET, exist_ok=True)
    for f in glob.glob(os.path.join(PLUGIN_SOURCE, "*.py")):
        shutil.copy(f, PLUGIN_TARGET)
    # no window and no inherited stdio: the MCP protocol runs over this server's stdout
    proc = subprocess.Popen([found[version], "cae"], cwd=work_dir, stdin=subprocess.DEVNULL,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            creationflags=subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP)
    start = time.time()
    while time.time() - start < wait:
        if _bridge_alive():
            return f"Abaqus {version} started in {work_dir}, bridge ready."
        if proc.poll():
            return f"Abaqus {version} exited with code {proc.returncode} ({found[version]} cae in {work_dir})."
        await asyncio.sleep(1)
    return (f"Abaqus {version} started in {work_dir}, but the bridge did not answer within {wait:.0f} s. "
            "CAE may still be loading or a license dialog is open; see ~/.abaqus_mcp/bridge.log.")


CALL_FREE_PATH = re.compile(r"""[A-Za-z_]\w*(\.[A-Za-z_]\w*|\[(\d+|'[^'\\]*'|"[^"\\]*")\])*""")

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
async def describe(path: str) -> str:
    path = path.strip()
    if not CALL_FREE_PATH.fullmatch(path):
        return "Invalid path: use names, attributes and ['key'] / [index] lookups only, e.g. mdb.models['Model-1'].parts"
    return await execute_script(DESCRIBE_CODE % path, timeout=60)


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
