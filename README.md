# Abaqus MCP Server

MCP server that runs Python scripts inside a **live Abaqus/CAE session** and returns their output (prints and tracebacks) to the MCP client, e.g. Claude Code.

Inspired by [jianzhichun/abaqus-mcp-server](https://github.com/jianzhichun/abaqus-mcp-server). Instead of GUI automation it uses a small Abaqus plug-in, so it works with Abaqus versions whose GUI is invisible to Windows UI automation (e.g. 6.14) and needs no window focus.

## How it works

`mcp_server.py` writes the script to `~/.abaqus_mcp/job_<id>.py`. The plug-in (`mcp_bridge_plugin.py`) polls that folder from inside Abaqus/CAE and hands each job to the CAE kernel via `sendCommand`; there `mcp_kernel.py` runs it and writes `job_<id>.json` with the output. Code runs in the command-line namespace, so changes show up in the GUI and variables persist between calls.

## Setup

1. Add the server to `.mcp.json`:

```json
{
  "mcpServers": {
    "abaqus": {
      "command": "uv",
      "args": ["--directory", "C:/path/to/abaqus-mcp-server", "run", "mcp_server.py"]
    }
  }
}
```

2. Start Abaqus/CAE with the `launch_abaqus` tool. It copies `abaqus_plugin/*.py` to `~/abaqus_plugins/` first, so the bridge always matches the server. If you start CAE yourself, copy those files there by hand. `~/.abaqus_mcp/bridge.log` should say `bridge started`.

## Tools

- `list_abaqus_installations()` lists installed versions found as `abq<version>.bat` in `?:\SIMULIA\Commands`, `?:\SIMULIA\*\Commands` and next to `abaqus` on PATH.
- `launch_abaqus(version="", work_dir="", wait=180)` installs the plug-in, starts `abq<version>.bat cae` in `work_dir` (default: home folder) and waits until the bridge answers. `version` can be omitted when only one is installed.
- `execute_script(python_code, timeout=600)` returns `[OK]` or `[FAILED]` followed by the script's output. Scripts run on Abaqus's own Python (2.7 in 6.14).
- `describe(path)` is a read-only API lookup in the live session: docstring with the signature, `__members__`, repository keys and methods, e.g. `describe("mdb.models['Model-1'].HeatTransferStep")`. Only attribute and `[key]` paths are accepted, no calls. The installed Abaqus is the reference, which matters because the scripting API differs between versions.

```python
j = mdb.jobs['Thermal2D']
j.submit(); j.waitForCompletion()
print j.status
```

## Tests

```
uv run python -m unittest test_mcp_server
```

Tested with Abaqus/CAE 6.14-5 on Windows 11.
