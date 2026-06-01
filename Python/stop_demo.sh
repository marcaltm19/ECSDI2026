#!/bin/bash
# =============================================================
# stop_demo.sh  —  Para todos los agentes de la demo
# =============================================================

echo "Parando agentes..."

for pidfile in /tmp/ds.pid /tmp/logistico.pid /tmp/t1.pid /tmp/t2.pid /tmp/t3.pid; do
    if [ -f "$pidfile" ]; then
        kill "$(cat $pidfile)" 2>/dev/null && echo "Parado PID $(cat $pidfile)" || true
        rm "$pidfile"
    fi
done

# Por si acaso, matar cualquier proceso python de agentes
pkill -f "AgenteTransportista.py" 2>/dev/null || true
pkill -f "AgenteLogistico.py" 2>/dev/null || true
pkill -f "DirectoryService.py" 2>/dev/null || true

echo "Sistema parado."
