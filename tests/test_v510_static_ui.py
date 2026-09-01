"""World Engine 5.1.0 — static companion UI gates.

The companion renders inside WebView2 with ``connect-src 'none'``. These tests
guard the properties that make that boundary real: no executable DOM sinks, no
browser networking, no remote assets, no credential surfaces, and a renderer
that refuses a projection schema it does not understand.

They read the shipped assets as text on purpose. A rule that is only honoured
by convention is not a boundary; this is what makes a regression fail loudly.
"""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from world_engine.desktop import SUPPORTED_PROJECTION_VERSIONS  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
UI = ROOT / "companion_ui"
HTML = (UI / "index.html").read_text(encoding="utf-8")
CSS = (UI / "app.css").read_text(encoding="utf-8")
JS = (UI / "app.js").read_text(encoding="utf-8")


class ExecutableSinkTests(unittest.TestCase):
    def test_no_html_injection_sinks(self) -> None:
        for sink in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "srcdoc"):
            self.assertNotIn(sink, JS, f"{sink} must not appear in the companion renderer")

    def test_no_dynamic_code_evaluation(self) -> None:
        self.assertNotIn("eval(", JS)
        self.assertFalse(re.search(r"\bnew\s+Function\s*\(", JS))

    def test_no_string_valued_timers(self) -> None:
        """A string first argument to a timer is an eval in disguise."""
        self.assertFalse(re.search(r"set(?:Timeout|Interval)\s*\(\s*[\"']", JS))

    def test_projected_text_goes_through_textcontent(self) -> None:
        self.assertIn("textContent", JS)


class NetworkBoundaryTests(unittest.TestCase):
    def test_no_browser_networking(self) -> None:
        for api in ("fetch(", "XMLHttpRequest", "WebSocket", "EventSource", "navigator.sendBeacon"):
            self.assertNotIn(api, JS, f"{api} would breach connect-src 'none'")

    def test_only_svg_namespace_urls_appear(self) -> None:
        """Any absolute URL left in the renderer must be a namespace, not a host."""
        for match in re.findall(r"https?://[^\"')\s]+", JS):
            self.assertIn("www.w3.org", match, f"unexpected remote URL in renderer: {match}")

    def test_html_loads_no_remote_assets(self) -> None:
        for match in re.findall(r"(?:src|href)\s*=\s*[\"']([^\"']+)[\"']", HTML):
            self.assertFalse(
                match.startswith("http://") or match.startswith("https://") or match.startswith("//"),
                f"remote asset referenced from the companion shell: {match}",
            )

    def test_css_loads_no_remote_assets(self) -> None:
        for match in re.findall(r"url\(([^)]+)\)", CSS):
            cleaned = match.strip("'\" ")
            self.assertFalse(
                cleaned.startswith("http://") or cleaned.startswith("https://") or cleaned.startswith("//"),
                f"remote asset referenced from the companion stylesheet: {cleaned}",
            )

    def test_no_browser_connection_flow(self) -> None:
        """No INPUT may collect an API key or a server address.

        Prose may legitimately mention the external GPT Builder's bearer-key
        import, which the user performs elsewhere; what must not exist is a
        control in this window that asks for one. The ngrok authtoken field is
        an existing, explicitly retained companion feature and is allowed.
        """
        for control in re.findall(r"<input[^>]*>", HTML, re.IGNORECASE):
            lowered = control.lower()
            if 'id="ngrok-token"' in lowered:
                continue
            for banned in ("api_key", "api-key", "apikey", "bearer", "base_url",
                           "base-url", "baseurl", "server_url", "endpoint_url"):
                self.assertNotIn(banned, lowered, f"connection-credential input present: {control}")

    def test_no_connection_dialog_asks_for_a_server_address(self) -> None:
        for control in re.findall(r"<input[^>]*>", HTML, re.IGNORECASE):
            self.assertNotIn('type="url"', control.lower(), f"server-address input present: {control}")

    def test_no_web_storage_of_credentials(self) -> None:
        for store in ("localStorage", "sessionStorage", "document.cookie", "indexedDB"):
            self.assertNotIn(store, JS, f"{store} must not hold companion state")


