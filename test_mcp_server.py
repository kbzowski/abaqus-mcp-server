import asyncio
import glob
import os
import runpy
import shutil
import sys
import tempfile
import threading
import unittest
from unittest.mock import AsyncMock, patch

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.append(HERE)
import mcp_server

PLUGIN_DIR = os.path.join(HERE, "abaqus_plugin")
bridge = runpy.run_path(os.path.join(PLUGIN_DIR, "mcp_bridge_plugin.py"))
kernel = runpy.run_path(os.path.join(PLUGIN_DIR, "mcp_kernel.py"))


def temp_dir(test):
    path = tempfile.mkdtemp()
    test.addCleanup(shutil.rmtree, path, True)
    return path


class TestExecuteScript(unittest.TestCase):
    def setUp(self):
        self.spool = temp_dir(self)
        patcher = patch.object(mcp_server, "SPOOL", self.spool)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.namespace = {}

    def run_job(self, run, out):
        kernel["_mcp_run"](run, out, self.namespace)

    def bridged(self, call, run_job=None):
        stop = threading.Event()

        def loop():
            while not stop.is_set():
                bridge["poll_once"](self.spool, run_job or self.run_job)
                stop.wait(0.05)

        t = threading.Thread(target=loop)
        t.start()
        try:
            return call()
        finally:
            stop.set()
            t.join()

    def execute(self, code, run_job=None, timeout=5.0):
        return self.bridged(lambda: asyncio.run(mcp_server.execute_script(code, timeout=timeout)), run_job)

    def describe(self, path):
        return self.bridged(lambda: asyncio.run(mcp_server.describe(path)))

    def test_output_and_cleanup(self):
        res = self.execute("print('zażółć', 6 * 7)")
        self.assertEqual(res, "[OK]\nzażółć 42")
        self.assertEqual(glob.glob(os.path.join(self.spool, "job_*")), [])

    def test_variables_persist_between_jobs(self):
        self.execute("x = 41")
        self.assertEqual(self.execute("print(x + 1)"), "[OK]\n42")

    def test_traceback_points_at_script_line(self):
        res = self.execute("a = 1\n1 / 0\n")
        self.assertTrue(res.startswith("[FAILED]"))
        self.assertIn("line 2", res)
        self.assertIn("ZeroDivisionError", res)

    def test_bridge_error_is_reported(self):
        def broken(run, out):
            raise RuntimeError("kernel unreachable")
        res = self.execute("x = 1", run_job=broken)
        self.assertTrue(res.startswith("[FAILED]\nBridge error:"))
        self.assertIn("kernel unreachable", res)

    def test_timeout_while_running(self):
        res = self.execute("x = 1", run_job=lambda run, out: threading.Event().wait(1), timeout=0.5)
        self.assertIn("still running", res)

    def test_describe_lists_doc_members_keys_and_methods(self):
        class Step:
            """Model.Step(name, previous) -> creates a Step."""
            __members__ = ["name", "previous"]
            name, previous = "Heat", "Initial"
            def keys(self):
                return ["a"]
            def setValues(self):
                pass
        self.namespace["mdb"] = {"Heat": Step()}
        res = self.describe("mdb['Heat']")
        self.assertTrue(res.startswith("[OK]\nTYPE: Step"))
        self.assertIn("DOC: Model.Step(name, previous)", res)
        self.assertIn("MEMBERS: name, previous", res)
        self.assertIn("KEYS: a", res)
        self.assertIn("setValues", res)
        self.assertNotIn("_mcp_describe", self.namespace)

    def test_describe_rejects_calls(self):
        for path in ["mdb.Model(name='x')", "__import__('os').remove('f')", "a; b", "a[b]"]:
            self.assertTrue(asyncio.run(mcp_server.describe(path)).startswith("Invalid path"), path)

    @patch.object(mcp_server, "PICKUP_TIMEOUT_S", 0.3)
    def test_no_bridge_removes_job(self):
        res = asyncio.run(mcp_server.execute_script("x = 1", timeout=5))
        self.assertIn("not running", res)
        self.assertEqual(glob.glob(os.path.join(self.spool, "job_*")), [])

    @patch.object(mcp_server, "PICKUP_TIMEOUT_S", 0.3)
    def test_running_job_means_busy_not_dead(self):
        open(os.path.join(self.spool, "job_long.run"), "w").close()
        res = asyncio.run(mcp_server.execute_script("x = 1", timeout=5))
        self.assertIn("busy with", res)
        self.assertEqual(asyncio.run(mcp_server.launch_abaqus()), "Abaqus/CAE with the bridge is already running.")


