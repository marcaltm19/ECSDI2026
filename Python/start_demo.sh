#!/bin/bash
# =============================================================
# start_demo.sh  —  Arranca todos los agentes para la demo
# Uso: cd Python && bash start_demo.sh
# Para parar todo: bash stop_demo.sh
# =============================================================

set -e
cd "$(dirname "$0")"

export PYTHONPATH="$(pwd)/..:$PYTHONPATH"
export ECSDI_PUBLIC_HOST="${ECSDI_PUBLIC_HOST:-localhost}"
export ECSDI_DHOST="${ECSDI_DHOST:-localhost}"

if [ -x "../venv/bin/python" ]; then
	PYTHON_BIN="../venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
	PYTHON_BIN="python3"
elif command -v python >/dev/null 2>&1; then
	PYTHON_BIN="python"
else
	echo "[ERROR] No se encontro un interprete Python valido"
	exit 1
fi

echo "====================================================="
echo " INICIANDO SISTEMA MULTIAGENTE ECSDI 2026"
echo "====================================================="

# 1. Limpiar pedidos y envios previos para demo limpia
mkdir -p data
echo '[]' > data/pedidos.json
echo '[]' > data/envios.json
echo '[]' > data/facturas.json
echo '{}' > data/valoraciones.json
echo '{}' > data/historial_compras.json
echo '{}' > data/historial_busquedas.json
echo "[OK] Datos reiniciados"

# 2. Directory Service (DS)
echo "[1/8] Arrancando DirectoryService en puerto 9000..."
"$PYTHON_BIN" DirectoryService.py --port 9000 --open &
echo $! > /tmp/ds.pid
sleep 2

# 3. Agente Comprador
echo "[2/8] Arrancando AgenteComprador en puerto 9001..."
"$PYTHON_BIN" AgenteComprador.py --port 9001 --dport 9000 --open &
echo $! > /tmp/comprador.pid
sleep 1

# 4. Agente GestorPedidos
echo "[3/8] Arrancando AgenteGestorPedidos en puerto 9002..."
"$PYTHON_BIN" AgenteGestorPedidos.py --port 9002 --dport 9000 --open &
echo $! > /tmp/gestor.pid
sleep 1

# 5. Agente Experiencia
echo "[4/8] Arrancando AgenteExperiencia en puerto 9005..."
"$PYTHON_BIN" AgenteExperiencia.py --port 9005 --dport 9000 --open &
echo $! > /tmp/experiencia.pid
sleep 1

# 6. Agente Logistico
echo "[5/8] Arrancando AgenteLogistico en puerto 9003..."
"$PYTHON_BIN" AgenteLogistico.py --port 9003 --dport 9000 --open &
echo $! > /tmp/logistico.pid
sleep 1

# 7. Tres transportistas propios con puertos distintos y ciudades asignadas
echo "[6/11] Arrancando AgenteTransportista: RapidExpress en puerto 9010 (Madrid)..."
"$PYTHON_BIN" AgenteTransportista.py --port 9010 --dport 9000 --nombre RapidExpress --ciudad Madrid --open &
echo $! > /tmp/t1.pid
sleep 0.5

echo "[7/11] Arrancando AgenteTransportista: EcoEnvios en puerto 9011 (Barcelona)..."
"$PYTHON_BIN" AgenteTransportista.py --port 9011 --dport 9000 --nombre EcoEnvios --ciudad Barcelona --open &
echo $! > /tmp/t2.pid
sleep 0.5

echo "[8/11] Arrancando AgenteTransportista: MensajeriaPlus en puerto 9012 (Valencia)..."
"$PYTHON_BIN" AgenteTransportista.py --port 9012 --dport 9000 --nombre MensajeriaPlus --ciudad Valencia --open &
echo $! > /tmp/t3.pid
sleep 0.5

# 8. Agente de Devoluciones
echo "[9/11] Arrancando AgenteDevolucion en puerto 9006..."
"$PYTHON_BIN" AgenteDevolucion.py --port 9006 --dport 9000 --open &
echo $! > /tmp/devolucion.pid
sleep 0.5

# 9. Agente Vendedor Externo
echo "[10/11] Arrancando AgenteVendedorExterno en puerto 9007..."
"$PYTHON_BIN" AgenteVendedorExterno.py --port 9007 --dport 9000 --nombre VendedorExterno1 --open &
echo $! > /tmp/vendedor.pid
sleep 0.5

# 10. Agente Usuario (Interfaz Web)
echo "[11/11] Arrancando AgenteUsuario en puerto 9020..."
"$PYTHON_BIN" AgenteUsuario.py --port 9020 --dport 9000 --open &
echo $! > /tmp/usuario.pid
sleep 1

echo ""
echo "====================================================="
echo " Sistema arrancado correctamente"
echo " DS:             http://localhost:9000/Register"
echo " Comprador:      http://localhost:9001/comm"
echo " Gestor:         http://localhost:9002/comm"
echo " Experiencia:    http://localhost:9005/comm"
echo " Logistico:      http://localhost:9003/comm"
echo " Devolucion:     http://localhost:9006/comm"
echo " Vendedor Ext1:  http://localhost:9007/comm"
echo " Usuario (UI):   http://localhost:9020/"
echo " Transportista1 (RapidExpress - Madrid):     puerto 9010"
echo " Transportista2 (EcoEnvios - Barcelona):     puerto 9011"
echo " Transportista3 (MensajeriaPlus - Valencia): puerto 9012"
echo ""
echo " Ver agentes registrados: http://localhost:9000/info"
echo " Para parar todo:         bash stop_demo.sh"
echo "====================================================="
