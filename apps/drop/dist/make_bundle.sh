#!/bin/bash
# Build poise-drop.zip: the app, the C sources it compiles, the neural weights, and start scripts.
set -e; OUT=$(realpath -m "${1:-poise-drop.zip}"); cd "$(dirname "$0")/../../.."; T=$(mktemp -d); D=$T/poise-drop
mkdir -p $D/apps/drop $D/src $D/results
cp apps/drop/app.py apps/drop/index.html $D/apps/drop/
cp src/server.c src/common.c src/common.h src/smart_client.c src/poise_sender.c src/poise.h src/rl_cc.h src/deep_cc.h src/smart_cc.h src/neural_weights.h src/impair.c $D/src/
cp results/dqn_chat.txt $D/results/; cp LICENSE $D/ 2>/dev/null || true
cp apps/drop/dist/start.sh apps/drop/dist/Dockerfile apps/drop/dist/README.txt $D/; chmod +x $D/start.sh
(cd $T && rm -f "$OUT" && zip -qr "$OUT" poise-drop); rm -rf $T; echo "wrote $OUT"
