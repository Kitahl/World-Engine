# License/content notes

This package contains original glue/runtime code generated for the user's World Engine plus the user's legacy World Engine specification.

It does **not** bundle proprietary D&D rulebooks, spell catalogs, monster catalogs, or other non-user-supplied Wizards of the Coast book content. The gameplay kernel implements generic dice/state mechanics and a limited baseline 5e-style attack/check model.

Before public/commercial redistribution, review any content later imported into the database separately for its applicable license.

## v3.6 music dependencies

- `pywebview` is **not vendored**; it is installed from PyPI via `requirements-music.txt`. The upstream `r0x0r/pywebview` project is BSD-3-Clause licensed.
- No code from `gajus/youtube-player` or `feross/yt-player` is copied into this package; they were research references only.
- YouTube video/audio bytes are not downloaded or redistributed. Playback uses YouTube's hosted embedded player/IFrame API. Availability and rights for a user-selected YouTube video remain governed by YouTube and the video's rights holder.

## v3.7 deterministic rules kernel

- `world_engine/rules.py` is an original Python/SQLite implementation built for this project.
- Foundry VTT D&D5e was used as an architectural/semantic research reference; Foundry runtime/UI/document code is not vendored.
- No official D&D/SRD spell, feat, class, monster, equipment, or sourcebook text is bundled in v3.7.
- `scripts/seed_rules_demo.py` contains only original generic demonstration mechanics.
- Future rules-content imports must track the licence/source of each dataset separately from the engine software licence.