class ProjectionGuardTests(unittest.TestCase):
    def test_renderer_pins_the_supported_schema(self) -> None:
        for version in SUPPORTED_PROJECTION_VERSIONS:
            self.assertIn(version, JS, "renderer must declare the schema it accepts")

    def test_renderer_refuses_unknown_schema(self) -> None:
        self.assertIn("SUPPORTED_SCHEMAS", JS)
        self.assertIn("indexOf(schema)", JS)

    def test_renderer_guards_stale_snapshots(self) -> None:
        for marker in ("requestGeneration", "appliedGeneration", "appliedSequence", "projection_sequence"):
            self.assertIn(marker, JS, f"ordering guard missing: {marker}")

    def test_renderer_prevents_overlapping_polls(self) -> None:
        self.assertIn("refreshInFlight", JS)
        self.assertIn("refreshQueued", JS)

    def test_scene_art_is_local_and_deterministic(self) -> None:
        self.assertIn("mulberry32", JS, "procedural art must use the seeded generator")
        self.assertIn("terrain_seed", JS, "scene art must derive from the projected seed")
        self.assertNotIn("image_ref", JS, "the companion must not dereference stored image references")
        self.assertNotIn("new Image(", JS, "the companion must not load external images")


class AccessibilityTests(unittest.TestCase):
    def test_icon_only_controls_have_accessible_names(self) -> None:
        for match in re.findall(r"<button\b[^>]*>", HTML):
            if 'class="icon-button' in match or 'class="rail-toggle' in match:
                self.assertIn("aria-label", match, f"icon-only control without a name: {match}")

    def test_reduced_motion_is_honoured(self) -> None:
        self.assertIn("prefers-reduced-motion", CSS)

    def test_small_label_colour_meets_contrast(self) -> None:
        """--muted carries every small label; it must clear WCAG AA on --bg.

        Computed rather than asserted from a comment: a recorded ratio is the
        author's intention, not a measurement.
        """
        def channel(value: int) -> float:
            srgb = value / 255
            return srgb / 12.92 if srgb <= 0.03928 else ((srgb + 0.055) / 1.055) ** 2.4

        def luminance(hex_colour: str) -> float:
            hex_colour = hex_colour.lstrip("#")
            r, g, b = (int(hex_colour[i:i + 2], 16) for i in (0, 2, 4))
            return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)

        # Take the LAST definition of each token: the 5.1.0 layer is appended.
        muted = re.findall(r"--muted:\s*(#[0-9a-fA-F]{6})", CSS)[-1]
        background = re.findall(r"--bg:\s*(#[0-9a-fA-F]{6})", CSS)[-1]
        light, dark = sorted((luminance(muted), luminance(background)), reverse=True)
        ratio = (light + 0.05) / (dark + 0.05)
        self.assertGreaterEqual(round(ratio, 2), 4.5, f"--muted on --bg is {ratio:.2f}:1, below WCAG AA")

    def test_stage_is_reachable_and_labelled(self) -> None:
        self.assertIn('class="skip-link"', HTML)
        self.assertIn('id="stage"', HTML)


class ShellIdentityTests(unittest.TestCase):
    def test_release_identity_is_updated(self) -> None:
        self.assertIn("5.1.0", HTML)
        self.assertNotIn("World Engine 5.0.1 Companion", HTML)

    def test_operator_controls_are_still_present(self) -> None:
        """The redesign must not remove existing companion capability."""
        for control in ("forge-button", "connect-button", "retry-endpoint", "configure-ngrok",
                        "world-seed", "authoring-output"):
            self.assertIn(control, HTML, f"existing companion control disappeared: {control}")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
