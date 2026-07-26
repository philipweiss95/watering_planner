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
            start,
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
        self.assertLess(start, running)
        self.assertLess(running, switch_on)
        self.assertLess(running, switch_off)
        self.assertLess(switch_off, complete)
        self.assertIn(
            'duration: "{{ claimed_seconds | int(0) + 15 }}"',
            configuration,
        )
        self.assertIn(
            "claimed_status == 'running'",
            configuration,
        )
        self.assertIn("pump_start_authorized", configuration)
        self.assertIn("and claimed_seconds > 0", configuration)
        authorization_check = configuration.index(
            "and pump_start_authorized"
        )
        self.assertLess(authorization_check, switch_on)
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

    def test_missing_on_confirmation_keeps_guard_until_off_is_confirmed(
        self,
    ) -> None:
        configuration = (
            ROOT / "home-assistant" / "configuration.yaml"
        ).read_text(encoding="utf-8")
        failure_start = configuration.index(
            "{{ not is_state('switch.smart_plug_mini_refill', 'on') }}"
        )
        failure_end = configuration.index(
            "- alias: Persistierte Nachfuelldauer abwarten",
            failure_start,
        )
        failure_path = configuration[failure_start:failure_end]

        switch_off = failure_path.index("action: switch.turn_off")
        off_wait = failure_path.index("wait_template", switch_off)
        uncertain_report = failure_path.index(
            "may_have_transferred: true",
            off_wait,
        )
        confirmed_off = failure_path.index(
            "{{ is_state('switch.smart_plug_mini_refill', 'off') }}",
            uncertain_report,
        )
        guard_cancel = failure_path.index(
            "action: timer.cancel",
            confirmed_off,
        )
        run_id_clear = failure_path.index(
            "action: input_text.set_value",
            guard_cancel,
        )

        self.assertLess(switch_off, off_wait)
        self.assertLess(off_wait, uncertain_report)
        self.assertLess(uncertain_report, confirmed_off)
        self.assertLess(confirmed_off, guard_cancel)
        self.assertLess(guard_cancel, run_id_clear)
        self.assertNotIn("may_have_transferred: false", failure_path)
        self.assertIn(
            "Sicherheitsabschaltung bleibt aktiv",
            failure_path,
        )

        automations = (
            ROOT / "home-assistant" / "automations.yaml"
        ).read_text(encoding="utf-8")
        guard_start = automations.index(
            "id: bewaesserung_nachfuellpumpe_sicherheitsabschaltung"
        )
        guard_end = automations.index(
            "- id: bewaesserung_tank_warnung",
            guard_start,
        )
        guard_path = automations[guard_start:guard_end]
        self.assertIn("wait_template", guard_path)
        self.assertIn(
            "{{ is_state('switch.smart_plug_mini_refill', 'off') }}",
            guard_path,
        )
        self.assertIn('duration: "00:00:15"', guard_path)


if __name__ == "__main__":
    unittest.main()
