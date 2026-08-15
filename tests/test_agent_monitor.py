from __future__ import annotations

import unittest

from rigol_agent.monitor import _event_type


class MonitorTests(unittest.TestCase):
    def test_online_offline_and_change_events(self) -> None:
        online = {"online": True, "channels": {"CH1": {"enabled": True}}}
        changed = {"online": True, "channels": {"CH1": {"enabled": False}}}
        offline = {"online": False, "error": "disconnected"}
        self.assertEqual(_event_type(None, online), "online")
        self.assertEqual(_event_type(online, online), "unchanged")
        self.assertEqual(_event_type(online, changed), "changed")
        self.assertEqual(_event_type(changed, offline), "offline")
        self.assertEqual(_event_type(offline, online), "online")


if __name__ == "__main__":
    unittest.main()
