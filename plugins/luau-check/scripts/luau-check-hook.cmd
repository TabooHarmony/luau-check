@echo off
rem luau-check hook launcher for Windows (v3.0.0)
rem Harnesses on Windows run hooks via cmd; this wrapper dispatches to the
rem bundled python engine (fell back to git-bash if python is not on PATH).
rem Note: hooks.json points at luau-check-hook.sh for POSIX; on Windows
rem change the hook command to scripts/luau-check-hook.cmd (see README).
setlocal
set "SCRIPT_DIR=%~dp0"
where python >nul 2>nul
if %errorlevel% equ 0 (
  python "%SCRIPT_DIR%luau_check_hook.py"
  exit /b %errorlevel%
)
where py >nul 2>nul
if %errorlevel% equ 0 (
  py -3 "%SCRIPT_DIR%luau_check_hook.py"
  exit /b %errorlevel%
)
"C:\Program Files\Git\bin\bash.exe" "%SCRIPT_DIR%luau-check-hook.sh"
exit /b %errorlevel%
