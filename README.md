# ECSDI 2025/2026

Práctica de Enginyeria del Coneixement i Sistemes Distribuïts Intel·ligents,
cuatrimestre de primavera 2025/2026.

## Estructura del proyecto


### Código de la práctica (segunda entrega)
- `Python/AgenteComprador.py`      — búsqueda y gestión del catálogo de productos
- `Python/AgenteGestorPedidos.py`  — procesamiento de pedidos y generación de facturas
- `Python/AgenteLogistico.py`      — gestión de envíos y negociación con transportistas
- `Python/AgenteTransportista.py`  — agente transportista (lanzar varias instancias)
- `Python/AgenteUsuario.py`        — cliente de prueba del sistema
- `Python/DirectoryService.py`     — servicio de directorio con soporte multi-agente
- `Python/ontologia.py`            — definición del namespace de la ontología
- `Python/data/`                   — datos de productos, centros logísticos y resultados
- `Ontologias/ontoECSDI.owx`       — ontología del proyecto


### Material de laboratorio (proporcionado por el profesor)
- `AgentUtil/`       — utilidades para la comunicación entre agentes
- `Examples/`        — plantillas y ejemplos de agentes
- `Ontologias/`      — ontologías de referencia (excepto `ontoECSDI.owx`)
- `Python/*.ipynb`   — notebooks introductorios a Python


## Requisitos
pip install -r requirements.txt

## Orden de arranque
1. python3 Python/DirectoryService.py --port 9000
2. python3 Python/AgenteTransportista.py --nombre TransRapid --port 9010 --dport 9000
3. python3 Python/AgenteTransportista.py --nombre ExpressGo --port 9011 --dport 9000
4. python3 Python/AgenteComprador.py --port 9001 --dport 9000
5. python3 Python/AgenteGestorPedidos.py --port 9002 --dport 9000
6. python3 Python/AgenteLogistico.py --port 9003 --dport 9000
7. python3 Python/AgenteUsuario.py --dport 9000

## Resultados
- `Python/data/facturas.json` → facturas generadas por cada compra
- `Python/data/envios.json`   → envíos asignados a transportista
- `Python/data/pedidos.json`  → cola temporal de pedidos (vacío tras procesarse)