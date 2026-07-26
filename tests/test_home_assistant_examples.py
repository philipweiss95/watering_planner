from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).parent.parent


class HomeAssistantExampleTests(unittest.TestCase):
    def test_refill_script_uses_two_phase_api_and_safety_guard(self) -> None:
        configuration = (
            ROOT / "home-assistant" / "configuration.yaml"
        ).read_text(encoding="utf-8")
        automations = (
            ROOT / "home-assistant" / "automations.yaml"
        ).read_text(encoding="utf-8")

        start = configuration.index(
            "action: rest_command.bewaesserung_refill_start"
        )
        switch_on = configuration.index(
            "action: switch.turn_on",
            start,
        )
        running = configuration.index(
            "action: rest_command.bewaesserung_refill_running",
            switch_on,
        )
        switch_off = configuration.index(
            "action: switch.turn_off",
            switch_on,
        )
        complete = configuration.index(
            "action: rest_command.bewaesserung_refill_complete",
            switch_off,
        )

        self.assertLess(start, switch_on)
        self.assertLess(switch_on, running)
        self.assertLess(running, switch_off)
        self.assertLess(switch_off, complete)
        self.assertIn(
            'duration: "{{ reserved_seconds | int(0) + 15 }}"',
            configuration,
        )
        self.assertIn(
            "timer.watering_refill_pump_guard",
            configuration,
        )
        self.assertIn(
            "bewaesserung_nachfuellpumpe_sicherheitsabschaltung",
            automations,
        )
        self.assertGreaterEqual(
            automations.count(
                "entity_id: switch.smart_plug_mini_refill"
            ),
            2,
        )
        self.assertNotIn(
            'url: "http://192.168.178.116:8080/api/refill/mark-run"',
            configuration,
        )

    def test_manual_and_automatic_triggers_share_single_refill_script(
        self,
    ) -> None:
        automations = (
            ROOT / "home-assistant" / "automations.yaml"
        ).read_text(encoding="utf-8")
        self.assertEqual(
            automations.count(
                "action: script.bewaesserung_nachfuellen"
            ),
            2,
        )
        self.assertIn("run_type: manual", automations)
        self.assertIn("run_type: automatic", automations)
        self.assertIn("mode: single", automations)


if __name__ == "__main__":
    unittest.main()
