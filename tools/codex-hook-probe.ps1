# codex-hook-probe.ps1 - one-shot PostToolUse hook execution probe for Codex on Windows
# Runs a single `codex exec` inside an isolated CODEX_HOME with ONE user-level
# PostToolUse hook pointing at a sentinel .cmd that writes markers and dumps env/stdin.
# Prints a VERDICT at the end. All artifacts stay under %USERPROFILE%\codex-hook-probe.
# Your real ~/.codex is never touched (config is isolated; auth.json is copied, not moved).

$ErrorActionPreference = 'Continue'
$OutputEncoding = [System.Text.Encoding]::UTF8

if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT) {
  Write-Host 'VERDICT: NOT-WINDOWS - this probe tests Windows hook execution only.'
  exit 3
}

$probeRoot  = Join-Path $env:USERPROFILE 'codex-hook-probe'
$probeHome  = Join-Path $probeRoot 'codex-home'
$work       = Join-Path $probeRoot 'work'
New-Item -ItemType Directory -Force -Path $probeHome, $work | Out-Null

$env:CODEX_HOME = $probeHome
$summary = Join-Path $probeRoot 'summary.txt'
Set-Content -Path $summary -Value 'codex-hook-probe summary'
function Log($m) { Write-Host $m; Add-Content -Path $summary -Value $m }

