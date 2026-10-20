/*
 * mod-night-brightness
 *
 * Lets each player set how bright their own night is: .night 1 (darkest) to .night 5 (brightest).
 *
 * The night is in the client's lighting tables, and those hold one night for everybody. But the
 * server can tell a single client to light a place with another Light.dbc row
 * (SMSG_OVERRIDE_LIGHT: the row to replace, the row to use, a fade time), and the client patch
 * patch-z98-night-brightness.MPQ carries every light once more for each level. The tool that
 * builds the patch also writes the list of swaps a level needs on each map, in the order the
 * client has to get them (mod_night_brightness_light). This module keeps each account's level
 * and sends its swaps: when a client loads a map, and when the player changes the level.
 *
 * Level 3 is the tables as they are, so nothing is sent for it. A client without the patch
 * doesn't know the rows it is told about and ignores the swaps.
 *
 * What the client does with a swap decides how they may be sent (Wow.exe 12340):
 *  - It keeps the swaps in a list and blends them in that order. A level's list starts with the
 *    map's default light on a row that reaches everywhere, so it covers all that came before.
 *  - It drops a swap, once faded out, by moving the last swap of the list into the gap. A level
 *    whose swaps are not the end of the list can therefore not be taken away without shuffling
 *    what stays.
 *  - It finds the swap to drop by the row it replaces, and takes the first.
 * So every level swaps rows of its own, and the levels a client has been sent on a map are kept
 * as a stack. A level the client doesn't have is laid on top (it only adds). A level it has,
 * further down, is reached by taking away the ones above it (they are the end of the list).
 * Level 3 takes everything away. One change at a time: what is being dropped has to be gone
 * before anything is added behind it.
 *
 * Other modules can lay a see-through swap over the night: an overlay (mod-weather's overcast
 * skies, through NightBrightness_SetOverlay below). The client's list is one for everything, so
 * this module keeps it: each layer of the stack (the realm's own night at the bottom, then each
 * level) has its own overlay, on rows made for that level, and only the top one shows. A level
 * laid on top covers the overlay below it and gets its own; taking levels away takes their
 * overlays with them.
 *
 * Released under the MIT License.
 */

#include "Chat.h"
#include "ChatCommand.h"
#include "Config.h"
#include "DataMap.h"
#include "DatabaseEnv.h"
#include "GameTime.h"
#include "Log.h"
#include "Opcodes.h"
#include "Player.h"
#include "ScriptMgr.h"
#include "WorldPacket.h"
#include "WorldSession.h"

#include <algorithm>
#include <array>
#include <mutex>
#include <unordered_map>
#include <vector>

using namespace Acore::ChatCommands;

namespace
{
    // The levels the client patch is built with (night_levels.py). The default is the lighting
    // tables themselves: it has no rows to swap.
    constexpr uint8 LEVEL_MIN = 1;
    constexpr uint8 LEVEL_DEFAULT = 3;
    constexpr uint8 LEVEL_MAX = 5;
    constexpr std::array<char const*, LEVEL_MAX + 1> LEVEL_NAMES = { "", "darkest", "dark", "normal", "bright", "brightest" };

    // The fade when a client loads a map: it has just forgotten every swap and is behind a
    // loading screen. Not 0, the client divides by it.
    constexpr uint32 ENTER_FADE_MS = 1;
    // How long after a fade a player has to wait before the next change. The fade runs on the
    // client's clock: this covers the packets' way there and a client that hangs for a moment.
    constexpr Milliseconds CHANGE_MARGIN = 3s;
    // A level asked for while the client was busy goes before overlays for this long after the
    // client is free, so a player isn't kept waiting behind a front that keeps changing the sky.
    constexpr Milliseconds LEVEL_PRIORITY = 10s;

    struct Swap
    {
        uint32 areaLight = 0; // the Light row whose place and reach the swap takes
        uint32 light = 0;     // the Light row drawn there; 0 for no swap

        bool operator==(Swap const& other) const { return areaLight == other.areaLight && light == other.light; }
    };

    using LevelSwaps = std::array<std::vector<Swap>, LEVEL_MAX + 1>;

    bool enabled = true;
    uint32 fadeMs = 1500;

    // map -> level -> its swaps, in the order they are sent. Filled once at startup.
    std::unordered_map<uint32, LevelSwaps> swapsByMap;

