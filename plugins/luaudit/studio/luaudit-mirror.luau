--!strict
-- luaudit Studio mirror plugin
--
-- One-way mirror: Studio script tree -> plugin settings (JSON), which the
-- luaudit engine reads and materializes into ~/.luaudit/mirror/ plus a
-- sourcemap.json. Lets the agent hook type-check scripts that only exist
-- inside Studio (MCP-only / script-sync workflows with no local files).
--
-- WHY settings.json: Studio plugins cannot write arbitrary files to disk
-- (writefile/isfolder are not available in the plugin sandbox). The
-- documented persistence channel is Plugin:SetSetting, which writes JSON to
--   %AppData%/Local/Roblox/<UserId>/InstalledPlugins/0/settings.json
-- The engine reads that file, reconstructs the mirror + sourcemap, and
-- checks. Everything stays file-based on the engine side.
--
-- Install: copy this file into %APPDATA%\Roblox\Plugins\ (Studio loads it on
-- startup). Silent: no toolbar, no UI.
--
-- Design:
-- - One-way (Studio -> settings). Never writes back into Studio.
-- - Debounced: 500ms of quiet after the last change before writing.
-- - Stores the whole tree + sources as ONE JSON string under one key
--   (avoids the backslash/newline/quote restrictions on individual keys).
-- - Polls the tree every ~2s and writes only when it changed.

local Plugin = plugin
local HttpService = game:GetService("HttpService")
local RunService = game:GetService("RunService")

local SETTINGS_KEY = "luaudit-mirror-v1"

-- Instance classes we mirror as files.
local SCRIPT_CLASSES = {
	Script = true,
	LocalScript = true,
	ModuleScript = true,
}

-- Containers we recurse into. Deliberately lean: everything reachable from
-- here must be safe AND useful to mirror. Folder and Actor are how real
-- places organize scripts (Rojo projects especially); ServerStorage holds
-- server-only code. Exotic/core services (CoreGui, RobloxReplicatedStorage,
-- etc.) are excluded: their scripts are engine-owned, often unreadable at
-- PluginSecurity, and would make every poll expensive or throwy.
local CONTAINER_CLASSES = {
	DataModel = true,
	Folder = true,
	Actor = true,
	Workspace = true,
	ReplicatedStorage = true,
	ReplicatedFirst = true,
	ServerScriptService = true,
	ServerStorage = true,
	StarterGui = true,
	StarterPlayer = true,
	StarterPlayerScripts = true,
	StarterPlayerGui = true,
	StarterPack = true,
	Players = true,
	Lighting = true,
	SoundService = true,
	Chat = true,
	Teams = true,
	TestService = true,
	TextChatService = true,
}

local function isContainer(instance: Instance): boolean
	return CONTAINER_CLASSES[instance.ClassName] or SCRIPT_CLASSES[instance.ClassName]
end

-- Compute the relative path of a script instance under the place root.
local function relPathOf(instance: Instance): string
	local pathParts: { string } = {}
	local parent = instance.Parent
	while parent and parent ~= game do
		table.insert(pathParts, 1, parent.Name)
		parent = parent.Parent
	end
	table.insert(pathParts, instance.Name)
	local ext = if instance:IsA("ModuleScript") then ".luau" else ".lua"
	return table.concat(pathParts, "/") .. ext
end

-- Build a sourcemap-style tree (flat rojo format, no tree wrapper).
-- Script nodes carry filePaths = {rel}; containers carry children.
local function buildTree(instance: Instance): { any }
	local node = {
		name = instance.Name,
		className = instance.ClassName,
		children = {},
		filePaths = {},
	}
	if SCRIPT_CLASSES[instance.ClassName] then
		node.filePaths = { relPathOf(instance) }
	end
	for _, child in instance:GetChildren() do
		if isContainer(child) then
			table.insert(node.children, buildTree(child))
		end
	end
	return node
end

-- Collect (rel, source) pairs from the live tree. Each read is guarded:
-- a single unreadable script (permissions, deleted mid-walk) must not kill
-- the whole mirror.
local function collectSources(instance: Instance, out: { any })
	for _, child in instance:GetChildren() do
		if SCRIPT_CLASSES[child.ClassName] then
			local ok, source = pcall(function()
				return (child :: any).Source
			end)
			if ok then
				table.insert(out, { relPathOf(child), source })
			end
		end
		if isContainer(child) then
			collectSources(child, out)
		end
	end
end

-- Build the full payload: {sources: {rel: source}, tree: {...}}
local function buildPayload(): string
	local sources: { any } = {}
	collectSources(game, sources)
	local sourcesMap: { [string]: string } = {}
	for _, pair in sources do
		sourcesMap[pair[1]] = pair[2]
	end
	local payload = {
		sources = sourcesMap,
		tree = buildTree(game),
	}
	return HttpService:JSONEncode(payload)
end

-- Debounce: schedule a settings write 500ms after the last change.
local debounceThread: thread? = nil
local function scheduleWrite()
	if debounceThread then
		task.cancel(debounceThread)
	end
	debounceThread = task.delay(0.5, function()
		local ok, err = pcall(function()
			Plugin:SetSetting(SETTINGS_KEY, buildPayload())
		end)
		if not ok then
			warn("luaudit mirror: failed to write settings: " .. tostring(err))
		end
	end)
end

-- Poll the tree every ~2s and write only when it changed. Frame counting
-- uses a Heartbeat accumulator: RunService has no GetFrameNumber API.
local lastPayload: string? = nil
local secondsSincePoll = 0
RunService.Heartbeat:Connect(function(deltaTime: number)
	secondsSincePoll += deltaTime
	if secondsSincePoll < 2 then
		return
	end
	secondsSincePoll = 0
	local ok, now = pcall(buildPayload)
	if not ok then
		warn("luaudit mirror: buildPayload failed: " .. tostring(now))
		return
	end
	if now ~= lastPayload then
		lastPayload = now
		scheduleWrite()
	end
end)

-- Initial mirror on load (after a short delay so the tree is ready).
task.delay(2, function()
	lastPayload = buildPayload()
	scheduleWrite()
end)
