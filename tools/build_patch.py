#!/usr/bin/env python3
"""The client patch of mod-night-brightness: the night at five brightness levels, and the
module's list of light swaps (`mod_night_brightness_light`) that goes with it.

    python3 build_patch.py --client /path/to/client --out patch-z98-night-brightness.MPQ \\
        --sql ../data/sql/db-world/mod_night_brightness_light.sql \\
        [--patch your-lighting-patch.MPQ ...] [--check]

--client is a 3.3.5a client folder (with Data/ and Data/<locale>/). Each of the four lighting
tables (Light, LightParams, LightIntBand, LightFloatBand) is read from the last archive the client
loads that has it: Data/patch-*.MPQ other than patch, patch-2 and patch-3, last name first, then
Data/<locale>/patch-<locale>-*.MPQ, then Data/patch-3, patch-2 and patch.MPQ, then
Data/<locale>/locale-<locale>.MPQ. --patch adds patches your players load after those (give them
in load order); leave the built patch itself out. The night your client shows is level 3.

The lighting tables can hold only one night. But the server can tell one client to light a place
with another Light row: SMSG_OVERRIDE_LIGHT names a Light row to replace, the row to use in its
place and a fade time, and the client keeps a list of them (0x7F10A0 in Wow.exe, build 12340).
Each takes the position and reach of the row it replaces and is blended in over the client's own
result, in the order of the list (0x7F14DB). So the patch carries every light once more per
level, on a map number that doesn't exist, where the client never uses them by itself, and the
module sends a player the swaps for their level.

    level 1   0.6 x
    level 2   0.83 x
    level 3   1 x      the client's own night: nothing is sent
    level 4   1.4 x
    level 5   2 x

A level scales the colours of LightIntBand.dbc (sunlight, ambient light, sky, fog, clouds, water)
on one clock: by day (06:00 to 19:00) nothing changes, from 21:00 to 04:30 every colour is the
level's share of what the client shows, and in between it fades. A brighter level stops at the
row's noon colour, so the night never gets brighter than the day. Keys are added at the clock's
corners where a row has room for them, so rows with only a midnight and a noon key keep the day
as it is. Rows with one colour (most dungeons and interiors) have no night and are left alone.

A LightParams row whose colours differ at any level is copied for each level (ID + 3000 x step,
with its LightIntBand and LightFloatBand rows), and every Light row is (ID + 10000 x step,
map 9000), with the copied params in its slots.

The swaps only work as a whole map. The client blends a map's default light first, then the zone
lights the camera is in, farthest centre first (0x7ED0A0), then the swaps. A swap for one zone on
top of that would let the unswapped night through wherever the zone's light is fading out. So a
level replaces all of it: first the map's default light, on a Light row that reaches everywhere
(ID 9000 + level; the default rows themselves have no reach, and a swap is only drawn within the
reach of the row it replaces), then every zone light of the map, widest first. The fixed order
is not the client's, which sorts by distance every frame; --check counts, in a model of the
spheres alone, how often that gives another colour.

Each level swaps rows of its own: a zone light's copy is put in the place of that same copy.
The client drops a swap by moving the last one of its list into the gap (0x7F1648), which would
shuffle a list that has to stay in order, and it finds the swap to drop by the row it replaces.
With rows of its own a second level can be laid over the first without dropping anything (its
first swap covers all that came before), and taking the top level away again only drops swaps
from the end of the list. The module keeps to that.

Left out:

- Northrend (map 571). Eleven of its zones are lit through outlines in Wow.exe, not through the
  reach of a Light row (the table at 0xADEF58, blended before everything else at 0x7F13A0). The
  swap of the default light would cover them, at noon as well, and nothing can be swapped back
  into an outline.
- Zone lights that reach less than 3 yards: the client leaves those out itself (0x7F1432).

A map without Light rows of its own is lit by Light row 1 (0x7ECC4C): it gets that row's copy as
its only swap. The maps are read from the client's Map.dbc.

The patch holds all four lighting tables, so it has to load after every other patch that has
one: name it so it sorts after them (a patch-z name does). Build it again whenever any of them
changes. The patch and the SQL have to come from the same build.

Needs Python 3 and StormLib (`STORMLIB`, default /usr/local/lib/libstorm.dylib).
"""

