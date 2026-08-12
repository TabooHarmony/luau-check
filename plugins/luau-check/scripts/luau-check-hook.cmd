@echo off
rem luau-check hook launcher for Windows (v3.0.0)
rem Harnesses on Windows run hooks via cmd; this wrapper dispatches to the
rem bundled python engine. hooks.json points at luau-check-hook.sh for POSIX;
rem on a Windows-native harness set the hook command to
rem   "${CLAUDE_PLUGIN_ROOT}"/scripts/luau-check-hook.cmd
rem (see README).
setlocal
set "SCRIPT_DIR=%~dp0"

where python >nul 2>nul
if %errorlevel% equ 0 goto :have_python
where py >nul 2>nul
if %errorlevel% equ 0 goto :have_py

rem No python on PATH: fall back to git-bash, which ships with the harnesses.
if exist "C:\Program Files\Git\bin\bash.exe" (
  "C:\Program Files\Git\bin\bash.exe" "%SCRIPT_DIR%luau-check-hook.sh"
  goto :done
)
echo luau-check: python not found on Windows: %errorlevel% 1>&2
exit /b 1

:have_python
python "%SCRIPT_DIR%luau_check_hook.py"
goto :done

:have_py
py -3 "%SCRIPT_DIR%luau_check_hook.py"
goto :done

:done
exit /b %errorlevel%