    // Accounts that picked another level than the default.
    std::mutex levelLock;
    std::unordered_map<uint32, uint8> levelByAccount;

    // What this character's client has been sent on the map it is on. Lives on the Player
    // object and is only touched by the thread handling that player.
    class ClientNight : public DataMap::Base
    {
    public:
        std::vector<uint8> levels; // bottom to top; the client shows the last, or the default
        // The overlay sent over each layer: [0] over the realm's own night, [i] over levels[i - 1].
        std::vector<Swap> overlays = std::vector<Swap>(1);
        // The overlay another module wants, for each level the client could be on.
        std::array<Swap, LEVEL_MAX + 1> wanted {};
        Milliseconds nextChange = 0ms;
        Milliseconds levelAsked = 0ms; // overlays wait until then: a refused level comes first

        uint8 Shown() const { return levels.empty() ? LEVEL_DEFAULT : levels.back(); }
        Swap const& Wanted() const { return wanted[Shown()]; }
    };

    constexpr char const* CLIENT_NIGHT_KEY = "mod-night-brightness";

    bool IsLevel(uint8 level)
    {
        return level >= LEVEL_MIN && level <= LEVEL_MAX;
    }

    uint8 AccountLevel(uint32 account)
    {
        std::lock_guard<std::mutex> guard(levelLock);
        auto itr = levelByAccount.find(account);
        return itr != levelByAccount.end() ? itr->second : LEVEL_DEFAULT;
    }

    // The swaps of the map the player is on, or nullptr where the night can't be changed.
    LevelSwaps const* MapSwaps(Player* player)
    {
        auto itr = swapsByMap.find(player->GetMapId());
        return itr != swapsByMap.end() ? &itr->second : nullptr;
    }

    void SendSwap(Player* player, uint32 areaLight, uint32 light, uint32 fade)
    {
        WorldPacket data(SMSG_OVERRIDE_LIGHT, 4 + 4 + 4);
        data << uint32(areaLight);
        data << uint32(light); // 0 takes the swap for areaLight away again
        data << uint32(fade);
        player->SendDirectMessage(&data);
    }

    void Add(Player* player, std::vector<Swap> const& swaps, uint32 fade)
    {
        for (Swap const& swap : swaps)
            SendSwap(player, swap.areaLight, swap.light, fade);
    }

    void TakeAway(Player* player, std::vector<Swap> const& swaps, uint32 fade)
    {
        for (Swap const& swap : swaps)
            SendSwap(player, swap.areaLight, 0, fade);
    }

    // Lays the wanted overlay over the top layer. True if a swap was taken away, which the client
    // has to be done with before the next change.
    bool LayOverlay(Player* player, ClientNight* client, uint32 fade)
    {
        Swap& have = client->overlays.back();
        Swap const& want = client->Wanted();
        if (have == want)
            return false;

        // A swap on the same row replaces the old one by itself (the client fades it out).
        bool const dropped = have.light != 0;
        if (have.light && (!want.light || have.areaLight != want.areaLight))
            SendSwap(player, have.areaLight, 0, fade);
        if (want.light)
            SendSwap(player, want.areaLight, want.light, fade);
        have = want;
        return dropped;
    }

    // Takes the client to a level on the map the player is on. True if anything was sent.
    bool Show(Player* player, ClientNight* client, uint8 level, uint32 fade)
    {
        LevelSwaps const* swaps = MapSwaps(player);
        if (!swaps || level == client->Shown())
            return false;

        auto have = std::find(client->levels.begin(), client->levels.end(), level);
        if (level != LEVEL_DEFAULT && have == client->levels.end())
        {
            if ((*swaps)[level].empty())
                return false;

            // The level's first swap covers everything below, the overlay too; it gets its own.
            Add(player, (*swaps)[level], fade);
            client->levels.push_back(level);
            client->overlays.emplace_back();
            LayOverlay(player, client, fade);
            return true;
        }

        // Everything above the level goes, with its overlays; for the default that is everything.
        std::size_t const keep = level == LEVEL_DEFAULT ? 0 : std::size_t(have - client->levels.begin()) + 1;
        for (std::size_t i = keep; i < client->levels.size(); ++i)
        {
            TakeAway(player, (*swaps)[client->levels[i]], fade);
            if (Swap const& overlay = client->overlays[i + 1]; overlay.light)
                SendSwap(player, overlay.areaLight, 0, fade);
        }
        client->levels.resize(keep);
        client->overlays.resize(keep + 1);

        // The overlay that shows again may be out of date.
        LayOverlay(player, client, fade);
        return true;
    }
}

