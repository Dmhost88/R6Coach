# Knowledge data

The coach's strategy knowledge is plain YAML in [r6coach/knowledge/data/](../r6coach/knowledge/data/), versioned with the code. Nothing in it is fetched at run time. This page says what each folder holds, how the code uses it, and how to change it safely.

| Path | One file per | Loaded by | Used for |
|---|---|---|---|
| `maps/<map_id>.yaml` | Ranked map | `knowledge.Knowledge` | sites, spawns, per-site defender/attacker plans, common mistakes, general tips |
| `callouts/<map_id>.yaml` | map | `knowledge.Knowledge` | exact room names per floor, so the coach says real callouts |
| `layout/<map_id>.yaml` | map | `maplayout.MapLayouts` | room boxes on Ubisoft's blueprint images, map features, compass sides, for the live map |
| `setups/<map_id>.yaml` | map | `setups.SetupLibrary` | standard setup briefs: where each piece of utility goes, per site/side/operator |
| `operators.yaml` | — | `knowledge.Knowledge` | operator names, sides, roles |
| `meta.yaml` | — | `knowledge.Knowledge` | season and patch, the Ranked map pool, ban meta and other cross-map notes |

`map_id` is lowercase with underscores (`kafe_dostoyevsky`, `nighthaven_labs`) and is the same in every folder. The current pool is 15 maps: bank, border, calypso_casino, chalet, clubhouse, coastline, consulate, fortress, kafe_dostoyevsky, kanal, lair, nighthaven_labs, oregon, outback, villa.

## maps/

```yaml
id: chalet
name: Chalet
aliases: ["Chalet", "CHALET"]      # how the game or the player might name it
floors: [basement, 1F, 2F, roof]
attacker_spawns:
  - name: "Front Yard"
    notes: "..."
sites:
  - id: master_bedroom_office       # referenced by setups/
    name: "2F Master Bedroom / 2F Office"
    aliases: ["Master Bedroom", "Office"]
    floor: 2F
    defender: {reinforce: [...], rotates: [...], anchor_spots: [...], roam_areas: [...], plan_summary: "..."}
    attacker: {entry_points: [...], plant_spots: [...], drone_paths: [...], plan_summary: "..."}
    # the real files carry more keys (recommended_ops, executes, post_plant, retake_tips, …);
    # copy an existing map as the template
    common_mistakes: [...]
    uncertain: [...]                # things sources disagreed on; the coach treats them as unsure
general: {attack: [...], defense: [...]}
```

Write entries the way the coach would say them: exact callouts, short, specific. Put anything you could not confirm under `uncertain` rather than stating it as fact.

## callouts/

Room names per floor, plus a `version_note` that says which season and sources they were checked against. The coach is told to use only these names. When Ubisoft reworks a map, update this file first; `layout/` and `setups/` refer to these names.

## layout/

Made by `tools/locate_rooms.py` (Claude vision on Ubisoft's blueprint images), then corrected by hand. Per floor: the blueprint image name and size, and each room's box `[x1, y1, x2, y2]` in percent of the image (x left→right, y top→bottom) with a confidence. Optional, added by hand:

```yaml
sides: {top: "Lakeside", bottom: "Front Yard", left: "Cliffside", right: "Campfire"}
floors:
  - floor: 2F
    features:
      - {kind: stairs, name: "Library Stairs", at: [64.5, 64.0]}
      - {kind: hatch,  name: "Library hatch",  at: [35.5, 77.0]}
```

The format is documented in full in the docstring of [r6coach/maplayout.py](../r6coach/maplayout.py). The images themselves are Ubisoft's and live only in `data/maps/` (git-ignored); fetch them with `tools/fetch_blueprints.py`.

## setups/

Standard setup briefs. The format, with every field explained, is in [setups/README.md](../r6coach/knowledge/data/setups/README.md). Each placement is placed on the map `between` two rooms (a wall), at a `room` (the centre of its box), or `at` an explicit point. The player can attach their own picture per placement from the dashboard; those go to `data/setups/` and are never committed. The dashboard's Setups tab shows every brief and picks up edits to these files without a restart; it links each brief to its line on GitHub, so keep each brief's `- id:` as the first line of its entry.

## Checking a change

Always run both after editing any of these files:

```bash
.venv/Scripts/python tools/check_setups.py
```

```bash
.venv/Scripts/python -m pytest -q tests/test_knowledge.py tests/test_setups.py tests/test_maplayout.py
```

`check_setups.py` exits 1 if a file does not load, a brief names a site that does not exist, or a placement does not land on the map. Then look at the result in the dashboard preview:

```bash
.venv/Scripts/python tools/preview_dashboard.py --map Chalet --side defense --site "Master Bedroom" --op Kaid --screen prep_phase --port 8766
```

## Season refresh

Each new season, on a `data/season-<YxSy>` branch:

1. Update `meta.yaml` (season, Ranked map pool, ban meta) and `operators.yaml` (new operators, role changes).
2. For reworked maps: callouts → maps → re-run `tools/fetch_blueprints.py` and `tools/locate_rooms.py <map_id>` → fix the layout by hand → fix setups.
3. For maps added to or removed from the Ranked pool: add or remove the file in every folder.
4. Run the checks above and `tools/eval_real.py`, and note sources and season in each changed file's `version_note`.

## Sources and licences

Strategy text is summarised in our own words from public guides; do not paste guide text verbatim. Blueprint images are Ubisoft's, for personal use, and stay out of the repository.
