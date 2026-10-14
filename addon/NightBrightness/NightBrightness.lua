-- NightBrightness: a slider for mod-night-brightness.
--
-- The server keeps each account's night brightness and changes it with the .night chat command.
-- This addon is a face for that command: it asks for the level once after login, sends
-- ".night <level>" when the slider moves, and reads the answers the server prints in chat
-- (hiding the ones it asked for). Nothing is saved on this side.
--
-- The server lets a level change only once the last one has faded in (it says "The night is
-- still changing"), so the addon sends one change at a time and tries again a little later.

local LEVEL_MIN, LEVEL_DEFAULT, LEVEL_MAX = 1, 3, 5
local NAMES = { "Darkest", "Dark", "Normal", "Bright", "Brightest" }

local SETTLE = 0.5         -- seconds the slider has to rest before its level is sent
local AFTER_CHANGE = 5     -- the server's fade (1.5 s) and wait (3 s) after a change
local RETRY = 2            -- after "still changing"
local TIMEOUT = 10         -- no answer by then: the realm doesn't run the module
local HIDE_FOR = 6         -- how long the answer to our own command is kept out of chat
local FIRST_ASK = 3        -- after the first loading screen

-- The server's lines (mod-night-brightness, NightBrightness.cpp).
local SHOWN = "^Your night brightness is (%d)"
local SET = "^Night brightness set to (%d)"
local BUSY = "^The night is still changing"
local OFF = "^Night brightness can't be changed"
local OURS = { SHOWN, SET, BUSY, OFF, "^Pick a night brightness", "^There is no such command" }

local current             -- the account's level as the server last said, nil until it has
local wanted              -- the level the slider asks for
local asked               -- what we are waiting for an answer to: "query" or a level
local askedAt = 0
local nextSend = 0        -- don't send before this
local settleAt = 0
local hideUntil = 0
local queryAt             -- when to ask for the level after login
local state = "waiting"   -- waiting, ready, off, absent

local panel, slider, levelText, statusText
local fromServer = false

local function Describe(level)
    local text = level .. ": " .. NAMES[level]
    if level == LEVEL_DEFAULT then
        text = text .. " (the realm's own night)"
    end
    return text
end

local function UpdateStatus()
    if not statusText then
        return
    end
    if state == "absent" then
        statusText:SetText("|cffff6060The realm doesn't answer. It needs the mod-night-brightness module.|r")
    elseif state == "off" then
        statusText:SetText("|cffff6060Night brightness can't be changed on this realm right now.|r")
    elseif not current then
        statusText:SetText("Asking the realm for your setting...")
    elseif wanted and wanted ~= current then
        statusText:SetText("Changing to " .. Describe(wanted) .. "...")
    else
        statusText:SetText("|cff80ff80Your nights: " .. Describe(current) .. ".|r")
    end
end

local function ShowLevel(level)
    if not slider then
        return
    end
    fromServer = true
    slider:SetValue(level)
    fromServer = false
    levelText:SetText(Describe(level))
end

local function Send(command, what)
    asked, askedAt = what, GetTime()
    hideUntil = askedAt + HIDE_FOR
    SendChatMessage(command, "SAY")
end

-- A line from the server's chat channel for system messages.
local function OnSystemMessage(msg)
    local level = tonumber(msg:match(SHOWN) or msg:match(SET))
    if level then
        current = level
        state = "ready"
        if msg:match(SET) then
            nextSend = GetTime() + AFTER_CHANGE
        end
        -- Typed by hand, or the first answer: the slider follows. The answer to the slider's own
        -- change leaves it alone: the player may have moved it on since.
        if asked == nil or (asked == "query" and not wanted) then
            wanted = level
            ShowLevel(level)
        end
        asked = nil
    elseif msg:match(BUSY) then
        asked = nil
        nextSend = GetTime() + RETRY
    elseif msg:match(OFF) then
        asked = nil
        state = "off"
    end
    UpdateStatus()
end

local driver = CreateFrame("Frame")
driver:RegisterEvent("PLAYER_ENTERING_WORLD")
driver:RegisterEvent("CHAT_MSG_SYSTEM")
driver:SetScript("OnEvent", function(self, event, msg)
    if event == "CHAT_MSG_SYSTEM" then
        OnSystemMessage(msg)
    elseif not queryAt then
        queryAt = GetTime() + FIRST_ASK
    end
end)

local elapsed = 0
driver:SetScript("OnUpdate", function(self, delta)
    elapsed = elapsed + delta
    if elapsed < 0.1 then
        return
    end
    elapsed = 0
    local now = GetTime()

    if asked and now - askedAt > TIMEOUT then
        if asked == "query" and not current then
            state = "absent"
        end
        asked = nil
        UpdateStatus()
    end
    if asked or state == "off" or state == "absent" then
        return
    end

    if not current then
        if queryAt and now >= queryAt then
            Send(".night", "query")
        end
        return
    end
    if wanted and wanted ~= current and now >= nextSend and now >= settleAt then
        Send(".night " .. wanted, wanted)
        UpdateStatus()
    end
end)

-- Keep the lines the addon asked for out of the chat window; the panel shows them.
ChatFrame_AddMessageEventFilter("CHAT_MSG_SYSTEM", function(self, event, msg, ...)
    if GetTime() > hideUntil then
        return false
    end
    for _, pattern in ipairs(OURS) do
        if msg:match(pattern) then
            return true
        end
    end
    return false
end)

local function Want(level)
    level = math.max(LEVEL_MIN, math.min(LEVEL_MAX, level))
    wanted = level
    settleAt = GetTime() + SETTLE
    if levelText then
        levelText:SetText(Describe(level))
    end
    UpdateStatus()
end

-- Interface > AddOns > Night Brightness
panel = CreateFrame("Frame", "NightBrightnessPanel", UIParent)
panel.name = "Night Brightness"
panel:Hide()

local title = panel:CreateFontString(nil, "ARTWORK", "GameFontNormalLarge")
title:SetPoint("TOPLEFT", 16, -16)
title:SetText("Night Brightness")

local about = panel:CreateFontString(nil, "ARTWORK", "GameFontHighlightSmall")
about:SetPoint("TOPLEFT", title, "BOTTOMLEFT", 0, -8)
about:SetPoint("RIGHT", panel, "RIGHT", -32, 0)
about:SetJustifyH("LEFT")
about:SetText("How bright your nights are. Only you see the difference, and it is the same on all "
    .. "your characters. Days don't change. Northrend always has the realm's own night.\n\n"
    .. "You can also type .night 1 to .night 5 in chat, or /night 1 to /night 5.")

slider = CreateFrame("Slider", "NightBrightnessSlider", panel, "OptionsSliderTemplate")
slider:SetPoint("TOPLEFT", about, "BOTTOMLEFT", 4, -32)
slider:SetWidth(260)
slider:SetMinMaxValues(LEVEL_MIN, LEVEL_MAX)
slider:SetValueStep(1)
slider:SetValue(LEVEL_DEFAULT)
NightBrightnessSliderLow:SetText(NAMES[LEVEL_MIN])
NightBrightnessSliderHigh:SetText(NAMES[LEVEL_MAX])
NightBrightnessSliderText:SetText("Night brightness")
slider:SetScript("OnValueChanged", function(self, value)
    if fromServer then
        return
    end
    Want(math.floor(value + 0.5))
end)

levelText = panel:CreateFontString(nil, "ARTWORK", "GameFontHighlight")
levelText:SetPoint("TOP", slider, "BOTTOM", 0, -16)
levelText:SetText(Describe(LEVEL_DEFAULT))

statusText = panel:CreateFontString(nil, "ARTWORK", "GameFontNormalSmall")
statusText:SetPoint("TOPLEFT", slider, "BOTTOMLEFT", -4, -48)
statusText:SetPoint("RIGHT", panel, "RIGHT", -32, 0)
statusText:SetJustifyH("LEFT")

panel:SetScript("OnShow", function()
    if current then
        ShowLevel(wanted or current)
    end
    UpdateStatus()
end)

InterfaceOptions_AddCategory(panel)

SLASH_NIGHTBRIGHTNESS1 = "/night"
SLASH_NIGHTBRIGHTNESS2 = "/nightbrightness"
SlashCmdList["NIGHTBRIGHTNESS"] = function(arg)
    local level = tonumber(arg and arg:match("^%s*(%d+)%s*$"))
    if level then
        if state == "absent" or state == "off" then
            UpdateStatus()
            DEFAULT_CHAT_FRAME:AddMessage("Night Brightness: " .. statusText:GetText())
        elseif level < LEVEL_MIN or level > LEVEL_MAX then
            DEFAULT_CHAT_FRAME:AddMessage("Night Brightness: pick 1 (darkest) to 5 (brightest).")
        else
            Want(level)
            ShowLevel(level)
        end
        return
    end
    -- the first call only opens the AddOns list in 3.3.5; the second one opens the page
    InterfaceOptionsFrame_OpenToCategory(panel)
    InterfaceOptionsFrame_OpenToCategory(panel)
end