class TestInstallations(unittest.TestCase):
    def setUp(self):
        root = temp_dir(self)
        for rel in ["SIMULIA/Abaqus/Commands/abq6145.bat", "SIMULIA/Abaqus/Commands/abq_cae_open.bat",
                    "SIMULIA/Commands/abq2023.bat", "SIMULIA/Commands/abaqus.bat"]:
            os.makedirs(os.path.dirname(os.path.join(root, rel)), exist_ok=True)
            open(os.path.join(root, rel), "w").close()
        self.root = root
        for target, value in [("COMMANDS_GLOBS", [f"{root}/SIMULIA/Commands", f"{root}/SIMULIA/*/Commands"]),
                              ("SPOOL", temp_dir(self)), ("PLUGIN_TARGET", os.path.join(root, "abaqus_plugins"))]:
            patcher = patch.object(mcp_server, target, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        which = patch("shutil.which", return_value=None)
        which.start()
        self.addCleanup(which.stop)

    def test_finds_versions_only(self):
        self.assertEqual(sorted(mcp_server.find_installations()), ["2023", "6145"])

    def test_finds_linux_launchers_without_extension(self):
        for name in ["abq2024", "abq2024.log"]:
            open(os.path.join(self.root, "SIMULIA/Commands", name), "w").close()
        self.assertEqual(sorted(mcp_server.find_installations()), ["2023", "2024", "6145"])

    @patch("subprocess.Popen")
    def test_launch_needs_version_when_several(self, popen):
        self.assertTrue(asyncio.run(mcp_server.launch_abaqus()).startswith("Choose a version:"))
        popen.assert_not_called()

    @patch("asyncio.sleep", new_callable=AsyncMock)
    @patch("subprocess.Popen")
    def test_launch_installs_plugin_runs_cae_and_waits_for_bridge(self, popen, _sleep):
        popen.return_value.poll.return_value = None
        with patch.object(mcp_server, "_bridge_alive", side_effect=[False, False, True]):
            res = asyncio.run(mcp_server.launch_abaqus("2023", work_dir=self.root))
        self.assertIn("bridge ready", res)
        args, kwargs = popen.call_args
        self.assertEqual(args[0], [os.path.normpath(f"{self.root}/SIMULIA/Commands/abq2023.bat"), "cae"])
        self.assertEqual(kwargs["cwd"], self.root)
        self.assertEqual(sorted(os.listdir(mcp_server.PLUGIN_TARGET)), ["mcp_bridge_plugin.py", "mcp_kernel.py", "mcp_nogui.py"])

    @patch("asyncio.sleep", new_callable=AsyncMock)
    @patch("subprocess.Popen")
    def test_launch_headless_runs_nogui_bridge(self, popen, _sleep):
        popen.return_value.poll.return_value = None
        with patch.object(mcp_server, "_bridge_alive", side_effect=[False, True]):
            asyncio.run(mcp_server.launch_abaqus("2023", headless=True))
        self.assertEqual(popen.call_args[0][0][1:],
                         ["cae", "noGUI=" + os.path.join(mcp_server.PLUGIN_TARGET, "mcp_nogui.py")])


class TestHttpAuth(unittest.TestCase):
    TOKEN = "s3cret-token-0123456789"

    def client(self):
        from starlette.applications import Starlette
        from starlette.responses import PlainTextResponse
        from starlette.routing import Route
        from starlette.testclient import TestClient
        app = Starlette(routes=[Route("/mcp", lambda r: PlainTextResponse("ok"), methods=["POST"])])
        return TestClient(mcp_server.require_token(app, self.TOKEN))

    def test_rejects_missing_and_wrong_token(self):
        c = self.client()
        self.assertEqual(c.post("/mcp").status_code, 401)
        self.assertEqual(c.post("/mcp", headers={"Authorization": "Bearer wrong"}).status_code, 401)

    def test_accepts_right_token(self):
        res = self.client().post("/mcp", headers={"Authorization": f"Bearer {self.TOKEN}"})
        self.assertEqual((res.status_code, res.text), (200, "ok"))

    @patch("uvicorn.run")
    def test_public_host_needs_token(self, run):
        with patch.dict(os.environ, {"ABAQUS_MCP_TOKEN": ""}), self.assertRaises(SystemExit):
            mcp_server.main(["--http", "--host", "0.0.0.0"])
        with patch.dict(os.environ, {"ABAQUS_MCP_TOKEN": "short"}), self.assertRaises(SystemExit):
            mcp_server.main(["--http"])
        run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
