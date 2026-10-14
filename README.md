# Night Brightness

An [AzerothCore](https://www.azerothcore.org/) (WotLK 3.3.5a) module that lets each player set
how bright their own night is, in game, with a chat command.

A realm with dark nights has one night for everybody: it is in the client's lighting tables,
which a client patch replaces for all players alike. But one screen is not another, and one
player's "moody" is another's "can't see the road". With this module and its client patch:

- **`.night` shows your level, `.night 1` to `.night 5` sets it.** 1 is the darkest, 3 is the
  realm's own night, 5 the brightest.
- **Only the night changes.** The day is the same at every level; dusk and dawn fade between
  the day and your night.
- **Only you see it.** Each client is sent its own lighting; nobody else's night changes.
- **It fades over.** A change takes a second and a half, without a loading screen or a relog.
- **It is remembered for the account.** Every character of the account gets the level, at login
  and on every map.
- **Bots are skipped.** They have no screen.
- **Not in Northrend.** Everybody has the realm's night there (see "Known limits").

| Level | Night | |
| --- | --- | --- |
| 1 | 0.6 x | darkest |
| 2 | 0.83 x | dark: the realm's night as it was before 2026-10-13 |
| 3 | 1 x | normal: the realm's night, what a player sees who never used the command |
| 4 | 1.4 x | bright |
| 5 | 2 x | brightest: about the night of the Reforged HD client the realm is built on |

## Patch Notes: Night Brightness

Category: World

- You can now set how dark your own nights are. Type `.night` to see your setting and `.night 1`
  (darkest) to `.night 5` (brightest) to change it. It applies to all your characters and changes
  nothing for anyone else.

## Requirements

- AzerothCore WotLK (written against the mod-playerbots branch at `2a2211c`). Needs a core with
  `WorldSession::IsHeadless()`.
- The client patch `patch-z98-night-brightness.MPQ`, which holds the night at each level. It is
  built by `tools/client-patches/night_levels.py` in
  [wow-server](https://github.com/buildthehomelab/wow-server) from the realm's other lighting
  patches. The same run writes this module's `data/sql/db-world/mod_night_brightness_light.sql`:
  **the patch and that file belong together**. Build both again whenever the realm's lighting
  changes.

A player whose client doesn't have the patch sees the realm's own night at every level: the
client is told about lighting rows it doesn't have, and ignores that.

## Installation

1. Clone into the server's `modules/` folder as `mod-night-brightness` and rebuild:

   ```bash
   cd modules && git clone https://github.com/buildthehomelab/wow-mod-night-brightness.git mod-night-brightness
   ```

2. Copy `conf/mod_night_brightness.conf.dist` to `mod_night_brightness.conf` in your config
   folder. The defaults work as-is.

3. The SQL is applied by the server's updater at startup: `mod_night_brightness_light` in the
   world database (the lights of each level), `mod_night_brightness` in the characters database
   (each account's level). `data/sql/uninstall/` takes both out again, by hand.

4. Put the client patch where your players get their patches from.

## Configuration

| Option | Default | What it does |
| --- | --- | --- |
| `NightBrightness.Enable` | `1` | Master switch. Off: the command is refused and nobody is sent a level. |
| `NightBrightness.FadeMs` | `1500` | How long a change of level takes on screen, in milliseconds. A player can change it again a second after that. |

## How it works

The server can tell a single 3.3.5a client to light a place with another row of its `Light.dbc`:
`SMSG_OVERRIDE_LIGHT` names the row to replace, the row to use and a fade time. The client keeps
a list of these swaps. Each one takes the position and reach of the row it replaces, and is
blended over the client's own lighting in the order of the list. The list is emptied whenever
the client leaves a map.

The client patch carries every light once more for each level, on a map number that doesn't
exist, so a client never uses them unasked. A level's list for a map replaces the map's lighting
as a whole: its default light first, on a row of the level's own that reaches everywhere and so
covers all that came before, then every zone light, widest first. `mod_night_brightness_light`
holds those lists.

The module sends a player's list when their client loads a map (at login, after a reconnect,
after a portal) and when the level changes. How it changes a level follows from how the client
keeps its list: a swap that has faded out is dropped by moving the last swap into its place,
which would shuffle a list that has to stay in order. So each level swaps rows of its own, and
the module remembers which levels a client has been sent on its map, as a stack:

- a level the client doesn't have is added on top; its first swap hides everything below;
- a level it has further down is reached by taking away the ones above, which are the end of
  the list;
- level 3 takes everything away.

Either way the night fades from one to the other.

## Cost

Loading a continent at a level other than 3 sends one 12-byte packet per light of that map: 97
for the Eastern Kingdoms, 142 for Kalimdor, 177 for Outland, a handful for a dungeon. Changing
the level sends as many once more. Nothing is sent to a player on level 3, and nothing runs
while a player just plays.

A client that was sent all four levels on one map keeps four lists until it leaves the map. Each
swap costs it a distance check per frame.

Account levels are read once at startup and kept in memory; a change is written in the
background.

## Known limits

- **Northrend.** Eleven Northrend zones are lit through outlines built into the client, not
  through the reach of a `Light.dbc` row. A level's default light would cover them, by day as
  well, so Northrend (map 571) has no lists and everybody has the realm's night there. `.night`
  says so when it is used there.
- **Zone borders.** The client sorts a map's zone lights by distance every frame; a list of
  swaps has one fixed order. Where two zones' lights overlap, a level other than 3 can blend
  them a little differently from the stock night: in a model of the lights, at about one place
  in a hundred or fewer on the continents (`night_levels.py --check` counts it).
- **The core's own light swaps.** A few boss fights change a room's lighting the same way, on
  the map's default light row. They arrive after this module's list and are drawn on top of
  it. A level changed in the middle of such a fight is laid over the fight's lighting until the
  player leaves the map.
- **One change at a time.** What is taken away has to be gone from the client's list before
  anything is added behind it, and the fade runs on the client's clock. The module makes a
  player wait for the fade plus three seconds before the next change.
- **Nothing here has been seen in a game client yet.** It is built from reading the client's
  code and from a model of its list.

## Troubleshooting

- **`.night` says it can't be changed:** `NightBrightness.Enable` is `0`, or
  `mod_night_brightness_light` is empty (the startup log says how many swaps were loaded).
- **A level changes nothing:** it is day (the levels differ from 21:00 to 04:30, and partly
  from 19:00 and until 06:00), or the character is in Northrend, or the client doesn't have
  `patch-z98-night-brightness.MPQ`, or the patch and the module's SQL are from different builds.
- **A zone looks wrong at a level but right at 3:** see "Zone borders" above; note the place.

## Credits

Written for the Vaultrona / Evermore realm.

## License

MIT, see [LICENSE](LICENSE).
