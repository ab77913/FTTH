@echo off
title FTTH Data Ingestion Platform â€” Full Pipeline
color 0A

echo ============================================================
echo   FTTH Data Ingestion Platform
echo   8-Agent Address Processing Pipeline
echo ============================================================
echo.
echo   Pipeline stages:
echo     [0] Reverse Geocoder   coords   ^> address (Nominatim/Google)
echo     [1] Agent 1            Smarty + Melissa address validation
echo     [2] Agent 2            Google Geocoding  (refine lat/lon)
echo     [3] Agent 3            Census TIGER parcel + land-use
echo     [4] Agent 4            Street View + Azure Vision (building)
echo     [5] Agent 5            Street View + Azure Vision (detailed)
echo     [6] Agent 6            Final FTTH classification
echo     [7] Agent 7            Neighborhood address discovery
echo ============================================================
echo.

:: â”€â”€ Paths + Python environment â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
set "PROJECT_DIR=%~dp0"
if "%PROJECT_DIR:~-1%"=="\" set "PROJECT_DIR=%PROJECT_DIR:~0,-1%"
if not defined FTTH_VENV_DIR set "FTTH_VENV_DIR=%PROJECT_DIR%\.venv"
set "VENV_DIR=%FTTH_VENV_DIR%"
set "PYTHON=%VENV_DIR%\Scripts\python.exe"
if not exist "%PYTHON%" if exist "%PROJECT_DIR%\.venv311\Scripts\python.exe" (
    set "VENV_DIR=%PROJECT_DIR%\.venv311"
    set "PYTHON=%PROJECT_DIR%\.venv311\Scripts\python.exe"
)

:: â”€â”€ Environment variables for all pipeline agents â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

:: Database (PostgreSQL running locally on port 5432)
set DATABASE_URL=postgresql+psycopg2://ftth:ftth@127.0.0.1:5432/ftth
set REDIS_URL=redis://127.0.0.1:6379/0
set CELERY_BROKER_URL=redis://127.0.0.1:6379/1
set CELERY_RESULT_BACKEND=redis://127.0.0.1:6379/2
set RABBITMQ_URL=amqp://ftth:ftth@127.0.0.1:5672/ftth

:: Pipeline confidence gates. Score >= threshold becomes final; lower scores continue.
set AGENT0_CONFIDENCE_THRESHOLD=90
set AGENT1_CONFIDENCE_THRESHOLD=90
set AGENT2_CONFIDENCE_THRESHOLD=90
set AGENT3_CONFIDENCE_THRESHOLD=90
set AGENT5_CONFIDENCE_THRESHOLD=90
set AGENT6_CONFIDENCE_THRESHOLD=90

:: Non-secret runtime defaults (override via .env)
set USE_MOCK_PROVIDERS=false
set FTTH_AGENT1_PROVIDER_MODE=smarty_only
set FTTH_SSL_VERIFY=0
set FTTH_SKIP_NOMINATIM=1
set FTTH_DEV_RELOAD=0
set FTTH_ENABLE_PADDLE_OCR=1
set FTTH_ENABLE_PADDLEOCR_SCAN=1

