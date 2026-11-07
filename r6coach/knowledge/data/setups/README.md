# Standard setup briefs

One YAML file per map (`<map_id>.yaml`, the same id as `knowledge/data/maps/<map_id>.yaml`).
`r6coach/setups.py` loads them; the dashboard shows the briefs for the current map, site, side and
operator during ban, pick and prep, and the coach sees them in the map dossier.

```yaml
map: chalet
briefs:
  - id: chalet-office-kaid-library-wall   # unique across all maps; lowercase, digits, dashes
    site: master_bedroom_office           # a site id from maps/<map_id>.yaml
    side: defense                         # defense | attack
    operators: [Kaid]                     # who the brief is for; [] = whole team
    title: "Claw the Office-Library wall" # short, names the job
    text: >-                              # 1-3 sentences, exact callouts, said like the coach would
      Reinforce both Office-Library panels, then claw the lower one from the Office side ...
    placements:                           # where each piece of utility goes, in the order to do it
      - kind: reinforce
        floor: 2F
        between: [Office, Library]        # a wall: the shared edge of the two room boxes
        note: "Both panels"
      - kind: kaid
        floor: 2F
        between: [Office, Library]
        nudge: [-1.0, 0]                  # optional shift in % of image (x, y) so icons don't stack
        note: "Low on the Office side"
      - kind: hatch
        floor: 2F
        at: [36.0, 77.0]                  # explicit point, % of the blueprint image (x left->right, y top->bottom)
        name: "Library hatch"             # what the spot is called (needed with `at`)
        note: "Reinforce it"
      - kind: plant
        floor: 2F
        room: Office                      # a room: the centre of its box
```

Position, first match wins: `at` (explicit), `between: [A, B]` (wall between two rooms; use
`exterior` as B for an outside wall and give `edge: top|bottom|left|right`), `room` (centre).
Room names must be rooms in `layout/<map_id>.yaml` on that floor, or the icon is listed but not drawn.
`floor` uses the layout's floor names (B, 1F, 2F, 3F, ROOF).

Placement kinds: reinforce, hatch (reinforced hatch), open (a rotate or head hole to open),
rotate, mira, bandit, kaid, castle, deployable (shield), trap, camera, anchor (where to stand),
thermite, hibana, maverick, ace, breach (any soft or hard breach), entry (window or door to
enter), drone (drone spot), plant.

Screenshots: every placement has a picture slot. The file lives in
`data/setups/<map_id>/<brief_id>-<n>.jpg|png|webp` (n = placement number, from 1), which is
git-ignored. Drop a file on the slot in the dashboard, or pick one of your own saved frames. Don't
put third-party guide images in the repo.