class NightBrightnessPlayerScript : public PlayerScript
{
public:
    NightBrightnessPlayerScript() : PlayerScript("NightBrightnessPlayerScript", { PLAYERHOOK_ON_SEND_INITIAL_PACKETS_BEFORE_ADD_TO_MAP }) { }

    // Fires once each time a client loads a world: at login, when it comes back to a character
    // that stayed in the world after a lost connection, and after every change of map. The
    // client drops its swaps together with the world it leaves, so each of them starts over.
    void OnPlayerSendInitialPacketsBeforeAddToMap(Player* player, WorldPacket& /*data*/) override
    {
        WorldSession* session = player->GetSession();
        if (session->IsHeadless()) // a bot has no client to light anything
            return;

        ClientNight* client = player->CustomData.GetDefault<ClientNight>(CLIENT_NIGHT_KEY);
        client->levels.clear();
        client->overlays.assign(1, Swap());
        client->wanted = {};
        client->nextChange = 0ms;
        client->levelAsked = 0ms;
        if (!enabled)
            return;

        Show(player, client, AccountLevel(session->GetAccountId()), ENTER_FADE_MS);
    }
};

class NightBrightnessCommandScript : public CommandScript
{
public:
    NightBrightnessCommandScript() : CommandScript("NightBrightnessCommandScript") { }

    ChatCommandTable GetCommands() const override
    {
        static ChatCommandTable commandTable =
        {
            { "night", HandleNightCommand, SEC_PLAYER, Console::No },
        };
        return commandTable;
    }

    static bool HandleNightCommand(ChatHandler* handler, Optional<uint8> level)
    {
        Player* player = handler->GetSession() ? handler->GetSession()->GetPlayer() : nullptr;
        if (!player)
            return false;

        if (!enabled || swapsByMap.empty())
        {
            handler->SendSysMessage("Night brightness can't be changed on this server right now.");
            return true;
        }

        uint32 account = handler->GetSession()->GetAccountId();
        uint8 current = AccountLevel(account);
        if (!level)
        {
            handler->PSendSysMessage("Your night brightness is {} ({}). Type .night {} ({}) to .night {} ({}) to change it; "
                "{} is the realm's own night. It changes only what you see, on all your characters.",
                uint32(current), LEVEL_NAMES[current], uint32(LEVEL_MIN), LEVEL_NAMES[LEVEL_MIN],
                uint32(LEVEL_MAX), LEVEL_NAMES[LEVEL_MAX], uint32(LEVEL_DEFAULT));
            return true;
        }

        if (!IsLevel(*level))
        {
            handler->PSendSysMessage("Pick a night brightness from {} ({}) to {} ({}).",
                uint32(LEVEL_MIN), LEVEL_NAMES[LEVEL_MIN], uint32(LEVEL_MAX), LEVEL_NAMES[LEVEL_MAX]);
            return true;
        }

        ClientNight* client = player->CustomData.GetDefault<ClientNight>(CLIENT_NIGHT_KEY);
        Milliseconds now = GameTime::GetGameTimeMS();
        // Only a change that sends something has to wait for the client.
        bool const sends = MapSwaps(player) && *level != client->Shown();
        if (sends && now < client->nextChange)
        {
            client->levelAsked = client->nextChange + LEVEL_PRIORITY;
            handler->PSendSysMessage("The night is still changing. Try again in {} seconds.",
                uint32((client->nextChange - now).count() + 999) / 1000);
            return true;
        }

        if (*level != current)
        {
            {
                std::lock_guard<std::mutex> guard(levelLock);
                if (*level == LEVEL_DEFAULT)
                    levelByAccount.erase(account);
                else
                    levelByAccount[account] = *level;
            }

            if (*level == LEVEL_DEFAULT)
                CharacterDatabase.Execute("DELETE FROM `mod_night_brightness` WHERE `account` = {}", account);
            else
                CharacterDatabase.Execute("REPLACE INTO `mod_night_brightness` (`account`, `level`) VALUES ({}, {})", account, *level);
        }

        if (Show(player, client, *level, fadeMs))
        {
            client->nextChange = now + Milliseconds(fadeMs) + CHANGE_MARGIN;
            client->levelAsked = 0ms;
        }

        if (MapSwaps(player))
            handler->PSendSysMessage("Night brightness set to {} ({}). You will see the difference at night.",
                uint32(*level), LEVEL_NAMES[*level]);
        else
            handler->PSendSysMessage("Night brightness set to {} ({}). Where you are now the night stays the realm's own (as in all of Northrend).",
                uint32(*level), LEVEL_NAMES[*level]);
        return true;
    }
};

