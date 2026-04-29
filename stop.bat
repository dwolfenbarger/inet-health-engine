@echo off
title Internet Health Engine — Stop
cd /d C:\ai\inet-health-engine
echo Stopping all containers...
docker compose down
echo Done.
pause