:: â”€â”€ Step 0: Create venv and install dependencies â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
echo [0/7] Preparing Python virtual environment...
cd /d "%PROJECT_DIR%"
if not exist "%PYTHON%" (
    echo        Creating venv: %VENV_DIR%
        py -3.13 -m venv "%VENV_DIR%" >nul 2>&1
    if errorlevel 1 py -3.12 -m venv "%VENV_DIR%" >nul 2>&1
    if errorlevel 1 py -3.11 -m venv "%VENV_DIR%" >nul 2>&1
    if errorlevel 1 py -3.10 -m venv "%VENV_DIR%" >nul 2>&1
    if errorlevel 1 python -m venv "%VENV_DIR%"
)
if not exist "%PYTHON%" (
    echo.
    echo   ERROR: Could not create Python virtual environment.
    echo   Install Python 3.10+ and make sure py.exe or python.exe is on PATH.
    pause
    exit /b 1
)
"%PYTHON%" -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)"
if errorlevel 1 (
    echo.
    echo   ERROR: Python 3.10 or newer is required.
    "%PYTHON%" --version
    pause
    exit /b 1
)
"%PYTHON%" --version
echo        Installing/updating core dependencies...
"%PYTHON%" -m pip install --upgrade pip setuptools wheel
if errorlevel 1 goto DEP_FAIL
"%PYTHON%" -m pip install -r requirements.txt
if errorlevel 1 goto DEP_FAIL
if exist requirements-ocr.txt (
    echo        Installing optional PaddleOCR dependencies...
    "%PYTHON%" -m pip install -r requirements-ocr.txt
    if errorlevel 1 (
        echo        WARNING: PaddleOCR optional install failed.
        echo        Agent 5 will continue with Azure Vision OCR.
        set "FTTH_ENABLE_PADDLE_OCR=0"
    ) else (
        set "FTTH_ENABLE_PADDLE_OCR=1"
        set "FTTH_ENABLE_PADDLEOCR_SCAN=1"
    )
)
"%PYTHON%" -c "import importlib.util, sys; sys.exit(0 if importlib.util.find_spec('paddleocr') and importlib.util.find_spec('paddle') else 1)" >nul 2>&1
if errorlevel 1 (
    echo        PaddleOCR runtime not detected; using Azure Vision OCR only.
    set "FTTH_ENABLE_PADDLE_OCR=0"
) else (
    echo        PaddleOCR runtime detected and enabled.
    set "FTTH_ENABLE_PADDLE_OCR=1"
    set "FTTH_ENABLE_PADDLEOCR_SCAN=1"
)
echo        Python environment ready.
echo.
goto DEPS_READY

:DEP_FAIL
echo.
echo   ERROR: Dependency installation failed. Check the pip output above.
pause
exit /b 1

:DEPS_READY

:: Load API keys and secrets from .env (never hardcode secrets in this script)
if exist "%PROJECT_DIR%\.env" (
    echo        Loading configuration from .env...
    "%PYTHON%" "%PROJECT_DIR%\scripts\emit_env_bat.py" > "%PROJECT_DIR%\.env.runtime.bat"
    if exist "%PROJECT_DIR%\.env.runtime.bat" call "%PROJECT_DIR%\.env.runtime.bat"
    del "%PROJECT_DIR%\.env.runtime.bat" >nul 2>&1
) else (
    echo        WARNING: .env not found. Copy .env.example to .env and add your API keys.
)

:: â”€â”€ Step 1: Stop existing services â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
echo [1/7] Stopping any existing services...
powershell -NoProfile -ExecutionPolicy Bypass -File "%PROJECT_DIR%\scripts\stop_ftth_services.ps1" -ProjectDir "%PROJECT_DIR%"
docker rm -f ftth-redis ftth-rabbitmq >nul 2>&1
echo        Done.
echo.

:: Step 2: Start Docker infrastructure (PostgreSQL + Redis + RabbitMQ)
echo [2/7] Starting Docker infrastructure (PostgreSQL + Redis + RabbitMQ)...
cd /d "%PROJECT_DIR%"
docker compose up -d postgres redis rabbitmq
if errorlevel 1 (
    echo   ERROR: Docker Compose failed. Start Docker Desktop and run this script again.
    pause
    exit /b 1
)

echo        Waiting for PostgreSQL to be ready...
:WAIT_POSTGRES
docker compose exec -T postgres pg_isready -U ftth -d ftth >nul 2>&1
if errorlevel 1 (
    timeout /t 2 /nobreak >nul
    goto WAIT_POSTGRES
)
echo        PostgreSQL is ready.

