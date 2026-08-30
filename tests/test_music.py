from pathlib import Path
import tempfile
import unittest

from world_engine import WorldEngine
from world_engine.music import MusicResolver, youtube_video_id
from music_player import player_html, start_player_server, make_youtube_referrer_handler, install_youtube_referrer_hook


class MusicResolverTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.db = root / "music.sqlite3"
        self.catalog = root / "music_catalog.json"
        self.engine = WorldEngine(self.db)
        self.engine.ensure_campaign("c1", "Music Test")
        self.engine.upsert_location("c1", "moonwood", "Moonwood Shrine", region="north", realm_id="crown", tags=["forest", "sacred"], state={"music_tags": ["mystic"]})
        self.engine.upsert_character("c1", "hero", "Hero", hp=20, max_hp=20, ac=15, location="moonwood")
        self.resolver = MusicResolver(self.engine, self.catalog)

    def tearDown(self):
        self.tmp.cleanup()

    def write_tracks(self, tracks):
        self.resolver.save_catalog({"version": 1, "defaults": {"volume": 55, "poll_seconds": 2}, "tracks": tracks})

    def test_youtube_url_parser(self):
        vid = "M7lc1UVf-VE"
        self.assertEqual(vid, youtube_video_id(vid))
        self.assertEqual(vid, youtube_video_id(f"https://www.youtube.com/watch?v={vid}&t=3"))
        self.assertEqual(vid, youtube_video_id(f"https://youtu.be/{vid}"))
        self.assertEqual(vid, youtube_video_id(f"https://www.youtube.com/embed/{vid}"))
        self.assertEqual(vid, youtube_video_id(f"https://www.youtube.com/shorts/{vid}"))
        with self.assertRaises(ValueError):
            youtube_video_id("not a youtube id")

    def test_location_beats_fallback(self):
        self.write_tracks([
            {"id":"fallback","name":"Fallback","youtube":"M7lc1UVf-VE","priority":-100,"match":{}},
            {"id":"moonwood","name":"Moonwood","youtube":"dQw4w9WgXcQ","priority":0,"match":{"location_ids":["moonwood"],"combat":False}},
        ])
        decision = self.resolver.resolve("c1")
        self.assertEqual("moonwood", decision.track["id"])
        self.assertIn("location_ids=moonwood", decision.reasons)

    def test_combat_overrides_location_ambient(self):
        self.engine.start_scene("c1", "scene1", "moonwood", scene_type="exploration", entities=[{"kind":"character","id":"hero"}])
        self.engine.upsert_npc("c1", "wolf", "Wolf", hp=8, max_hp=8, ac=12, location="moonwood")
        self.engine.start_combat("c1", "cmb1", "moonwood", [{"kind":"character","id":"hero"},{"kind":"npc","id":"wolf"}], scene_id="scene1")
        self.write_tracks([
            {"id":"ambient","name":"Ambient","youtube":"M7lc1UVf-VE","priority":300,"match":{"location_ids":["moonwood"],"combat":False}},
            {"id":"combat","name":"Combat","youtube":"dQw4w9WgXcQ","priority":400,"match":{"combat":True}},
            {"id":"moon-combat","name":"Moon Combat","youtube":"9bZkp7q19f0","priority":500,"match":{"location_ids":["moonwood"],"combat":True}},
        ])
        decision = self.resolver.resolve("c1")
        self.assertEqual("moon-combat", decision.track["id"])
        self.assertTrue(decision.context["combat"])

    def test_scene_weather_time_and_director_can_match(self):
        self.engine.start_scene("c1", "ritual1", "moonwood", scene_type="ritual", entities=[{"kind":"character","id":"hero"}], state={"music_tags":["ritual"]})
        self.engine.save_director("c1", "gaia", "Gaia", director_kind="divine", scope_type="location", scope_id="moonwood", source_kind="deity", source_id="gaia", authority=1.0)
        self.write_tracks([
            {"id":"ritual","name":"Ritual","youtube":"M7lc1UVf-VE","match":{"scene_types":["ritual"],"director_kinds":["divine"],"scene_tags_any":["ritual"]}},
        ])
        decision = self.resolver.resolve("c1")
        self.assertEqual("ritual", decision.track["id"])
        self.assertIn("divine", decision.context["director_kinds"])

    def test_player_html_keeps_visible_policy_sized_youtube_viewport(self):
        html = player_html()
        self.assertIn("width: 480px", html)
        self.assertIn("height:270px", html)
        self.assertIn("autoplay: 0", html)
        self.assertIn("Enable Background Music", html)

    def test_player_page_uses_real_origin_for_youtube_client_identity(self):
        import urllib.request
        server, origin = start_player_server()
        try:
            with urllib.request.urlopen(origin + "/player", timeout=2) as response:
                html = response.read().decode("utf-8")
                self.assertEqual("strict-origin-when-cross-origin", response.headers.get("Referrer-Policy"))
            self.assertIn(f'const PLAYER_ORIGIN = "{origin}";', html)
            self.assertIn('origin: PLAYER_ORIGIN', html)
            self.assertIn('widget_referrer: PLAYER_ORIGIN', html)
            self.assertIn('name="referrer" content="strict-origin-when-cross-origin"', html)
            self.assertNotIn('__PLAYER_ORIGIN__', html)
        finally:
            server.shutdown()
            server.server_close()

    def test_player_explains_youtube_error_153(self):
        html = player_html("http://127.0.0.1:9999")
        self.assertIn('code === 153', html)
        self.assertIn('client identity/referrer', html)

    def test_request_hook_sets_referer_only_for_youtube(self):
        class Req:
            def __init__(self, url):
                self.url=url
                self.headers={"User-Agent":"test"}
        hook=make_youtube_referrer_handler("http://127.0.0.1:45678")
        yt=Req("https://www.youtube.com/embed/M7lc1UVf-VE?enablejsapi=1")
        hook(yt)
        self.assertEqual("http://127.0.0.1:45678/player",yt.headers.get("Referer"))
        yt2=Req("https://www.youtube-nocookie.com/embed/M7lc1UVf-VE")
        hook(yt2)
        self.assertIn("Referer",yt2.headers)
        other=Req("https://example.com/test")
        hook(other)
        self.assertNotIn("Referer",other.headers)

    def test_referrer_hook_is_synchronous_before_webview2_send(self):
        class Event:
            def __init__(self):
                self._should_lock=False
                self.handlers=[]
            def __iadd__(self, fn):
                self.handlers.append(fn)
                return self
        class Events:
            def __init__(self): self.request_sent=Event()
        class Window:
            def __init__(self): self.events=Events()
        w=Window()
        install_youtube_referrer_hook(w, "http://127.0.0.1:34567")
        self.assertTrue(w.events.request_sent._should_lock)
        self.assertEqual(1,len(w.events.request_sent.handlers))
        class Req:
            url="https://www.youtube.com/embed/M7lc1UVf-VE"
            headers={}
        req=Req(); w.events.request_sent.handlers[0](req)
        self.assertEqual("http://127.0.0.1:34567/player",req.headers.get("Referer"))

    def test_add_track_for_current_context_persists(self):
        entry = self.resolver.add_track_for_context("c1", "https://youtu.be/M7lc1UVf-VE", name="Moonwood Ambient", scope="location", volume=47)
        loaded = self.resolver.load_catalog()
        self.assertEqual(1, len(loaded["tracks"]))
        self.assertEqual(47, loaded["tracks"][0]["volume"])
        self.assertEqual(["moonwood"], entry["match"]["location_ids"])
        self.assertEqual("Moonwood Ambient", self.resolver.resolve("c1").track["name"])


if __name__ == "__main__":
    unittest.main()
