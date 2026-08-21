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

-- Containers we recurse into. Anything not listed (Terrain, Camera, Sound,
-- decals, meshes, folders of non-scripts) is skipped.
local CONTAINER_CLASSES = {
	DataModel = true,
	Workspace = true,
	ReplicatedStorage = true,
	ServerScriptService = true,
	ReplicatedFirst = true,
	StarterGui = true,
	StarterPlayer = true,
	StarterPack = true,
	Players = true,
	Lighting = true,
	SoundService = true,
	Chat = true,
	Teams = true,
	PlayerGui = true,
	Backpack = true,
	CoreGui = true,
	CorePackages = true,
	LocalizationService = true,
	TestService = true,
	MaterialService = true,
	ProximityPromptService = true,
	PathfindingService = true,
	PhysicsService = true,
	InsertService = true,
	RenderingService = true,
	Selection = true,
	NetworkClient = true,
	NetworkServer = true,
	TextChatService = true,
	BadgeService = true,
	CollectionService = true,
	Debris = true,
	DraggerService = true,
	FriendService = true,
	GamePassService = true,
	HapticService = true,
	HttpRbxApiService = true,
	KeyframeSequenceProvider = true,
	LogService = true,
	MarketplaceService = true,
	MemoryService = true,
	MessagingService = true,
	PackageService = true,
	PermissionsService = true,
	PlayerEmulatorService = true,
	PolicyService = true,
	PointsService = true,
	RobloxReplicatedStorage = true,
	RobloxServerStorage = true,
	RobloxSoundService = true,
	SocialService = true,
	StarterPlayerScripts = true,
	StarterPlayerGui = true,
	TeleportService = true,
	ThirdPartyUserService = true,
	UserInputService = true,
	UserService = true,
	VoiceChatService = true,
	VRService = true,
	WebService = true,
	WorkspaceService = true,
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

-- Collect (rel, source) pairs from the live tree.
local function collectSources(instance: Instance, out: { any })
	for _, child in instance:GetChildren() do
		if SCRIPT_CLASSES[child.ClassName] then
			table.insert(out, { relPathOf(child), child.Source })
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

-- Poll the tree every ~2s and write only when it changed.
local lastPayload: string? = nil
RunService.Heartbeat:Connect(function()
	if (RunService:GetFrameNumber() % 120) ~= 0 then
		return
	end
	local now = buildPayload()
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
