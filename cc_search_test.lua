local BASE = "https://YOUR-APP-NAME.onrender.com"

local function urlEncode(s)
    return tostring(s):gsub("([^%w%-_%.~])", function(c)
        return string.format("%%%02X", string.byte(c))
    end)
end

local function get(url)
    local res, err = http.get(url)
    if not res then
        return nil, err
    end

    local body = res.readAll()
    res.close()
    return body, nil
end

print("Testing bridge...")
local pong, err = get(BASE .. "/ping")
if not pong then
    print("Ping failed: " .. tostring(err))
    return
end
print("Ping: " .. tostring(pong))

print("Search YouTube:")
local q = read()

local body, err = get(BASE .. "/search?q=" .. urlEncode(q) .. "&max_results=5")
if not body then
    print("HTTP failed: " .. tostring(err))
    return
end

local data = textutils.unserializeJSON(body)
if not data then
    print("Bad JSON:")
    print(body)
    return
end

if data.ok == false then
    print("Bridge error:")
    print(body)
    return
end

if data.mode then
    print("Mode: " .. tostring(data.mode))
end

for i, v in ipairs(data.results or {}) do
    print("")
    print(i .. ". " .. tostring(v.title))
    print("   " .. tostring(v.channel))
    print("   id=" .. tostring(v.id))
end
