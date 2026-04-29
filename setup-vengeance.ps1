# setup-vengeance.ps1
# Run from C:\ai\inet-health-engine after extracting the archive
# Sets up the project for Docker Desktop on Windows

Write-Host "=== Internet Health Engine — Windows Setup ===" -ForegroundColor Cyan

# 1. Create data directories (bind mounts need to exist)
$dirs = @("data\timescaledb","data\neo4j","data\elasticsearch","data\redis","data\minio")
foreach ($d in $dirs) {
    New-Item -ItemType Directory -Force -Path $d | Out-Null
    Write-Host "  Created: $d"
}

# 2. Copy the Windows-tuned docker-compose
Copy-Item "docker-compose-vengeance.yml" "docker-compose.yml" -Force
Write-Host "  Installed: docker-compose.yml (Windows/x86_64 tuned)"

# 3. Validate compose file
Write-Host "`nValidating docker-compose..." -ForegroundColor Yellow
docker compose config --quiet
if ($LASTEXITCODE -ne 0) {
    Write-Host "ERROR: docker-compose.yml validation failed" -ForegroundColor Red
    exit 1
}
Write-Host "  docker-compose.yml is valid" -ForegroundColor Green

# 4. Pull all base images in parallel
Write-Host "`nPulling base images..." -ForegroundColor Yellow
docker compose pull timescaledb neo4j elasticsearch redis minio

# 5. Build collector + api + frontend images
Write-Host "`nBuilding application images..." -ForegroundColor Yellow
docker compose build --parallel

Write-Host "`n=== Setup complete! ===" -ForegroundColor Green
Write-Host "Start with:  docker compose up -d" -ForegroundColor Cyan
Write-Host "Frontend:    http://localhost:3000"
Write-Host "API:         http://localhost:8000"
Write-Host "TimescaleDB: localhost:5432"
Write-Host "Neo4j:       http://localhost:7474"
Write-Host "Kibana:      http://localhost:9200"