import argparse
import ctypes
import glob
import math
import os
import random
import struct
import sys
import tempfile

# The night's brightness at each level, as a share of the client's. Level 3 is the tables as they
# are; the module knows the levels by number, so add to the ends, not in between.
LEVELS = {1: 0.6, 2: 1 / 1.2, 3: 1.0, 4: 1.4, 5: 2.0}
DEFAULT = 3
PARAM_STEP, LIGHT_STEP = 3000, 10000
# Where the copies live: a map the client never loads, so it never lights anything with them
# unasked.
NOWHERE = 9000
# The Light rows a map's default light is swapped on, EVERYWHERE + level: everywhere is inside them.
EVERYWHERE = 9000
REACH = (1e9, 2e9)
# Maps with zones the client lights through outlines of its own: a level can't be made whole there.
OUTLINED = {571}
# The client leaves out zone lights that reach less than this (3 yards, in Light.dbc's units).
SMALLEST = 3 * 36.0
# The Light row that lights a map without rows of its own.
FALLBACK_LIGHT = 1

LIGHT = "DBFilesClient\\Light.dbc"
PARAMS = "DBFilesClient\\LightParams.dbc"
INTBAND = "DBFilesClient\\LightIntBand.dbc"
FLOATBAND = "DBFilesClient\\LightFloatBand.dbc"
MAPS = "DBFilesClient\\Map.dbc"
TABLES = (LIGHT, PARAMS, INTBAND, FLOATBAND)
# Light.dbc: ID, map, x, y, z, falloff start, falloff end (floats), then 8 LightParams slots.
SLOTS = 7
# LightParams has 18 LightIntBand rows (ID = (param - 1) * 18 + band + 1) and 6 LightFloatBand rows.
INT_BANDS, FLOAT_BANDS = 18, 6
# LightIntBand.dbc: ID, key count, 16 times, 16 colours. Times are in half minutes.
KEYS = 16
INT_FIELDS = 2 + KEYS * 2
HOUR, DAY = 120, 2880
DUSK, NIGHT, DAWN, MORNING, NOON = 19 * HOUR, 21 * HOUR, 4 * HOUR + 60, 6 * HOUR, 12 * HOUR
# Keys a row gets, most wanted first: the clock's corners, then the middle of each fade.
CLOCK = (NIGHT, DAWN, DUSK, MORNING, 20 * HOUR, 5 * HOUR + 30)

BASE_PATCHES = ("patch-3.mpq", "patch-2.mpq", "patch.mpq")


# --- MPQ ---------------------------------------------------------------------------------------

lib = ctypes.CDLL(os.environ.get("STORMLIB", "/usr/local/lib/libstorm.dylib"))
H = ctypes.c_void_p
for _name, _args in [
    ("SFileOpenArchive", [ctypes.c_char_p, ctypes.c_uint, ctypes.c_uint, ctypes.POINTER(H)]),
    ("SFileCreateArchive", [ctypes.c_char_p, ctypes.c_uint, ctypes.c_uint, ctypes.POINTER(H)]),
    ("SFileAddFileEx", [H, ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint, ctypes.c_uint, ctypes.c_uint]),
    ("SFileExtractFile", [H, ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]),
    ("SFileCloseArchive", [H]),
]:
    getattr(lib, _name).argtypes = _args
    getattr(lib, _name).restype = ctypes.c_bool


def mpqs(folder, pattern):
    return [p for p in glob.glob(os.path.join(folder, "*")) if os.path.basename(p).lower().endswith(".mpq")
            and pattern(os.path.basename(p).lower())]


