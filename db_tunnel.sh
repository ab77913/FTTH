#!/bin/bash
# ============================================================
# FTTH Server — PostgreSQL SSH Tunnel
# Forwards local 5432 -> server DB at 172.19.64.7:5432
# Usage: bash db_tunnel.sh
# ============================================================

SERVER_IP="172.19.64.7"
SERVER_USER="Admin"
LOCAL_PORT=5432

echo ""
echo " FTTH PostgreSQL Tunnel"
echo " Forwarding localhost:$LOCAL_PORT -> $SERVER_IP:5432"
echo " Press Ctrl+C to stop."
echo ""
echo " Once connected, connect PostgreSQL to:"
echo "   Host: localhost | Port: $LOCAL_PORT | DB: ftth"
echo "   App user:   ftth / ftth"
echo "   Admin user: postgres / postgres"
echo ""

ssh -N \
  -L ${LOCAL_PORT}:127.0.0.1:5432 \
  -o ServerAliveInterval=60 \
  -o ServerAliveCountMax=5 \
  -o ExitOnForwardFailure=yes \
  -o StrictHostKeyChecking=no \
  ${SERVER_USER}@${SERVER_IP} -p 22
