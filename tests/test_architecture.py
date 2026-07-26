from __future__ import annotations

import ast
import unittest
from pathlib import Path

from watering_backend.api import build_router


ROOT = Path(__file__).parent.parent
BACKEND = ROOT / "watering_backend"


class ArchitectureTests(unittest.TestCase):
    def test_core_is_a_small_compatibility_facade(self) -> None:
        core = BACKEND / "core.py"
        source = core.read_text(encoding="utf-8")
        self.assertLess(len(source.splitlines()), 1000)
        self.assertNotIn("CREATE TABLE", source)
        self.assertNotIn("SELECT * FROM", source)

    def test_services_and_api_do_not_import_core_or_server(self) -> None:
        for directory in (BACKEND / "services", BACKEND / "api"):
            for path in directory.glob("*.py"):
                tree = ast.parse(path.read_text(encoding="utf-8"))
                imports = {
                    node.module
                    for node in ast.walk(tree)
                    if isinstance(node, ast.ImportFrom) and node.module
                }
                imports.update(
                    alias.name
                    for node in ast.walk(tree)
                    if isinstance(node, ast.Import)
                    for alias in node.names
                )
                self.assertNotIn("server", imports, path.name)
                self.assertNotIn("watering_backend.core", imports, path.name)

    def test_all_http_endpoints_are_registered(self) -> None:
        router = build_router()
        self.assertEqual(len(router.routes), 38)
        self.assertEqual(
            len({(route.method, route.template) for route in router.routes}),
            len(router.routes),
        )
        registered = {
            (route.method, route.template)
            for route in router.routes
        }
        self.assertTrue(
            {
                ("POST", "/api/refill/start"),
                ("POST", "/api/refill/running"),
                ("POST", "/api/refill/complete"),
                ("POST", "/api/refill/fail"),
                ("GET", "/api/refill/runs/{run_id}"),
            }
            <= registered
        )


if __name__ == "__main__":
    unittest.main()