echo        Waiting for Redis to be ready...
:WAIT_REDIS
docker compose exec -T redis redis-cli ping >nul 2>&1
if errorlevel 1 (
    timeout /t 2 /nobreak >nul
    goto WAIT_REDIS
)
echo        Redis is ready.

echo        Waiting for RabbitMQ to be ready...
:WAIT_RABBITMQ
docker compose exec -T rabbitmq rabbitmq-diagnostics -q ping >nul 2>&1
if errorlevel 1 (
    timeout /t 2 /nobreak >nul
    goto WAIT_RABBITMQ
)
echo        RabbitMQ is ready.
echo.

:: â”€â”€ Step 3: Firewall rules â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
echo [3/7] Checking firewall rules...
powershell -NoProfile -Command ^
  "if (-not (Get-NetFirewallRule -DisplayName 'FTTH-PG-5432' -ErrorAction SilentlyContinue)) { New-NetFirewallRule -DisplayName 'FTTH-PG-5432' -Direction Inbound -Protocol TCP -LocalPort 5432 -Action Allow -Profile Any | Out-Null }; if (-not (Get-NetFirewallRule -DisplayName 'FTTH-PG-5433-PROXY' -ErrorAction SilentlyContinue)) { New-NetFirewallRule -DisplayName 'FTTH-PG-5433-PROXY' -Direction Inbound -Protocol TCP -LocalPort 5433 -Action Allow -Profile Any | Out-Null }; if (-not (Get-NetFirewallRule -DisplayName 'FTTH-SSH-22' -ErrorAction SilentlyContinue)) { New-NetFirewallRule -DisplayName 'FTTH-SSH-22' -Direction Inbound -Protocol TCP -LocalPort 22 -Action Allow -Profile Any | Out-Null }"
echo        Done.
echo.

:: â”€â”€ Step 4: SSH server â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
echo [4/7] Ensuring SSH server is running...
powershell -NoProfile -Command ^
  "$s = Get-Service sshd -ErrorAction SilentlyContinue; if ($s -and $s.Status -ne 'Running') { Start-Service sshd }; Set-Service sshd -StartupType Automatic -ErrorAction SilentlyContinue; Write-Host '       sshd:' (Get-Service sshd -ErrorAction SilentlyContinue).Status"
echo.

:: â”€â”€ Step 5: FastAPI server â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
echo [5/7] Starting FastAPI server (port 8000)...
cd /d "%PROJECT_DIR%"
if not exist logs mkdir logs
if not exist logs\services mkdir logs\services
if not exist logs\agents mkdir logs\agents
set PYTHONPATH=%PROJECT_DIR%;%PROJECT_DIR%\backend
start /B /MIN "" "%PYTHON%" api_server.py > logs\services\api_server.log 2>&1
echo        Waiting for API to start...
set /a API_WAIT=0
:WAIT_API
timeout /t 2 /nobreak >nul
powershell -NoProfile -Command ^
  "try { $null = Invoke-WebRequest 'http://127.0.0.1:8000' -UseBasicParsing -TimeoutSec 2 -ErrorAction Stop; exit 0 } catch { exit 1 }" >nul 2>&1
if errorlevel 1 (
    set /a API_WAIT+=1
    if %API_WAIT% GEQ 25 (
        echo.
        echo   WARNING: API not responding after 50 s â€” check logs\services\api_server.log
        echo.
        goto SKIP_API_WAIT
    )
    goto WAIT_API
)
echo        FastAPI is ready on port 8000.
:SKIP_API_WAIT
echo.

:: â”€â”€ Step 6: Celery pipeline worker (auto-restarts on agent code changes) â”€â”€â”€â”€â”€â”€â”€
echo [6/7] Starting Celery pipeline worker (dev auto-reload)...
start /B /MIN "FTTH Celery Worker" "%PYTHON%" "%PROJECT_DIR%\scripts\celery_dev_worker.py"
timeout /t 3 /nobreak >nul
echo        Done.
echo.

