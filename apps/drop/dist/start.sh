#!/bin/bash
# Start REPLIC Drop. Needs python3 and gcc. Usage: ./start.sh   (PORT=8801 ./start.sh to change the port)
cd "$(dirname "$0")"
command -v python3 >/dev/null || { echo "python3 is required (sudo apt install python3)"; exit 1; }
command -v gcc >/dev/null || { echo "gcc is required (sudo apt install build-essential)"; exit 1; }
URL="http://localhost:${PORT:-8800}"
( sleep 2; (command -v xdg-open >/dev/null && xdg-open "$URL" >/dev/null 2>&1) || (command -v open >/dev/null && open "$URL" >/dev/null 2>&1) || true ) &
echo "Open $URL in your browser (Ctrl-C to stop)"
exec python3 apps/drop/app.py
