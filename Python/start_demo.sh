#!/bin/bash
# =============================================================
# start_demo.sh  —  Arranca todos los agentes para la demo
# Uso: cd Python && bash start_demo.sh
# Para parar todo: bash stop_demo.sh
# =============================================================

set -e
cd "$(dirname "$0")"

export PYTHONPATH="$(pwd)/..:$PYTHONPATH"

echo "====================================================="
echo " INICIANDO SISTEMA MULTIAGENTE ECSDI 2026"
echo "====================================================="

# 1. Limpiar pedidos y envios previos para demo limpia
mkdir -p data
echo '[]' > data/pedidos.json
echo '[]' > data/envios.json
echo "[OK] Datos reiniciados"

# 2. Directory Service (DS)
echo "[1/5] Arrancando DirectoryService en puerto 9000..."
python DirectoryService.py --port 9000 &
echo $! > /tmp/ds.pid
sleep 2

# 3. Agente Logistico
echo "[2/5] Arrancando AgenteLogistico en puerto 9003..."
python AgenteLogistico.py --port 9003 --dport 9000 &
echo $! > /tmp/logistico.pid
sleep 1

# 4. Tres transportistas propios con puertos distintos
echo "[3/5] Arrancando AgenteTransportista: RapidExpress en puerto 9010..."
python AgenteTransportista.py --port 9010 --dport 9000 --nombre RapidExpress &
echo $! > /tmp/t1.pid
sleep 0.5

echo "[4/5] Arrancando AgenteTransportista: EcoEnvios en puerto 9011..."
python AgenteTransportista.py --port 9011 --dport 9000 --nombre EcoEnvios &
echo $! > /tmp/t2.pid
sleep 0.5

echo "[5/5] Arrancando AgenteTransportista: MensajeriaPlus en puerto 9012..."
python AgenteTransportista.py --port 9012 --dport 9000 --nombre MensajeriaPlus &
echo $! > /tmp/t3.pid
sleep 0.5

echo ""
echo "====================================================="
echo " Sistema arrancado correctamente"
echo " DS:           http://localhost:9000/Register"
echo " Logistico:    http://localhost:9003/comm"
echo " Transportista1 (RapidExpress):  puerto 9010"
echo " Transportista2 (EcoEnvios):     puerto 9011"
echo " Transportista3 (MensajeriaPlus):puerto 9012"
echo ""
echo " Para ver el registro del DS: http://localhost:9000/info"
echo " Para parar todo: bash stop_demo.sh"
echo "====================================================="