:: â”€â”€ Step 7: Nginx â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
echo [7/7] Starting Nginx (port 80)...
start /B "" "C:\nginx\nginx.exe" -p "C:\nginx"
timeout /t 2 /nobreak >nul
echo        Done.
echo.

:: â”€â”€ Verify stack is up â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
echo Verifying services...
powershell -NoProfile -Command ^
  "try { $r = Invoke-RestMethod 'http://127.0.0.1/api/login' -Method POST -Body '{\"username\":\"ftth_team\",\"password\":\"Meridian@2026\"}' -ContentType 'application/json' -UseBasicParsing; Write-Host '       App is UP  --  user:' $r.username } catch { Write-Host '       WARNING: App may still be starting up â€” check logs\api_server.log' }"
echo.

:: â”€â”€ Connection details â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
echo ============================================================
echo   Application is running!
echo.
echo   Web App    : http://172.19.64.7
echo   Local      : http://localhost
echo   Username   : ftth_team  /  admin
echo   Password   : (see README â€” default dev bootstrap user)
echo.
echo   ---- Pipeline API keys ----
echo   Loaded from .env (App Settings also writes .env)
if defined GOOGLE_GEOCODING_API_KEY (
    echo   Google Geo : %GOOGLE_GEOCODING_API_KEY:~0,8%...
) else (
    echo   Google Geo : not set
)
if defined SMARTY_AUTH_ID (
    echo   Smarty ID  : %SMARTY_AUTH_ID:~0,8%...
) else (
    echo   Smarty ID  : not set
)
if defined AZURE_VISION_KEY (
    echo   Azure Vision: configured
) else (
    echo   Azure Vision: not set
)
echo.
echo   ---- Pipeline agents (activated on Process click) ----
echo     A0  Reverse Geocoder   Nominatim + Google fallback
echo     A1  Address Validator  Smarty + Melissa
echo     A2  Geocoding          Google Geocoding API
echo     A3  Parcel             US Census TIGER (free)
echo     A4  Building           Street View + Azure Vision
echo     A5  Street View        Detailed imagery analysis
echo     A6  FTTH Final         Priority: HIGH/MEDIUM/LOW/SKIP
echo     A7  Neighborhood       New polygon addresses absent from Final
echo.
echo   ---- Infrastructure ----
echo   PostgreSQL : localhost:5432  (local Windows service)
echo   Redis      : localhost:6379  (Docker)
echo   RabbitMQ   : localhost:5672  (Docker)
echo.
echo   ---- PostgreSQL Connection ----
echo   Option 1 - Direct (same network):
echo     Host       : 172.19.64.7
echo     Port       : 5432
echo     DB/User/Pass: ftth / ftth / ftth
echo.
echo   Option 2 - Via Nginx Proxy:
echo     Host       : 172.19.64.7
echo     Port       : 5433
echo.
echo   Option 3 - SSH Tunnel:
echo     ssh -L 15432:127.0.0.1:5432 Admin@172.19.64.7
echo     Then: postgresql://ftth:ftth@localhost:15432/ftth
echo ============================================================
echo.
echo Logs:
echo   API Server  : logs\services\api_server.log
echo   Access      : logs\services\access.log
echo   Celery      : logs\services\celery_worker.log
echo   Pipeline    : logs\services\pipeline_tasks.log
echo   Ingestion   : logs\services\ingestion.log
echo   Agents      : logs\agents\*.log
echo.
echo Opening browser...
start http://localhost
echo.
echo Press any key to stop all services and exit...
pause >nul

:: â”€â”€ Shutdown â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
echo.
echo Stopping services...
cd /d "%PROJECT_DIR%"
powershell -NoProfile -ExecutionPolicy Bypass -File "%PROJECT_DIR%\scripts\stop_ftth_services.ps1" -ProjectDir "%PROJECT_DIR%" -StopDocker
echo All services stopped. Goodbye!
timeout /t 2 /nobreak >nul

