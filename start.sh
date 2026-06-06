#!/bin/bash
set -e

echo "=== XAUUSD 3-Candle Bot Starting ==="
echo "Starting signal worker in background..."
python -u worker.py &
WORKER_PID=$!
echo "Worker started (PID $WORKER_PID)"

echo "Starting Streamlit dashboard..."
exec streamlit run app.py \
    --server.port "${PORT:-8080}" \
    --server.address 0.0.0.0 \
    --server.headless true
