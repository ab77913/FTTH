@echo off
:: ============================================================
:: FTTH Server — PostgreSQL SSH Tunnel
:: Forwards local port 5432 -> server DB (172.19.64.7:5432)
:: ============================================================
:: Usage: Double-click or run from cmd.
:: Requires: OpenSSH on your machine  (comes with Windows 10+)
:: Auth:     First-time needs Windows Admin password.
::           Set up key auth (see README below) to skip password.
:: ============================================================

set SERVER_IP=172.19.64.7
set SERVER_USER=Admin
set SERVER_SSH_PORT=22
set LOCAL_DB_PORT=5432
set REMOTE_DB_HOST=127.0.0.1
set REMOTE_DB_PORT=5432

echo.
echo  FTTH PostgreSQL Tunnel
echo  Forwarding localhost:%LOCAL_DB_PORT% -^> %SERVER_IP%:%REMOTE_DB_PORT%
echo  Press Ctrl+C to disconnect.
echo.
echo  Once connected, use these credentials in pgAdmin / DBeaver / psql:
echo    Host:     localhost
echo    Port:     %LOCAL_DB_PORT%
echo    Database: ftth
echo    Username: ftth         Password: ftth
echo    Username: postgres     Password: postgres  (admin only)
echo.

ssh -N -L %LOCAL_DB_PORT%:%REMOTE_DB_HOST%:%REMOTE_DB_PORT% ^
    -o ServerAliveInterval=60 ^
    -o ServerAliveCountMax=5 ^
    -o ExitOnForwardFailure=yes ^
    -o StrictHostKeyChecking=no ^
    %SERVER_USER%@%SERVER_IP% -p %SERVER_SSH_PORT%

pause
