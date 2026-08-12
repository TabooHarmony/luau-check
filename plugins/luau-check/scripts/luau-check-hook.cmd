@echo off
rem luau-check hook launcher for Windows (v3.0.0)
rem Harnesses on Windows run hooks via cmd; this wrapper dispatches to the
rem bundled python engine. hooks.json points at luau-check-hook (extensionless;
rem cmd resolves luau-check-hook.cmd via PATHEXT, POSIX runs the bash shebang).
setlocal
set "SCRIPT_DIR=%~dp0"

rem Prefer known real installs (harness hook envs may only expose the broken
rem WindowsApps python alias on PATH).
if exist "C:\Program Files\Python312\python.exe" (
  "C:\Program Files\Python312\python.exe" "%SCRIPT_DIR%luau_check_hook.py"
  goto :done
)
if exist "C:\Python312\python.exe" (
  "C:\Python312\python.exe" "%SCRIPT_DIR%luau_check_hook.py"
  goto :done
)

where python >nul 2>nul
if %errorlevel% equ 0 goto :have_python
where py >nul 2>nul
if %errorlevel% equ 0 goto :have_py

rem No python on PATH: fall back to git-bash, which ships with the harnesses.
if exist "C:\Program Files\Git\bin\bash.exe" (
  "C:\Program Files\Git\bin\bash.exe" "%SCRIPT_DIR%luau-check-hook.sh"
  goto :done
)
echo luau-check: python not found on Windows 1>&2
exit /b 1

:have_python
python "%SCRIPT_DIR%luau_check_hook.py"
goto :done

:have_py
py -3 "%SCRIPT_DIR%luau_check_hook.py"
goto :done

:done
exit /b %errorlevel%
