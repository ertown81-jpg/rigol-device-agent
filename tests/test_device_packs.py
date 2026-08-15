from __future__ import annotations

import contextlib
import io
import json
import unittest

from rigol_agent.adapter import SimulatedRigolAdapter
from rigol_agent.cli import main
from rigol_agent.device_packs import DevicePackRegistry, get_device_pack, list_device_packs, validate_standard_result
from rigol_agent.device_packs.template import EXAMPLE_PACK
from rigol_agent.models import RiskLevel, ToolSpec
from rigol_agent.policy import ExecutionPolicy
from rigol_agent.tools import ToolRegistry


class DevicePackTests(unittest.TestCase):
    def test_builtin_pack_is_valid_and_explicitly_registered(self) -> None:
        pack = get_device_pack("rigol_ds1102ze")
        pack.validate()
        self.assertEqual([item.pack_id for item in list_device_packs()], ["rigol_ds1102ze"])
        self.assertEqual(pack.metadata()["adaptive"]["kind"], "oscilloscope_signal")
        self.assertIn("get_device_status", {spec.name for spec in pack.tool_specs})

    def test_template_is_not_implicitly_trusted(self) -> None:
        self.assertNotIn(EXAMPLE_PACK.pack_id, {item.pack_id for item in list_device_packs()})

    def test_registry_rejects_duplicate_pack(self) -> None:
        registry = DevicePackRegistry()
        registry.register(EXAMPLE_PACK)
        with self.assertRaisesRegex(ValueError, "已注册"):
            registry.register(EXAMPLE_PACK)

    def test_tool_registry_uses_pack_specific_specs_and_validator(self) -> None:
        calls: list[tuple[str, dict]] = []

        def validator(tool: str, arguments: dict) -> None:
            calls.append((tool, arguments))

        status_only = (
            ToolSpec(
                "get_device_status",
                "status",
                RiskLevel.READ_ONLY,
                {"type": "object", "properties": {}, "required": [], "additionalProperties": False},
            ),
        )
        registry = ToolRegistry(
            SimulatedRigolAdapter(),
            ExecutionPolicy(device_label="测试设备", argument_validator=validator),
            status_only,
        )
        self.assertEqual([item["name"] for item in registry.capabilities()], ["get_device_status"])
        registry.execute("get_device_status", {})
        self.assertEqual(calls, [("get_device_status", {})])
        with self.assertRaises(KeyError):
            registry.spec("measure")

    def test_cli_lists_device_packs_without_connecting_hardware(self) -> None:
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            exit_code = main(["devices"])
        payload = json.loads(output.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["device_packs"][0]["pack_id"], "rigol_ds1102ze")

    def test_standard_status_contract_rejects_incompatible_adapter_output(self) -> None:
        with self.assertRaisesRegex(ValueError, "identity"):
            validate_standard_result("get_device_status", {"online": True, "status": {}, "errors": []})


if __name__ == "__main__":
    unittest.main()