def search_order(client, locale, extra):
    """The archives that can hold a lighting table, the one the client uses first."""
    data, loc = os.path.join(client, "Data"), locale.lower()
    # Last loaded first: patch-enUS-3 before patch-enUS-2 before patch-enUS.
    by_name = lambda paths: sorted(paths, key=lambda p: os.path.basename(p).lower()[:-4], reverse=True)
    order = list(reversed(extra))
    order += by_name(mpqs(data, lambda n: n.startswith("patch-") and n not in BASE_PATCHES))
    order += by_name(mpqs(os.path.join(data, locale), lambda n: n.startswith("patch-%s" % loc)))
    found = {os.path.basename(p).lower(): p for p in mpqs(data, lambda n: n in BASE_PATCHES)}
    order += [found[n] for n in BASE_PATCHES if n in found]
    order += mpqs(os.path.join(data, locale), lambda n: n == "locale-%s.mpq" % loc)
    return order


def extract(archives, name, dst):
    """The first copy of name in archives; returns the archive it came from."""
    for mpq in archives:
        h = H()
        if not lib.SFileOpenArchive(mpq.encode(), 0, 0x100, ctypes.byref(h)):
            sys.exit("can't open " + mpq)
        try:
            if lib.SFileExtractFile(h, name.encode("latin-1"), dst.encode(), 0):
                return mpq
        finally:
            lib.SFileCloseArchive(h)
    sys.exit("no %s in the client" % name)


def pack(out, files):
    """files: [(local path, archive name)]. zlib-compressed, with listfile and attributes."""
    if os.path.exists(out):
        os.remove(out)
    h = H()
    if not lib.SFileCreateArchive(out.encode(), 0x00300000, max(64, len(files) * 2), ctypes.byref(h)):
        sys.exit("can't create " + out)
    for src, name in files:
        if not lib.SFileAddFileEx(h, src.encode(), name.encode("latin-1"), 0x80000200, 0x02, 0x02):
            lib.SFileCloseArchive(h)
            os.remove(out)
            sys.exit("can't add " + name)
    if not lib.SFileCloseArchive(h):
        os.remove(out)
        sys.exit("can't finish " + out)


# --- DBC ---------------------------------------------------------------------------------------

class Table:
    """A string-less WDBC file as {ID: list of uint32 fields}."""

    def __init__(self, path):
        data = open(path, "rb").read()
        magic, count, self.fields, size, strsize = struct.unpack_from("<4s4I", data, 0)
        if magic != b"WDBC" or size != self.fields * 4 or strsize > 1:
            sys.exit(path + " isn't a WDBC file without strings")
        self.rows = {}
        for i in range(count):
            row = list(struct.unpack_from("<%dI" % self.fields, data, 20 + i * size))
            self.rows[row[0]] = row

    def write(self, path):
        with open(path, "wb") as out:
            out.write(struct.pack("<4s4I", b"WDBC", len(self.rows), self.fields, self.fields * 4, 1))
            for key in sorted(self.rows):
                out.write(struct.pack("<%dI" % self.fields, *self.rows[key]))
            out.write(b"\0")


def floats(row, start, count):
    return struct.unpack("<%df" % count, struct.pack("<%dI" % count, *row[start:start + count]))


def map_ids(archives):
    """Every map of the client."""
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "Map.dbc")
        extract(archives, MAPS, path)
        data = open(path, "rb").read()
    magic, count, _, size, _ = struct.unpack_from("<4s4I", data, 0)
    if magic != b"WDBC":
        sys.exit("Map.dbc isn't a WDBC file")
    return sorted(struct.unpack_from("<I", data, 20 + i * size)[0] for i in range(count))


# --- The night ---------------------------------------------------------------------------------

def channels(colour):
    return [(colour >> shift) & 0xFF for shift in (0, 8, 16)]


def colour_at(times, colours, t):
    """The client's own reading of a row: a straight line between keys, around the clock."""
    before = max((i for i in range(len(times)) if times[i] <= t), default=len(times) - 1)
    after = (before + 1) % len(times)
    span = (times[after] - times[before]) % DAY or DAY
    share = ((t - times[before]) % DAY) / span
    return [a + (b - a) * share for a, b in zip(channels(colours[before]), channels(colours[after]))]


