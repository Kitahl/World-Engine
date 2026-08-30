# World Engine v3.9.0

Reconstructed verified release on the v3.7.1 deterministic rules baseline.

## NPC life / DECIDE extensions
- causal NPC thoughts and derived mood
- ten canonical needs
- reusable NPC archetype profiles
- shared jobs with capacity-safe SQLite reservations
- bounded deterministic GOAP planning fallback
- DECIDE `mood` consideration support

## Persistent internal 3D world map
- sparse map volumes with x/y/z bounds
- sparse persistent tiles, terrain, movement cost and LOS
- named 3D zones
- cross-map and vertical portals
- deterministic 3D routing
- persistent destructible terrain state

## World systems
- secrets/traps/passive discovery
- reward packages
- quest graph nodes/edges
- deterministic recipe execution
- faction relations
- crime/bounty records
- rumors with confidence/distortion propagation
- population/migration state
- divine favor/corruption and visions
- staged afflictions
- homesteads and town services
- regional climate/magic themes
- encounter templates

The rejected dense-simulation systems remain excluded: fluids, magma physics, body-part/tissue simulation, per-tile temperature propagation, and individual hauling.
