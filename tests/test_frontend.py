from pathlib import Path
from html.parser import HTMLParser
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
PUBLIC = ROOT / "public"


class StructureParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids = []
        self.form_depth = 0
        self.nested_forms = 0

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if attributes.get("id"):
            self.ids.append(attributes["id"])
        if tag == "form":
            if self.form_depth:
                self.nested_forms += 1
            self.form_depth += 1

    def handle_endtag(self, tag):
        if tag == "form":
            self.form_depth = max(0, self.form_depth - 1)


class FrontendStructureTests(unittest.TestCase):
    def read(self, path: str) -> str:
        return (PUBLIC / path).read_text(encoding="utf-8")

    def test_entrypoint_is_modular_and_all_imports_exist(self):
        entrypoint = self.read("app.js")
        imports = re.findall(r'from\s+"(\./[^"]+)"', entrypoint)
        self.assertGreaterEqual(len(imports), 8)
        for imported in imports:
            self.assertTrue((PUBLIC / imported.removeprefix("./")).is_file(), imported)
        self.assertLess(len(entrypoint.splitlines()), 300)

    def test_service_worker_precaches_every_local_module_and_stylesheet(self):
        service_worker = self.read("sw.js")
        for path in [*sorted((PUBLIC / "js").glob("*.js")), *sorted((PUBLIC / "css").glob("*.css"))]:
            relative = "/" + path.relative_to(PUBLIC).as_posix()
            self.assertIn(f'"{relative}', service_worker)

    def test_dynamic_ui_avoids_unsafe_html_and_native_dialogs(self):
        scripts = "\n".join(path.read_text(encoding="utf-8") for path in (PUBLIC / "js").glob("*.js"))
        scripts += self.read("app.js")
        self.assertNotRegex(scripts, r"\.innerHTML\s*=")
        self.assertNotIn("window.alert(", scripts)
        self.assertNotIn("window.confirm(", scripts)
        self.assertIn("textContent", scripts)
        self.assertIn("confirmDialog", scripts)
        self.assertIn("Rückgängig", scripts)

    def test_required_views_and_actions_are_present(self):
        index = self.read("index.html")
        for view in ["dashboard", "forecast", "plants", "hoses", "history", "settings", "system", "info"]:
            self.assertIn(f'data-view="{view}"', index)
        for node_id in ["nextActionCard", "dayTimeline", "forecastChart", "plantFilters", "hoseTable", "appDialog", "toastRegion"]:
            self.assertIn(f'id="{node_id}"', index)
        self.assertIn("/api/diagnostics/home-assistant/test", self.read("app.js"))
        self.assertIn("/api/notifications/test", self.read("app.js"))

    def test_html_has_unique_ids_and_no_nested_forms(self):
        parser = StructureParser()
        parser.feed(self.read("index.html"))
        duplicates = {node_id for node_id in parser.ids if parser.ids.count(node_id) > 1}
        self.assertEqual(duplicates, set())
        self.assertEqual(parser.nested_forms, 0)

    def test_mobile_css_has_safe_areas_focus_and_stable_chart(self):
        css = "\n".join(path.read_text(encoding="utf-8") for path in (PUBLIC / "css").glob("*.css"))
        self.assertIn("@media (max-width: 719px)", css)
        self.assertIn("@media (max-width: 540px)", css)
        self.assertIn("safe-area-inset-bottom", css)
        self.assertIn(":focus-visible", css)
        self.assertIn("min-height: 44px", css)
        self.assertRegex(css, r"\.forecast-chart\s*\{[^}]*aspect-ratio", re.DOTALL)
        self.assertIn("prefers-reduced-motion", css)

    def test_browser_assets_do_not_reference_external_cdns_or_secrets(self):
        sources = "\n".join(path.read_text(encoding="utf-8") for path in PUBLIC.rglob("*") if path.is_file() and path.suffix in {".html", ".js", ".css"})
        self.assertNotRegex(sources, r"https?://(?:cdn|unpkg|jsdelivr)")
        self.assertNotIn("SMTP_PASSWORD", sources)
        self.assertNotIn("HOME_ASSISTANT_WEBHOOK_URL", sources)

    def test_mobile_forms_are_bounded_and_smtp_is_write_only(self):
        components = self.read("css/components.css")
        responsive = self.read("css/responsive.css")
        index = self.read("index.html")
        settings = self.read("js/settings.js")
        self.assertIn("min-width: 0;", components)
        self.assertIn(".field-grid > *", responsive)
        self.assertIn(
            'name="smtp_password" type="password"',
            index,
        )
        self.assertNotIn('value="smtp.', index)
        self.assertIn("/api/notifications/config", settings)
        self.assertIn("Werte werden nicht angezeigt", settings)


if __name__ == "__main__":
    unittest.main()
