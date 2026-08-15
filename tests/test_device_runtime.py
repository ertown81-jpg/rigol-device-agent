from __future__ import annotations

from dataclasses import replace
import unittest

from rigol_agent.device_packs.template import EXAMPLE_PACK
from rigol_agent.runtime import AgentStack, DeviceRuntime


class FakeAdapter:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


def stack_for(pack):
    return AgentStack(pack=pack, adapter=FakeAdapter(), tools=object(), agent=object())


class DeviceRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.first = EXAMPLE_PACK
        self.second = replace(EXAMPLE_PACK, pack_id="second_device", display_name="第二台测试设备")
        self.packs = {pack.pack_id: pack for pack in (self.first, self.second)}

    def runtime(self, first_stack, factory):
        return DeviceRuntime(
            first_stack,
            stack_factory=factory,
            pack_getter=self.packs.__getitem__,
            pack_lister=lambda: list(self.packs.values()),
        )

    def test_successful_switch_replaces_stack_and_closes_previous_adapter(self) -> None:
        original = stack_for(self.first)
        replacement = stack_for(self.second)
        runtime = self.runtime(original, lambda pack: replacement)

        result = runtime.select(self.second.pack_id)

        self.assertTrue(result["changed"])
        self.assertEqual(runtime.snapshot().pack.pack_id, self.second.pack_id)
        self.assertTrue(original.adapter.closed)
        self.assertFalse(replacement.adapter.closed)

    def test_failed_replacement_build_keeps_current_device_open(self) -> None:
        original = stack_for(self.first)

        def fail(_pack):
            raise RuntimeError("connection failed")

        runtime = self.runtime(original, fail)
        with self.assertRaisesRegex(RuntimeError, "connection failed"):
            runtime.select(self.second.pack_id)

        self.assertIs(runtime.snapshot(), original)
        self.assertFalse(original.adapter.closed)

    def test_selecting_active_pack_is_a_no_op(self) -> None:
        original = stack_for(self.first)
        runtime = self.runtime(original, lambda pack: stack_for(pack))

        result = runtime.select(self.first.pack_id)

        self.assertFalse(result["changed"])
        self.assertIs(runtime.snapshot(), original)
        self.assertFalse(original.adapter.closed)


if __name__ == "__main__":
    unittest.main()
