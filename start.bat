@echo off
title Internet Health Engine — Startup
cd /d C:\ai\inet-health-engine

echo.
echo  ============================================
echo   Internet Health Engine — Starting Up
echo  ============================================
echo.

REM Start storage layer first
echo [1/3] Starting storage services...
docker compose up -d timescaledb neo4j elasticsearch redis minio
if errorlevel 1 goto :error

REM Wait for health checks
echo [2/3] Waiting for storage to be ready...
:wait_loop
ping -n 6 127.0.0.1 >nul
for /f %%c in ('powershell -NoProfile -Command "(docker inspect --format '{{.State.Health.Status}}' inet-health-engine-timescaledb-1 inet-health-engine-neo4j-1 inet-health-engine-elasticsearch-1 inet-health-engine-redis-1 inet-health-engine-minio-1 2>$null | Where-Object { $_ -eq 'healthy' } | Measure-Object).Count" 2^>nul') do set COUNT=%%c
if not defined COUNT set COUNT=0
if %COUNT% LSS 5 (
    echo    Waiting... (%COUNT%/5 storage containers healthy)
    goto :wait_loop
)

REM Start everything else
echo [3/3] Starting collectors, API, and frontend...
docker compose up -d
if errorlevel 1 goto :error

echo.
echo  ============================================
echo   All services started successfully!
echo.
echo   Frontend:  http://localhost:3000
echo   API:       http://localhost:8000/health
echo   Neo4j:     http://localhost:7474
echo  ============================================
echo.
echo  Press any key to open the NOC UI in your browser...
pause >nul
start http://localhost:3000
goto :end

:error
echo.
echo  ERROR: Startup failed. Check docker compose logs.
echo  Run: docker compose logs --tail=20
pause

:end