Log '===== 1. ENVIRONMENT ====='
Log ("platform        : " + [Environment]::OSVersion.VersionString)
Log ("powershell      : " + $PSVersionTable.PSVersion)
Log ("date            : " + (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'))
Log ("probe dir       : $probeRoot")
Log ("isolated CODEX_HOME: $probeHome (your real ~/.codex is untouched)")

$codex = Get-Command codex -ErrorAction SilentlyContinue
if (-not $codex) {
  Log 'VERDICT: CODEX-MISSING - codex not found on PATH. Install with: npm install -g @openai/codex'
  exit 4
}
Log ("codex binary    : " + $codex.Source)
Log ("codex version   : " + (& $codex.Source --version 2>&1 | Select-Object -First 1))

$realAuth = Join-Path $env:USERPROFILE '.codex\auth.json'
$charmKey = $env:HERMES_CUSTOM_HYPER_CHARM_LAND_API_KEY
if ($charmKey) {
  Copy-Item $realAuth (Join-Path $probeHome 'auth.json') -Force -ErrorAction SilentlyContinue
  Log 'auth            : using CUSTOM PROVIDER (Charm Hyper / glm-5.2, key from HERMES_CUSTOM_HYPER_CHARM_LAND_API_KEY)'
  Log ('                 env key present: ' + [bool]$charmKey + ' (length ' + $charmKey.Length + ', value never logged)')
} elseif (-not (Test-Path $realAuth)) {
  Log 'VERDICT: AUTH-MISSING - no HERMES_CUSTOM_HYPER_CHARM_LAND_API_KEY env var and no ~/.codex/auth.json.'
  Log '   Fix: set the key first -> $env:HERMES_CUSTOM_HYPER_CHARM_LAND_API_KEY=''<your key>''  then re-run.'
  Log '   Or: run `codex login` once (ChatGPT sign-in) and re-run.'
  exit 5
} else {
  Copy-Item $realAuth (Join-Path $probeHome 'auth.json') -Force
  Log 'auth            : using your normal codex login (copied auth.json into isolated home, no secrets logged)'
}
Log ('                 (set $env:HERMES_CUSTOM_HYPER_CHARM_LAND_API_KEY before running to use the custom provider instead)')

# sentinel: trivially-correct .cmd. Marker writes come BEFORE the stdin read so a
# stdin hang can never cause a false "not executed".
$sentinel   = Join-Path $probeRoot 'sentinel.cmd'
$marker     = Join-Path $probeRoot 'hook_marker.txt'
$envDump    = Join-Path $probeRoot 'hook_env.txt'
$stdinDump  = Join-Path $probeRoot 'hook_stdin.txt'
$workMarker = Join-Path $work 'marker_from_hook.txt'
$sentinelBody = @"
@echo off
set > "$envDump" 2>&1
echo RAN_ARGS=%* > "$marker"
echo RAN_CWD=%CD% >> "$marker"
echo RAN_TIME=%DATE% %TIME% >> "$marker"
findstr /r .* > "$stdinDump"
echo marker > "$workMarker"
exit /b 0
"@
Set-Content -Path $sentinel -Value $sentinelBody -Encoding ASCII

# probe config: ONE user-level PostToolUse hook, matcher *, command only (no command_windows)
# plus the custom provider when the Charm Hyper key is present.
$inner = '"' + $sentinel + '"'
$tomlValue = $inner.Replace('\', '\\').Replace('"', '\"')
if ($charmKey) {
  $config = @"
# codex-hook-probe isolated config - custom provider (Charm Hyper / glm-5.2)
model = "glm-5.2"
model_provider = "charm-hyper"

[model_providers.charm-hyper]
name = "Charm Hyper"
base_url = "https://hyper.charm.land/v1"
env_key = "HERMES_CUSTOM_HYPER_CHARM_LAND_API_KEY"
wire_api = "responses"

[[hooks.hooks.PostToolUse]]
matcher = "*"
[[hooks.hooks.PostToolUse.hooks]]
type = "command"
command = "$tomlValue"
"@
} else {
  $config = @"
# codex-hook-probe isolated config - default (ChatGPT login)
[[hooks.hooks.PostToolUse]]
matcher = "*"
[[hooks.hooks.PostToolUse.hooks]]
type = "command"
command = "$tomlValue"
"@
}
Set-Content -Path (Join-Path $probeHome 'config.toml') -Value $config -Encoding ASCII
Log 'config          : isolated config.toml written (ONE PostToolUse -> sentinel.cmd)'
Log ('sentinel        : ' + $sentinel)

Remove-Item $marker, $envDump, $stdinDump, $workMarker -ErrorAction SilentlyContinue

Log '===== 2. EXEC RUN ====='
Log 'running: codex exec --skip-git-repo-check --dangerously-bypass-hook-trust "run echo HOOK-PROBE-OK"'
$merged = Join-Path $probeRoot 'exec_merged_output.txt'
$started = Get-Date
Push-Location $work
& $codex.Source exec --skip-git-repo-check --dangerously-bypass-hook-trust 'run echo HOOK-PROBE-OK' *>&1 | Tee-Object -FilePath $merged
$exit = $LASTEXITCODE
Pop-Location
$elapsed = (Get-Date) - $started
Log ("exit code       : " + $exit + "  (took " + [int]$elapsed.TotalSeconds + "s)")

Log '===== 3. EVIDENCE ====='
$hookLines = @(Select-String -Path $merged -Pattern 'hook:' -AllMatches | ForEach-Object { $_.Line })
if ($hookLines.Count -gt 0) {
  Log ("hook dispatch lines in stderr (" + $hookLines.Count + "):")
  $hookLines | Select-Object -First 6 | ForEach-Object { Log ('  ' + $_) }
} else {
  Log 'hook dispatch lines in stderr: NONE'
}

Log ('marker (abs)    : ' + (Test-Path $marker))
Log ('marker (cwd)    : ' + (Test-Path $workMarker))
if (Test-Path $marker) { Log ('  content: ' + ((Get-Content $marker -Raw) -replace "`r?`n", ' | ')) }
if (Test-Path $envDump) { Log ('env dump        : present (' + (Get-Content $envDump).Count + ' lines)') }
if (Test-Path $stdinDump) {
  Log ('stdin dump      : present, ' + ((Get-Content $stdinDump -Raw).Length) + ' chars')
} else {
  Log 'stdin dump      : absent'
}

$rollout = Get-ChildItem $probeHome -Recurse -Filter *.jsonl -ErrorAction SilentlyContinue |
           Sort-Object LastWriteTime -Descending | Select-Object -First 1
$hookRows = @()
if ($rollout) {
  Log ('rollout         : ' + $rollout.FullName)
  Log ('rollout size    : ' + $rollout.Length + ' bytes')
  $hookRows = @(Select-String -Path $rollout.FullName -Pattern '"hook|additionalContext' -AllMatches | ForEach-Object { $_.Line })
  Log ('hook/additionalContext rows in rollout: ' + $hookRows.Count)
  if ($hookRows.Count -gt 0) { $hookRows | Select-Object -First 3 | ForEach-Object { Log ('  ' + $_.Substring(0, [Math]::Min(300, $_.Length))) } }
} else {
  Log 'rollout         : none found'
}

Log '===== 4. VERDICT ====='
if (Test-Path $marker) {
  Log 'VERDICT: HOOKS-WORKING - the hook command EXECUTED (marker written).'
  if ($rollout -and $hookRows.Count -gt 0) { Log '   additionally: hook/additionalContext rows present in rollout -> context injection works.' }
  else { Log '   note: dispatch+exec yes, but no hook rows in rollout (context injection unproven).' }
} elseif ($exit -ne 0) {
  Log 'VERDICT: STARTUP-FAIL - codex did not complete the run. See exec_merged_output.txt (first lines below).'
  Get-Content $merged -TotalCount 12 | ForEach-Object { Log ('  ' + $_) }
} elseif ($hookLines.Count -gt 0) {
  Log 'VERDICT: DISPATCH-NO-EXEC - codex logged hook dispatch (hook: PostToolUse ... Completed) but the hook command NEVER ran (no marker). Same as the VM. Upstream bug on Windows.'
} else {
  Log 'VERDICT: NO-DISPATCH - no hook lifecycle lines at all. Hook path not reached.'
}

Log ''
Log ('Logs kept in: ' + $probeRoot + ' (exec_merged_output.txt, hook_marker.txt, hook_env.txt, hook_stdin.txt, rollout jsonl)')
Log 'END'
