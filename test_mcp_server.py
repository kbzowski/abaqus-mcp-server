import glob
import os
import runpy
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.append(HERE)
import mcp_server

PLUGIN_DIR = os.path.join(HERE, "abaqus_plugin")
bridge = runpy.run_path(os.path.join(PLUGIN_DIR, "mcp_bridge_plugin.py"))
kernel = runpy.run_path(os.path.join(PLUGIN_DIR, "mcp_kernel.py"))


class TestExecuteScript(unittest.TestCase):
    """mcp_server -> real bridge (poll_once) -> real kernel helper, with Abaqus's sendCommand swapped out."""

    def setUp(self):
        self.spool = tempfile.mkdtemp()
        patcher = patch.object(mcp_server, "SPOOL", self.spool)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.namespace = {}  # stands in for the CAE kernel's __main__

    def run_job(self, run, out):
        kernel["_mcp_run"](run, out, self.namespace)

    def execute(self, code, run_job=None, timeout=5):
        stop = threading.Event()

        def loop():
            while not stop.is_set():
                bridge["poll_once"](self.spool, run_job or self.run_job)
                stop.wait(0.05)

        t = threading.Thread(target=loop)
        t.start()
        try:
            return mcp_server.execute_script(code, timeout=timeout)
        finally:
            stop.set()
            t.join()

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
        # the bridge holds the job for 1 s, longer than the 0.5 s timeout
        res = self.execute("x = 1", run_job=lambda run, out: threading.Event().wait(1), timeout=0.5)
        self.assertIn("still running", res)

    @patch.object(mcp_server, "PICKUP_TIMEOUT", 0.3)
    def test_no_bridge_removes_job(self):
        res = mcp_server.execute_script("x = 1", timeout=5)
        self.assertIn("not running", res)
        self.assertEqual(glob.glob(os.path.join(self.spool, "job_*")), [])


if __name__ == "__main__":
    unittest.main()