def darkness(t):
    """0 by day, 1 at night."""
    if t >= NIGHT or t <= DAWN:
        return 1.0
    if t >= DUSK:
        return (t - DUSK) / (NIGHT - DUSK)
    if t < MORNING:
        return (MORNING - t) / (MORNING - DAWN)
    return 0.0


def night_row(row, factor):
    """One LightIntBand row with its night times factor, or None for a row without a night: fewer
    than two keys, or keys out of order."""
    count = row[1]
    times = list(row[2:2 + count])
    if count < 2 or times != sorted(set(times)):
        return None
    colours = row[2 + KEYS:2 + KEYS + count]
    top = {t: c & 0xFF000000 for t, c in zip(times, colours)}
    noon = colour_at(times, colours, NOON)
    keys = []
    for t in sorted(times + [t for t in CLOCK if t not in times][:KEYS - count]):
        scale = 1 + darkness(t) * (factor - 1)
        colour = top.get(t, 0)
        for shift, now, day in zip((0, 8, 16), colour_at(times, colours, t), noon):
            colour |= min(255, round(min(now * scale, max(now, day)))) << shift
        keys.append((t, colour))
    out = [row[0], len(keys)] + [t for t, _ in keys] + [0] * (KEYS - len(keys))
    return out + [c for _, c in keys] + [0] * (KEYS - len(keys))


def level_rows(ints, factor):
    """{ID: row} for one level: every row with a night scaled, the rest as they are."""
    return {row_id: night_row(row, factor) or row for row_id, row in ints.items()}


# --- The swaps ---------------------------------------------------------------------------------

def steps():
    """{level: 1, 2, ...} for the levels that need copies."""
    return {level: n + 1 for n, level in enumerate(sorted(l for l in LEVELS if l != DEFAULT))}


def int_ids(param):
    return [(param - 1) * INT_BANDS + b + 1 for b in range(INT_BANDS)]


def float_ids(param):
    return [(param - 1) * FLOAT_BANDS + b + 1 for b in range(FLOAT_BANDS)]


def map_lights(lights, map_id):
    """A map's default light and its zone lights, the way the client sorts them out (0x7ECB30):
    the last row at 0, 0, 0 is the default, every row somewhere else is a zone light."""
    default, zone = None, []
    for key in sorted(lights):
        row = lights[key]
        if row[1] != map_id:
            continue
        if floats(row, 2, 3) == (0.0, 0.0, 0.0):
            default = key
        else:
            zone.append(key)
    return default, zone


def widest_first(lights, zone):
    """The zone lights the client draws, in the order a level's swaps are sent."""
    return sorted((key for key in zone if floats(lights[key], 6, 1)[0] >= SMALLEST),
                  key=lambda key: (-floats(lights[key], 6, 1)[0], key))


def midnight(ints, param):
    """A param's sunlight, ambient, sky and fog colour at 00:00, for --check."""
    out = []
    for band in (0, 1, 2, 7):
        row = ints.get((param - 1) * INT_BANDS + band + 1)
        if not row or not row[1]:
            out += [0, 0, 0]
            continue
        colour = row[18] if row[2] == 0 else row[18 + row[1] - 1]
        out += channels(colour)
    return out


