# Night Brightness

An [AzerothCore](https://www.azerothcore.org/) (WotLK 3.3.5a) module that lets each player set
how bright their own night is, in game, with a chat command.

A realm with dark nights has one night for everybody: it is in the client's lighting tables,
which a client patch replaces for all players alike. But one screen is not another, and one
player's "moody" is another's "can't see the road". With this module and its client patch:

- **`.night` shows your level, `.night 1` to `.night 5` sets it.** 1 is the darkest, 3 is the
  realm's own night, 5 the brightest.
- **Or use the slider.** The optional NightBrightness addon puts it in Interface > AddOns (or
  `/night`).
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
| 2 | 0.83 x | dark |
| 3 | 1 x | normal: the night of the client the patch was built from, what a player sees who never used the command |
| 4 | 1.4 x | bright |
| 5 | 2 x | brightest, but never brighter than the day |

## Patch Notes: Night Brightness

Category: World

- You can now set how dark your own nights are. Type `.night` to see your setting and `.night 1`
  (darkest) to `.night 5` (brightest) to change it. It applies to all your characters and changes
  nothing for anyone else.
- The optional **Night Brightness** addon adds a slider for it under Interface > AddOns. Type
  `/night` to open it.

## Requirements

- AzerothCore WotLK (written against the mod-playerbots branch at `2a2211c`). Needs a core with
  `WorldSession::IsHeadless()`.
- A client patch that holds the night at each level, built from your players' client by
  `tools/build_patch.py` (see "Building the client patch"). The same run writes this module's
  `data/sql/db-world/mod_night_brightness_light.sql`: **the patch and that file belong
  together**. Build both again whenever your client's lighting changes.

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

4. Build the client patch and the SQL for your client (below), and put the patch where your
   players get their patches from. The SQL in this repository comes from one HD client with
   lighting patches of its own; a stock 3.3.5a client has other Light rows and needs its own
   build.

## Building the client patch

```bash
python3 tools/build_patch.py --client /path/to/client --out patch-z98-night-brightness.MPQ \
    --sql data/sql/db-world/mod_night_brightness_light.sql
```

- `--client` is the 3.3.5a client folder your players have (with `Data/` and `Data/enUS/`;
  `--locale` for another language). The four lighting tables (`Light`, `LightParams`,
  `LightIntBand`, `LightFloatBand`) are read from the last archive the client loads that has
  each.
- `--patch` adds a patch your players get on top of that client, such as your own lighting
  patch. Repeat it, in load order. Leave the night brightness patch itself out.
- `--out` is the patch. It carries all four lighting tables, so it has to load after every other
  patch that has one: give it a name that sorts after theirs (a `patch-z` name does).
- `--sql` writes the module's list of swaps. Restart the server after replacing it; the updater
  applies it again.
- `--check` also counts, per map, how often the fixed order of the swaps blends zone borders
  differently from the client's own order (see "Known limits").

The night at a level is the night the client shows, scaled on one clock: by day (06:00 to
19:00) nothing changes, from 21:00 to 04:30 every colour is the level's share, and in between it
fades. A brighter level stops at the colour the same light has at noon. Places with one fixed
colour, most dungeons and interiors, have no night and stay as they are.

Needs Python 3 and [StormLib](https://github.com/ladislav-zezula/StormLib) (`STORMLIB` for its
path, default `/usr/local/lib/libstorm.dylib`).

5. Optional: the addon in `addon/NightBrightness` (see below).

## The addon

`addon/NightBrightness` is a slider for the same setting, under Interface > AddOns > Night
Brightness. `/night` opens it, `/night 1` to `/night 5` sets the level directly. It is a face for
the `.night` command and needs no server change:

- the first time its panel opens (or `/night` is used) it sends `.night` once and reads the
  answer; it asks nothing at login, so nothing shows in chat then;
- when the slider has rested for half a second it sends `.night <level>`, one change at a time,
  and waits for the fade before the next (on "The night is still changing" it tries again);
- it keeps the answers to its own commands out of the chat window and shows them in the panel;
- a level typed by hand moves the slider too.

Nothing is saved in the client: the level stays the account's, on the server. Without the module
on the realm the panel says so after ten seconds. It uses Blizzard's own frames, so DragonUI and
other interface addons restyle it like any options page.

To hand it out with Portalkeeper, add a `mod_realm_config_addon` row: source
`https://github.com/buildthehomelab/wow-mod-night-brightness`, ref `main`, install directory
`NightBrightness`.

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

## Overlays from other modules

Other modules can lay a see-through light over the night, such as the overcast skies of
[mod-weather](https://github.com/buildthehomelab/wow-mod-weather). The client keeps one list of
swaps for everything, and its order matters, so this module keeps the list and the other module
asks it:

```cpp
// areaLights and lights: `count` entries, one per night level (1 to 5; 0 unused), the swap to
// lay over that level. 0 for none. False: the client is still busy, call again later.
extern "C" bool NightBrightness_SetOverlay(Player* player, uint32 count, uint32 const* areaLights,
    uint32 const* lights, uint32 fadeMs);
```

Declare it weak (`__attribute__((weak))`, GCC and Clang) and check it isn't null, and the other
module also works without this one. That needs both modules in the same binary: the default
static module build. In a dynamic module build (`MODULES=dynamic`) or with MSVC the other module
can't see this one and both would send swaps into the same list; run only one of them there. Each layer of the stack (the realm's own night, then each
level a client has been sent) has its own overlay, on rows made for that level, and only the top
one shows. A level laid on top covers the overlay below and gets its own; going back down takes
the overlays above with it and brings the one below up to date. An overlay that is replaced or
taken away makes the next change wait like a level change does.

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
  in a hundred or fewer on the continents (`tools/build_patch.py --check` counts it).
- **The core's own light swaps.** A few boss fights change a room's lighting the same way, on
  the map's default light row. They arrive after this module's list and are drawn on top of
  it. A level changed in the middle of such a fight is laid over the fight's lighting until the
  player leaves the map.
- **One change at a time.** What is taken away has to be gone from the client's list before
  anything is added behind it, and the fade runs on the client's clock. The module makes a
  player wait for the fade plus three seconds before the next change. That includes an
  overlay's change (an overcast step fades over up to 20 seconds), so `.night` can ask for a
  little patience while the weather changes; the addon tries again by itself. A level refused
  that way goes first: for ten seconds after the client is free, overlays wait for it. A
  `.night` that sends nothing (the level the client already has, or Northrend) never waits.
- **Nothing here has been seen in a game client yet.** It is built from reading the client's
  code and from a model of its list.

## Troubleshooting

- **`.night` says it can't be changed:** `NightBrightness.Enable` is `0`, or
  `mod_night_brightness_light` is empty (the startup log says how many swaps were loaded).
- **A level changes nothing:** it is day (the levels differ from 21:00 to 04:30, and partly
  from 19:00 and until 06:00), or the character is in Northrend, or the client doesn't have
  the client patch, or the patch and the module's SQL are from different builds.
- **A zone looks wrong at a level but right at 3:** see "Zone borders" above; note the place.

## License

MIT, see [LICENSE](LICENSE).
