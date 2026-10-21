# Abaqus MCP Server

MCP server that runs Python scripts inside a live Abaqus/CAE session and returns their output (prints and tracebacks) to the MCP client, e.g. Claude Code. It works with the GUI on Windows, headless on Windows and Linux, and over the network.

Inspired by [jianzhichun/abaqus-mcp-server](https://github.com/jianzhichun/abaqus-mcp-server). Instead of GUI automation it uses a small Abaqus plug-in, so it works with Abaqus versions whose GUI is invisible to Windows UI automation (e.g. 6.14) and needs no window focus.

## How it works

`mcp_server.py` writes the script to `~/.abaqus_mcp/job_<id>.py`. The plug-in (`mcp_bridge_plugin.py`) polls that folder from inside Abaqus/CAE and hands each job to the CAE kernel via `sendCommand`; there `mcp_kernel.py` runs it and writes `job_<id>.json` with the output. Code runs in the command-line namespace, so changes show up in the GUI and variables persist between calls. Without a GUI, `mcp_nogui.py` polls the same folder in a plain loop inside `abaqus cae noGUI` (see [Headless systems](#headless-systems)).

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

## Headless systems

On a server or any machine without a desktop session (Windows or Linux), run the kernel without the GUI. The MCP server and Abaqus must run as the same user, because both use `~/.abaqus_mcp`.

1. Install [uv](https://docs.astral.sh/uv/) and clone this repository on the Abaqus machine, then register the server in the client as in [Setup](#setup).
2. Run `list_abaqus_installations()` to check that the server finds Abaqus. If it does not, put the installation's `Commands` directory on PATH.
3. Start the bridge with `launch_abaqus(headless=True)`, or by hand:

Windows:

```
copy abaqus_plugin\*.py %USERPROFILE%\abaqus_plugins\
cd C:\path\to\work_dir
abq2023 cae noGUI=%USERPROFILE%\abaqus_plugins\mcp_nogui.py
```

Linux:

```
mkdir -p ~/abaqus_plugins && cp abaqus_plugin/*.py ~/abaqus_plugins/
cd /path/to/work_dir
nohup abq2023 cae noGUI=$HOME/abaqus_plugins/mcp_nogui.py > /dev/null 2>&1 &
```

4. `~/.abaqus_mcp/bridge.log` should say `headless bridge started`.

Differences from the GUI mode:

- `mdb`, `session`, `abaqusConstants` and `caeModules` are preloaded, as on the CAE command line, and variables persist between calls. Models, jobs and ODB access work; anything that needs a window does not.
- The bridge never exits by itself. Stop it by ending the Abaqus process (`taskkill` on Windows, `pkill -f mcp_nogui` on Linux). A script that calls `sys.exit()` only fails its own job.
- noGUI still checks out a CAE license.
- On Linux the GUI mode needs a display (`DISPLAY` or X forwarding); headless mode does not.

## Network access

By default the client starts the server itself and talks to it over stdio, so nothing listens on the network. To reach Abaqus from another machine, run the server on the Abaqus machine over HTTP:

```
uv run mcp_server.py --http                    # 127.0.0.1:8000, reach it through an SSH tunnel
uv run mcp_server.py --http --host 0.0.0.0 --port 8000
```

`execute_script` runs any code it receives, so access is restricted:

- The default `127.0.0.1` accepts local connections only. Forward it from your machine with `ssh -L 8000:127.0.0.1:8000 user@abaqus-host` and connect to `http://127.0.0.1:8000/mcp`. This is the recommended setup.
- Any other `--host` refuses to start unless `ABAQUS_MCP_TOKEN` is set (at least 16 characters, e.g. `python -c "import secrets; print(secrets.token_urlsafe(32))"`). Every request must then send `Authorization: Bearer <token>`. Once the variable is set, the token is required on `127.0.0.1` too.
- Plain HTTP sends the token unencrypted. Outside a trusted network use the SSH tunnel, a VPN or a TLS reverse proxy.

Client side, in `.mcp.json`:

```json
{
  "mcpServers": {
    "abaqus": {
      "type": "http",
      "url": "http://abaqus-host:8000/mcp",
      "headers": { "Authorization": "Bearer ${ABAQUS_MCP_TOKEN}" }
    }
  }
}
```

`launch_abaqus` starts Abaqus on the machine where the server runs, so the server, Abaqus and `~/.abaqus_mcp` stay on one machine and only MCP traffic crosses the network.

## Tools

- `list_abaqus_installations()` lists installed versions found as `abq<version>.bat` in `?:\SIMULIA\Commands` and `?:\SIMULIA\*\Commands` (Windows), as `abq<version>` in `/var/DassaultSystemes/SIMULIA/Commands`, `/usr/SIMULIA/Commands` and `/opt/SIMULIA/Commands` (Linux), and next to `abaqus` on PATH.
- `launch_abaqus(version="", work_dir="", wait=180, headless=False)` installs the plug-in, starts `abq<version> cae` in `work_dir` (default: home folder) and waits until the bridge answers. `version` can be omitted when only one is installed. `headless=True` starts `cae noGUI=mcp_nogui.py` instead, without a window.
- `execute_script(python_code, timeout=600)` returns `[OK]` or `[FAILED]` followed by the script's output. Scripts run on Abaqus's own Python (2.7 in 6.14).
- `describe(path)` is a read-only API lookup in the live session: docstring with the signature, `__members__`, repository keys and methods, e.g. `describe("mdb.models['Model-1'].HeatTransferStep")`. Only attribute and `[key]` paths are accepted, no calls. The answer comes from the installed Abaqus, which matters because the scripting API differs between versions.

```python
j = mdb.jobs['Thermal2D']
j.submit(); j.waitForCompletion()
print j.status
```

## Tests

```
uv run python -m unittest test_mcp_server
```

The GUI mode is tested with Abaqus/CAE 6.14-5 on Windows 11. Headless mode and Linux support have not been run against a real Abaqus yet; the HTTP transport has only been tested locally.