def check(lights, ints, maps, points=40000):
    """How often the fixed order of the swaps gives another colour than the client's own order
    (farthest centre first), at random places up to 150 yards high inside the zone lights of each
    map. A model of the spheres only; it knows nothing of what else the client does."""
    rnd = random.Random(1)
    for map_id in maps:
        default, zone = map_lights(lights, map_id)
        if default is None or not zone:
            continue
        zone = widest_first(lights, zone)
        if not zone:
            continue
        colour = {key: midnight(ints, lights[key][SLOTS]) for key in zone + [default]}
        place = {key: floats(lights[key], 2, 5) for key in zone}
        rank = {key: n for n, key in enumerate(zone)}
        covered = off = 0
        worst = 0.0
        for _ in range(points):
            x, _, z, _, end = place[rnd.choice(zone)]
            angle, r = rnd.uniform(0, 2 * math.pi), end * math.sqrt(rnd.random())
            at = (x + r * math.cos(angle), rnd.uniform(0, 5400), z + r * math.sin(angle))
            hit = []
            for key in zone:
                lx, ly, lz, start, end = place[key]
                d = math.dist(at, (lx, ly, lz))
                if d < end:
                    hit.append((d, 1.0 if d <= start else 1 - (d - start) / (end - start), key))
            if not hit:
                continue
            covered += 1
            results = []
            for order in (sorted(hit, key=lambda h: -h[0]), sorted(hit, key=lambda h: rank[h[2]])):
                got = colour[default]
                for _, weight, key in order:
                    got = [a + (b - a) * weight for a, b in zip(got, colour[key])]
                results.append(got)
            diff = max(abs(a - b) for a, b in zip(*results))
            off += diff > 4
            worst = max(worst, diff)
        print("  map %d: %d zone lights; the fixed order is off by more than 4/255 at %.1f%% of %d "
              "places (worst %d/255)" % (map_id, len(zone), 100.0 * off / max(covered, 1), covered, worst))


