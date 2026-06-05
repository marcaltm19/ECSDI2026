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

# 1. Limpiar datos previos
mkdir -p data
echo '{}' > data/historial_compras.json
echo '{}' > data/historial_busquedas.json
echo '[]' > data/informacion_pago.json
# Limpiar ficheros por-centro
for centro in centro_madrid centro_barcelona centro_valencia centro_sevilla; do
    echo '[]' > "data/listado_pedidos_${centro}.json"
    echo '[]' > "data/listado_envios_${centro}.json"
done
echo "[OK] Datos reiniciados"

# 2. Directory Service
echo "[1/16] Arrancando DirectoryService en puerto 9000..."
"$PYTHON_BIN" DirectoryService.py --port 9000 --open &
echo $! > /tmp/ds.pid
sleep 2

# 3. Agente Comprador
echo "[2/16] Arrancando AgenteComprador en puerto 9001..."
"$PYTHON_BIN" AgenteComprador.py --port 9001 --dport 9000 --open &
echo $! > /tmp/comprador.pid
sleep 1

# 4. Agente GestorPedidos
echo "[3/16] Arrancando AgenteGestorPedidos en puerto 9002..."
"$PYTHON_BIN" AgenteGestorPedidos.py --port 9002 --dport 9000 --open &
echo $! > /tmp/gestor.pid
sleep 1

# 4b. Agente GestorPagos
echo "[4/16] Arrancando AgenteGestorPagos en puerto 9014..."
"$PYTHON_BIN" AgenteGestorPagos.py --port 9014 --dport 9000 --open &
echo $! > /tmp/gestorpagos.pid
sleep 1

# 5. Agente Experiencia
echo "[5/16] Arrancando AgenteExperiencia en puerto 9005..."
"$PYTHON_BIN" AgenteExperiencia.py --port 9005 --dport 9000 --open &
echo $! > /tmp/experiencia.pid
sleep 1

# 6. Cuatro Agentes Logísticos (uno por centro)
echo "[6/16] Arrancando AgenteLogistico: Centro Madrid (9003)..."
"$PYTHON_BIN" AgenteLogistico.py --port 9003 --dport 9000 --centro "Centro Madrid" --open &
echo $! > /tmp/logistico_madrid.pid
sleep 0.8

echo "[7/16] Arrancando AgenteLogistico: Centro Barcelona (9004)..."
"$PYTHON_BIN" AgenteLogistico.py --port 9004 --dport 9000 --centro "Centro Barcelona" --open &
echo $! > /tmp/logistico_barcelona.pid
sleep 0.8

echo "[8/16] Arrancando AgenteLogistico: Centro Valencia (9008)..."
"$PYTHON_BIN" AgenteLogistico.py --port 9008 --dport 9000 --centro "Centro Valencia" --open &
echo $! > /tmp/logistico_valencia.pid
sleep 0.8

echo "[9/16] Arrancando AgenteLogistico: Centro Sevilla (9009)..."
"$PYTHON_BIN" AgenteLogistico.py --port 9009 --dport 9000 --centro "Centro Sevilla" --open &
echo $! > /tmp/logistico_sevilla.pid
sleep 0.8

# 7. Transportistas (uno por ciudad de cobertura + uno extra)
echo "[10/16] Arrancando AgenteTransportista: RapidExpress (Madrid, 9010)..."
"$PYTHON_BIN" AgenteTransportista.py --port 9010 --dport 9000 --nombre RapidExpress --ciudad Madrid --open &
echo $! > /tmp/t1.pid
sleep 0.5

echo "[11/16] Arrancando AgenteTransportista: EcoEnvios (Barcelona, 9011)..."
"$PYTHON_BIN" AgenteTransportista.py --port 9011 --dport 9000 --nombre EcoEnvios --ciudad Barcelona --open &
echo $! > /tmp/t2.pid
sleep 0.5

echo "[12/16] Arrancando AgenteTransportista: MensajeriaPlus (Valencia, 9012)..."
"$PYTHON_BIN" AgenteTransportista.py --port 9012 --dport 9000 --nombre MensajeriaPlus --ciudad Valencia --open &
echo $! > /tmp/t3.pid
sleep 0.5

echo "[13/16] Arrancando AgenteTransportista: SurExpress (Sevilla, 9013)..."
"$PYTHON_BIN" AgenteTransportista.py --port 9013 --dport 9000 --nombre SurExpress --ciudad Sevilla --open &
echo $! > /tmp/t4.pid
sleep 0.5

# 8. Agente de Devoluciones
echo "[14/16] Arrancando AgenteDevolucion en puerto 9006..."
"$PYTHON_BIN" AgenteDevolucion.py --port 9006 --dport 9000 --open &
echo $! > /tmp/devolucion.pid
sleep 0.5

# 9. Agente Vendedor Externo
echo "[15/16] Arrancando AgenteVendedorExterno en puerto 9007..."
"$PYTHON_BIN" AgenteVendedorExterno.py --port 9007 --dport 9000 --nombre VendedorExterno1 --open &
echo $! > /tmp/vendedor.pid
sleep 0.5

# 10. Agente Usuario
echo "[16/16] Arrancando AgenteUsuario en puerto 9020..."
"$PYTHON_BIN" AgenteUsuario.py --port 9020 --dport 9000 --open &
echo $! > /tmp/usuario.pid
sleep 1

echo ""
echo "====================================================="
echo " Sistema arrancado correctamente"
echo " DS:                      http://localhost:9000/Register"
echo " Comprador:               http://localhost:9001/comm"
echo " Gestor:                  http://localhost:9002/comm"
echo " GestorPagos:             http://localhost:9014/comm"
echo " Experiencia:             http://localhost:9005/comm"
echo " Logístico Madrid:        http://localhost:9003/comm"
echo " Logístico Barcelona:     http://localhost:9004/comm"
echo " Logístico Valencia:      http://localhost:9008/comm"
echo " Logístico Sevilla:       http://localhost:9009/comm"
echo " Devolucion:              http://localhost:9006/comm"
echo " Vendedor Externo:        http://localhost:9007/comm"
echo " Usuario (UI):            http://localhost:9020/"
echo " RapidExpress (Madrid):   puerto 9010"
echo " EcoEnvios (Barcelona):   puerto 9011"
echo " MensajeriaPlus (Valencia): puerto 9012"
echo " SurExpress (Sevilla):    puerto 9013"
echo ""
echo " Para parar todo: bash stop_demo.sh"
echo "====================================================="
