from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from watering_backend.app import ApplicationPaths, create_application


class SMTPWebConfigurationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.temporary_directory.name)
        root = Path(__file__).parent.parent
        self.paths = ApplicationPaths(
            root=root,
            public_dir=root / "public",
            data_dir=self.data_dir,
            database_path=self.data_dir / "watering.sqlite3",
            version_path=root / "VERSION",
            updater_token_file=self.data_dir / ".updater-token",
        )
        self.environment = {
            "NOTIFICATION_WORKER_DISABLED": "true",
        }
        self.application = create_application(
            paths=self.paths,
            environment=self.environment,
        )
        self.application.initialize()

    def tearDown(self) -> None:
        self.application.stop_notification_worker()
        self.temporary_directory.cleanup()

    @staticmethod
    def payload() -> dict:
        return {
            "enabled": True,
            "host": "smtp.private.example",
            "port": 587,
            "username": "private-user",
            "password": "private-password",
            "sender": "planner@private.example",
            "recipients": "owner@private.example",
            "security": "starttls",
        }

    def test_saved_values_are_persistent_but_browser_status_is_write_only(
        self,
    ) -> None:
        status = self.application.save_notification_configuration(
            self.payload()
        )
        self.assertTrue(status["enabled"])
        self.assertTrue(status["configured"])
        self.assertTrue(status["write_only"])
        self.assertTrue(status["web_configured"])
        self.assertNotIn("host", status)
        self.assertNotIn("from", status)
        self.assertNotIn("to", status)
        self.assertNotIn("security", status)

        browser_documents = json.dumps(
            {
                "state": self.application.get_state(),
                "diagnostics": (
                    self.application.notification_diagnostics()
                ),
            }
        )
        for secret in (
            "smtp.private.example",
            "private-user",
            "private-password",
            "planner@private.example",
            "owner@private.example",
        ):
            self.assertNotIn(secret, browser_documents)

        restarted = create_application(
            paths=self.paths,
            environment=self.environment,
        )
        restarted.initialize()
        config = restarted.smtp_configuration.load()
        self.assertEqual(config.host, "smtp.private.example")
        self.assertEqual(config.username, "private-user")
        self.assertEqual(config.password, "private-password")
        self.assertEqual(
            config.recipients,
            ("owner@private.example",),
        )

    def test_blank_fields_preserve_values_and_credentials_can_be_cleared(
        self,
    ) -> None:
        self.application.save_notification_configuration(self.payload())
        self.application.save_notification_configuration(
            {
                "enabled": False,
                "host": "",
                "password": "",
            }
        )
        preserved = self.application.smtp_configuration.load()
        self.assertFalse(preserved.enabled)
        self.assertEqual(preserved.host, "smtp.private.example")
        self.assertEqual(preserved.password, "private-password")

        self.application.save_notification_configuration(
            {
                "enabled": False,
                "clear_credentials": True,
            }
        )
        cleared = self.application.smtp_configuration.load()
        self.assertEqual(cleared.username, "")
        self.assertEqual(cleared.password, "")

    def test_invalid_enabled_configuration_rolls_back_every_value(
        self,
    ) -> None:
        with self.assertRaisesRegex(
            ValueError,
            "SMTP_HOST",
        ):
            self.application.save_notification_configuration(
                {
                    "enabled": True,
                    "host": "smtp.partial.example",
                }
            )
        self.assertEqual(
            self.application.settings.get(
                "secret_smtp_host",
                "missing",
            ),
            "missing",
        )
        self.assertFalse(
            self.application.smtp_configuration.load().enabled
        )

    def test_transport_errors_redact_every_saved_value(self) -> None:
        self.application.save_notification_configuration(self.payload())
        with patch(
            "watering_backend.notifications.send_email",
            side_effect=OSError(
                "smtp.private.example private-user private-password "
                "owner@private.example"
            ),
        ):
            with self.assertRaisesRegex(
                ValueError,
                r"\[geschützt\]",
            ):
                self.application.send_test_notification()
        diagnostics = json.dumps(
            self.application.notification_diagnostics()
        )
        for secret in (
            "smtp.private.example",
            "private-user",
            "private-password",
            "owner@private.example",
        ):
            self.assertNotIn(secret, diagnostics)
