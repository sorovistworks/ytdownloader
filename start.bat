@echo off
rem Starts YT Downloader and opens it in your browser.
cd /d "%~dp0"
where uv >nul 2>nul || (echo uv is not installed. Install it with:  winget install astral-sh.uv & pause & exit /b 1)
where ffmpeg >nul 2>nul || (echo ffmpeg is not installed. Install it with:  winget install Gyan.FFmpeg & pause & exit /b 1)
where node >nul 2>nul || where deno >nul 2>nul || (echo Node.js is needed for full YouTube quality. Install it with:  winget install OpenJS.NodeJS.LTS & pause & exit /b 1)
uv run app.py
pause
