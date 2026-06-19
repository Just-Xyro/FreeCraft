#!/bin/bash
echo "Starting FreeCraft..."

pkill -9 -f "start-api.py" 2>/dev/null
pkill -9 -f "uvicorn" 2>/dev/null
pkill -9 -f "node index.js" 2>/dev/null

for port in 8000 5000; do
    pid=$(lsof -ti :$port 2>/dev/null)
    [ -n "$pid" ] && kill -9 $pid 2>/dev/null
done

sleep 2

echo "FreeCraft — Free Minecraft Marketplace"

python start-api.py &
API_PID=$!
sleep 2

node index.js &
NODE_PID=$!

echo "Frontend running at http://0.0.0.0:5000"

cleanup() {
    kill -9 $API_PID $NODE_PID 2>/dev/null
    exit
}
trap cleanup EXIT INT TERM
wait
