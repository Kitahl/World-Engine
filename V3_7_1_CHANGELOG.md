# World Engine v3.7.1 Changelog

## Fixed — YouTube Error 153

- Replaced pywebview in-memory music HTML with a loopback `ThreadingHTTPServer` page, giving the YouTube iframe a real embedding origin.
- Added `Referrer-Policy: strict-origin-when-cross-origin` HTTP response header and matching HTML meta policy.
- Added YouTube IFrame `origin` and `widget_referrer` parameters.
- Added a pywebview 6.x `request_sent` hook that writes the player URL as `Referer` only for YouTube-owned embed/API requests.
- Forced that one request event synchronous because pywebview's EdgeChromium backend compares mutated headers immediately after firing the event.
- Added explicit player `onError` handling for 153, 100, 101/150 diagnostics.
- No yt-dlp, media extraction, hidden player, or unofficial YouTube transport was introduced.

## Verification

- Music-focused tests: 10/10 PASS.
- Complete World Engine suite: 158/158 PASS.
- GPT-visible OpenAPI operations remain 30 with zero duplicate operation IDs.