SQL_HEAD = """\
-- mod-night-brightness: the Light.dbc rows to swap for each night brightness level, map by
-- map, in the order the client has to get them. `area_light` is the row whose place and reach
-- the swap takes, `light` the row drawn there: for a zone light the level's copy of it, both
-- times; for the map's default light, at position 0, the level's row that reaches everywhere.
-- Written by the patch builder together with the client patch, which holds the rows named here.
-- Don't edit; build both again.

CREATE TABLE IF NOT EXISTS `mod_night_brightness_light` (
    `level`      TINYINT UNSIGNED NOT NULL,
    `map`        INT UNSIGNED NOT NULL,
    `position`   SMALLINT UNSIGNED NOT NULL,
    `area_light` INT UNSIGNED NOT NULL,
    `light`      INT UNSIGNED NOT NULL,
    PRIMARY KEY (`level`, `map`, `position`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

DELETE FROM `mod_night_brightness_light`;
"""


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--client", required=True, help="the client folder (has Data/ and Data/<locale>/)")
    ap.add_argument("--locale", default="enUS")
    ap.add_argument("--patch", action="append", default=[],
                    help="a patch your players load after the client's own, in load order (repeatable)")
    ap.add_argument("--out", required=True, help="the patch to write")
    ap.add_argument("--sql", required=True, help="where to write the module's list of swaps")
    ap.add_argument("--check", action="store_true", help="count where the fixed order differs from the client's")
    args = ap.parse_args()
    for mpq in args.patch:
        if not os.path.isfile(mpq):
            sys.exit("no such patch: " + mpq)
    if os.path.abspath(args.out) in {os.path.abspath(p) for p in args.patch}:
        sys.exit("--out is one of the --patch files; build from the patches it loads after")

    archives = search_order(args.client, args.locale, args.patch)
    tables = {}
    with tempfile.TemporaryDirectory() as tmp:
        for name in TABLES:
            path = os.path.join(tmp, name.split("\\")[1])
            source = extract(archives, name, path)
            tables[name] = Table(path)
            print("%s from %s" % (name.split("\\")[1], source))
    if tables[INTBAND].fields != INT_FIELDS:
        sys.exit("unexpected LightIntBand.dbc layout")
    lights, params = tables[LIGHT].rows, tables[PARAMS].rows
    ints, flts = tables[INTBAND].rows, tables[FLOATBAND].rows
    if max(params) >= PARAM_STEP or max(lights) >= EVERYWHERE or EVERYWHERE + max(LEVELS) >= LIGHT_STEP:
        sys.exit("the IDs have outgrown the steps")

    stock_params = len(params)
    by_level = {level: level_rows(ints, LEVELS[level]) for level in steps()}
    varies = sorted(p for p in params
                    if any(rows[i] != ints[i] for rows in by_level.values() for i in int_ids(p) if i in ints))
    for level, step in steps().items():
        for p in varies:
            copy = p + PARAM_STEP * step
            params[copy] = [copy] + params[p][1:]
            for old, new in zip(int_ids(p), int_ids(copy)):
                if old in ints:
                    ints[new] = [new] + by_level[level][old][1:]
            for old, new in zip(float_ids(p), float_ids(copy)):
                if old in flts:
                    flts[new] = [new] + flts[old][1:]

    changing = set(varies)
    originals = sorted(lights)
    has_night = {key for key in originals if changing & set(lights[key][SLOTS:SLOTS + 8])}
    if NOWHERE in {row[1] for row in lights.values()}:
        sys.exit("map %d has lights of its own" % NOWHERE)
    lit = {lights[key][1] for key in originals}
    if FALLBACK_LIGHT not in lights:
        sys.exit("Light %d is gone" % FALLBACK_LIGHT)
    # map -> [the row whose night its default light has, then its zone lights]
    orders = {}
    for map_id in sorted(lit | set(map_ids(archives))):
        if map_id in OUTLINED or map_id == NOWHERE:
            continue
        if map_id not in lit:
            if FALLBACK_LIGHT in has_night:
                orders[map_id] = [FALLBACK_LIGHT]
            continue
        default, zone = map_lights(lights, map_id)
        order = [default if default is not None else FALLBACK_LIGHT] + widest_first(lights, zone)
        if has_night & set(order):
            orders[map_id] = order
    swaps = []  # (level, map, order, the row to replace, the row to use)
    for level, step in steps().items():
        for map_id, order in orders.items():
            swaps.append((level, map_id, 0, EVERYWHERE + level, order[0] + LIGHT_STEP * step))
            swaps += [(level, map_id, n, key + LIGHT_STEP * step, key + LIGHT_STEP * step)
                      for n, key in enumerate(order[1:], 1)]
    if args.check:
        check(lights, ints, [m for m in orders if m in lit])
    for level, step in steps().items():
        for key in originals:
            row = lights[key]
            lights[key + LIGHT_STEP * step] = (
                [key + LIGHT_STEP * step, NOWHERE] + row[2:SLOTS]
                + [p + PARAM_STEP * step if p in changing else p for p in row[SLOTS:SLOTS + 8]] + row[SLOTS + 8:])
        lights[EVERYWHERE + level] = ([EVERYWHERE + level, NOWHERE, 0, 0, 0]
                                      + list(struct.unpack("<2I", struct.pack("<2f", *REACH)))
                                      + lights[FALLBACK_LIGHT + LIGHT_STEP * step][SLOTS:])

    for row in lights.values():
        for p in row[SLOTS:SLOTS + 8]:
            if p and p not in params:
                sys.exit("Light %d uses LightParams %d, which doesn't exist" % (row[0], p))

    with tempfile.TemporaryDirectory() as tmp:
        out = []
        for name in TABLES:
            path = os.path.join(tmp, name.split("\\")[1])
            tables[name].write(path)
            out.append((path, name))
        os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
        pack(args.out, out)

    with open(args.sql, "w") as sql:
        sql.write(SQL_HEAD)
        for at in range(0, len(swaps), 500):
            sql.write("INSERT INTO `mod_night_brightness_light` (`level`, `map`, `position`, `area_light`, `light`) VALUES\n")
            sql.write(",\n".join("(%d, %d, %d, %d, %d)" % swap for swap in swaps[at:at + 500]) + ";\n")

    print("levels: " + ", ".join("%d = %.2f" % (level, LEVELS[level]) for level in sorted(LEVELS)))
    print("%d of %d LightParams have a night and are copied %d times, like all %d Light rows (%d with a night)"
          % (len(varies), stock_params, len(steps()), len(originals), len(has_night)))
    print("%d maps (%d lit by Light %d), %d swaps in %s"
          % (len(orders), sum(m not in lit for m in orders), FALLBACK_LIGHT, len(swaps), args.sql))
    print("wrote " + args.out)


if __name__ == "__main__":
    main()
