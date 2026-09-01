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
import shutil
import subprocess
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
        for control in re.findall(r"<input\b[^>]*>", HTML, re.IGNORECASE):
            lowered = control.lower()
            if 'id="ngrok-token"' in lowered:
                continue
            for banned in ("api_key", "api-key", "apikey", "bearer", "base_url",
                           "base-url", "baseurl", "server_url", "endpoint_url"):
                self.assertNotIn(banned, lowered, f"connection-credential input present: {control}")

    def test_no_connection_dialog_asks_for_a_server_address(self) -> None:
        for control in re.findall(r"<input\b[^>]*>", HTML, re.IGNORECASE):
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

    def test_renderer_parses_in_a_real_javascript_runtime(self) -> None:
        node = shutil.which("node")
        if node is None:
            self.skipTest("Node.js is not installed for JavaScript syntax verification")
        result = subprocess.run([node, "--check", str(UI / "app.js")], capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_snapshot_ordering_gate_executes_and_never_rewinds_sequence(self) -> None:
        """Run the shipped gate itself, not a reimplementation of its rule."""
        node = shutil.which("node")
        if node is None:
            self.skipTest("Node.js is not installed for JavaScript behavior verification")
        harness = r'''
const fs = require("fs");
const source = fs.readFileSync(process.argv[1], "utf8");
const signature = "function applySnapshot(data, generation)";
const start = source.indexOf(signature);
if (start < 0) { throw new Error("snapshot gate not found"); }
const body = source.indexOf("{", start);
let depth = 0;
let end = -1;
for (let index = body; index < source.length; index += 1) {
  if (source[index] === "{") { depth += 1; }
  if (source[index] === "}") { depth -= 1; if (depth === 0) { end = index + 1; break; } }
}
if (end < 0) { throw new Error("snapshot gate is not balanced"); }
let appliedGeneration = -1;
let appliedSequence = -1;
const SUPPORTED_SCHEMAS = ["WE-DESKTOP-5.1.0"];
const renders = [];
function safeObject(value) { return value && typeof value === "object" && !Array.isArray(value) ? value : {}; }
function showNotice() {}
function render(value) { renders.push(value.projection_sequence); }
eval(source.slice(start, end));
function send(sequence, generation) {
  applySnapshot({ schema: "WE-DESKTOP-5.1.0", projection_sequence: sequence }, generation);
}
send(0, 1);   // initial zero is valid
send(0, 2);   // same revision may carry a newer endpoint state
send(9, 3);   // newer authoritative state is valid
send(9, 4);   // same revision remains valid for a newer request
send(8, 5);   // rewind must be rejected even for the latest request
send(10, 6);  // strict advance is valid
if (JSON.stringify(renders) !== JSON.stringify([0, 0, 9, 9, 10])) {
  throw new Error("unexpected render sequence: " + JSON.stringify(renders));
}
if (appliedSequence !== 10 || appliedGeneration !== 6) {
  throw new Error("gate state was not monotonic");
}
'''
        result = subprocess.run([node, "-e", harness, str(UI / "app.js")], capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_map_is_local_interactive_and_keyboard_selectable(self) -> None:
        for marker in ("mapView", "pointerdown", "wheel", "setPointerCapture", "map-location-list", "aria-pressed"):
            self.assertIn(marker, JS, f"map interaction marker missing: {marker}")
        self.assertNotIn("select_location(", JS, "map selection must not invoke a world-changing bridge method")

    def test_chronicle_only_reads_allowlisted_presentation_fields(self) -> None:
        chronicle = JS[JS.index('sectionTitle("Chronicle")'):JS.index('sectionTitle("Available world actions")')]
        for field in ("entry.id", "entry.title", "entry.narration", "entry.accepted_at", "entry.world_time"):
            self.assertIn(field, chronicle)
        for forbidden in ("private", "secret", "memory", "raw_event"):
            self.assertNotIn("entry." + forbidden, chronicle)


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

    def test_compact_layout_keeps_rail_and_drawer_usable(self) -> None:
        self.assertIn('window.matchMedia("(max-width: 980px)")', JS)
        self.assertIn('cockpit.dataset.drawer = compactQuery.matches ? "hidden" : "shown"', JS)
        compact_css = CSS[CSS.rfind("@media (max-width: 760px)"):]
        self.assertIn(".rail-modes", compact_css)
        self.assertIn("grid-template-columns: repeat(7, minmax(0, 1fr))", compact_css)


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
