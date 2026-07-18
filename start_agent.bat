@echo off
setlocal EnableExtensions EnableDelayedExpansion
title DevAgent Local
cd /d "%~dp0"
set "AGENT_WORKSPACE=%CD%"

echo ========================================
echo   DevAgent Local - launcher
echo ========================================
echo.

echo [1/5] Checking Docker Desktop...
docker info >nul 2>&1
if errorlevel 1 (
    echo ERROR: Docker Desktop is not running.
    echo Start Docker Desktop and run this file again.
    pause
    exit /b 1
)
echo [OK] Docker is ready.

echo [2/5] Checking Ollama...
curl.exe -fsS http://localhost:11434/api/tags >nul 2>&1
if errorlevel 1 (
    echo ERROR: Ollama is not responding at http://localhost:11434.
    echo Start Ollama and run this file again.
    pause
    exit /b 1
)
echo [OK] Ollama is ready.

echo [3/5] Building the DevAgent image...
docker build -t devagent-local "%AGENT_WORKSPACE%"
if errorlevel 1 (
    echo ERROR: Docker image build failed.
    pause
    exit /b 1
)
echo [OK] Image is ready.

echo [4/5] Starting the agent container...
docker rm -f ai-agent >nul 2>&1
docker run -d --name ai-agent --restart unless-stopped -p 127.0.0.1:5000:5000 -v "%AGENT_WORKSPACE%:/workspace" devagent-local >nul
if errorlevel 1 (
    echo ERROR: Agent container failed to start.
    pause
    exit /b 1
)

echo [5/5] Waiting for the web interface...
set "AGENT_READY=0"
for /L %%I in (1,1,30) do (
    curl.exe -fsS http://localhost:5000/api/health >nul 2>&1
    if !errorlevel! equ 0 (
        set "AGENT_READY=1"
        goto :ready
    )
    timeout /t 1 /nobreak >nul
)

:ready
if "%AGENT_READY%"=="0" (
    echo ERROR: The container started, but the web interface is unavailable.
    echo.
    docker logs --tail 80 ai-agent
    echo.
    pause
    exit /b 1
)

echo.
echo ========================================
echo   DevAgent is running
echo   http://localhost:5000
echo ========================================
echo.
start "" "http://localhost:5000"
echo You can close this window. The agent will keep running in Docker.
echo To stop it later, run: docker stop ai-agent
echo.
pause
endlocal

