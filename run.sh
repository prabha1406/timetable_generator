#!/bin/sh
# Start the app:  ./run.sh   then open http://localhost:8000
cd "$(dirname "$0")/backend" && exec python -m uvicorn app.main:app --port 8000