class NightBrightnessWorldScript : public WorldScript
{
public:
    NightBrightnessWorldScript() : WorldScript("NightBrightnessWorldScript", { WORLDHOOK_ON_AFTER_CONFIG_LOAD, WORLDHOOK_ON_STARTUP }) { }

    void OnAfterConfigLoad(bool /*reload*/) override
    {
        enabled = sConfigMgr->GetOption<bool>("NightBrightness.Enable", true);
        fadeMs = std::max<uint32>(1, sConfigMgr->GetOption<uint32>("NightBrightness.FadeMs", 1500));
    }

    void OnStartup() override
    {
        uint32 swaps = 0;
        if (QueryResult result = WorldDatabase.Query("SELECT `level`, `map`, `area_light`, `light` FROM `mod_night_brightness_light` ORDER BY `level`, `map`, `position`"))
        {
            do
            {
                Field* fields = result->Fetch();
                uint8 level = fields[0].Get<uint8>();
                if (!IsLevel(level) || level == LEVEL_DEFAULT)
                    continue;

                swapsByMap[fields[1].Get<uint32>()][level].push_back({ fields[2].Get<uint32>(), fields[3].Get<uint32>() });
                ++swaps;
            } while (result->NextRow());
        }

        uint32 accounts = 0;
        if (QueryResult result = CharacterDatabase.Query("SELECT `account`, `level` FROM `mod_night_brightness`"))
        {
            std::lock_guard<std::mutex> guard(levelLock);
            do
            {
                Field* fields = result->Fetch();
                uint8 level = fields[1].Get<uint8>();
                if (!IsLevel(level) || level == LEVEL_DEFAULT)
                    continue;

                levelByAccount[fields[0].Get<uint32>()] = level;
                ++accounts;
            } while (result->NextRow());
        }

        LOG_INFO("server.loading", ">> mod-night-brightness: {} light swaps on {} maps, {} accounts with a level of their own",
            swaps, swapsByMap.size(), accounts);
        if (!swaps)
            LOG_WARN("server.loading", ">> mod-night-brightness: mod_night_brightness_light is empty, .night does nothing");
    }
};

/*
 * For other modules: lays a see-through swap over the night (mod-weather's overcast skies), or
 * takes it away. `areaLights` and `lights` hold `count` entries, one per night level (index 1 to
 * 5, 0 unused): the swap to use over that level, made for it. 0, or a level past `count`, for
 * none. Call it again as often as it may change; it sends only what changed.
 *
 * Returns false while the client is still busy with the last change (a level, or an overlay
 * that was taken away), or a level the player asked for goes first. Nothing was sent then and
 * nothing is kept; call again a little later. Call only from the thread that updates the player.
 */
extern "C" bool NightBrightness_SetOverlay(Player* player, uint32 count, uint32 const* areaLights, uint32 const* lights,
    uint32 fadeMs)
{
    std::array<Swap, LEVEL_MAX + 1> wanted {};
    for (uint8 level = LEVEL_MIN; level <= LEVEL_MAX && level < count; ++level)
        if (areaLights[level] && lights[level])
            wanted[level] = { areaLights[level], lights[level] };

    // Kept only once taken, so it is always what the client has: a level changed later lays the
    // overlay the caller knows about.
    ClientNight* client = player->CustomData.GetDefault<ClientNight>(CLIENT_NIGHT_KEY);
    if (client->overlays.back() == wanted[client->Shown()])
    {
        client->wanted = wanted;
        return true;
    }

    Milliseconds const now = GameTime::GetGameTimeMS();
    if (now < client->nextChange || now < client->levelAsked)
        return false;

    client->wanted = wanted;
    uint32 const fade = std::max<uint32>(1, fadeMs);
    if (LayOverlay(player, client, fade))
        client->nextChange = now + Milliseconds(fade) + CHANGE_MARGIN;
    return true;
}

void AddNightBrightnessScripts()
{
    new NightBrightnessPlayerScript();
    new NightBrightnessCommandScript();
    new NightBrightnessWorldScript();
}
