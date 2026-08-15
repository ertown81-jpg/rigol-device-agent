from __future__ import annotations

import json
import threading
import tempfile
import unittest
from http.server import HTTPServer
from urllib.request import Request, urlopen
from pathlib import Path

from rigol_agent.adapter import SimulatedRigolAdapter
from rigol_agent.agent import RigolAgent
from rigol_agent.adaptive import ClosedLoopSignalAgent
from rigol_agent.planner import RuleBasedPlanner
from rigol_agent.policy import ExecutionPolicy
from rigol_agent.service import _handler_factory, serve
from rigol_agent.tools import ToolRegistry


class ServiceTests(unittest.TestCase):
    def test_service_refuses_non_loopback_binding(self) -> None:
        with self.assertRaisesRegex(ValueError, "回环地址"):
            serve(SimulatedRigolAdapter(), host="0.0.0.0")

    def test_health_capabilities_and_plan_endpoints(self) -> None:
        adapter = SimulatedRigolAdapter()
        tools = ToolRegistry(adapter, ExecutionPolicy())
        agent = RigolAgent(RuleBasedPlanner(), tools)
        server = HTTPServer(("127.0.0.1", 0), _handler_factory(agent, tools))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_port}"
        try:
            with urlopen(f"{base}/", timeout=2) as response:
                page = response.read().decode("utf-8")
            with urlopen(f"{base}/health", timeout=2) as response:
                health = json.load(response)
            with urlopen(f"{base}/device", timeout=2) as response:
                device = json.load(response)
            with urlopen(f"{base}/capabilities", timeout=2) as response:
                capabilities = json.load(response)
            with urlopen(f"{base}/device-packs", timeout=2) as response:
                device_packs = json.load(response)
            select_request = Request(
                f"{base}/device-packs/select",
                method="POST",
                data=json.dumps({"pack_id": "rigol_ds1102ze"}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
            )
            with urlopen(select_request, timeout=2) as response:
                selection = json.load(response)
            request = Request(
                f"{base}/plan",
                method="POST",
                data=json.dumps({"request": "读取 CH1 的频率"}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
            )
            with urlopen(request, timeout=2) as response:
                plan = json.load(response)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)
        self.assertEqual(health["status"], "ok")
        self.assertIn("智能设备 Agent", page)
        self.assertIn('id="device-pack-select"', page)
        self.assertEqual(health["device_pack"]["pack_id"], "rigol_ds1102ze")
        self.assertTrue(device["online"])
        self.assertEqual(len(capabilities["tools"]), 11)
        self.assertEqual(capabilities["device_pack"]["device_class"], "oscilloscope")
        self.assertEqual(device_packs["active_pack_id"], "rigol_ds1102ze")
        self.assertEqual(len(device_packs["device_packs"]), 1)
        self.assertFalse(device_packs["switching_supported"])
        self.assertFalse(selection["changed"])
        self.assertEqual(plan["steps"][0]["tool"], "measure")

    def test_evidence_incomplete_task_returns_http_200(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            adapter = SimulatedRigolAdapter("low_resolution", Path(directory) / "samples")
            tools = ToolRegistry(adapter, ExecutionPolicy())
            planner = RuleBasedPlanner()
            agent = RigolAgent(planner, tools, output_dir=Path(directory) / "sessions")
            agent.adaptive_runner = ClosedLoopSignalAgent(
                tools,
                planner,
                output_dir=Path(directory) / "sessions",
                allow_adaptive_changes=True,
            )
            server = HTTPServer(("127.0.0.1", 0), _handler_factory(agent, tools))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            request = Request(
                f"http://127.0.0.1:{server.server_port}/tasks",
                method="POST",
                data=json.dumps({"request": "analyze CH1 signal"}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
            )
            try:
                with urlopen(request, timeout=10) as response:
                    self.assertEqual(response.status, 200)
                    result = json.load(response)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)
        self.assertTrue(result["analysis"]["execution_success"])
        self.assertFalse(result["analysis"]["scientific_success"])


if __name__ == "__main__":
    unittest.main()
