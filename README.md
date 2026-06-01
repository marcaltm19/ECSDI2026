# ECSDI 2025/2026

Práctica de Enginyeria del Coneixement i Sistemes Distribuïts Intel·ligents,
cuatrimestre de primavera 2025/2026.

---

## Estructura del proyecto

### Código de la práctica (segunda entrega)
- `Python/AgenteComprador.py`       — búsqueda y gestión del catálogo de productos
- `Python/AgenteGestorPedidos.py`   — procesamiento de pedidos y generación de facturas
- `Python/AgenteLogistico.py`       — gestión de envíos y negociación con transportistas (Contract Net)
- `Python/AgenteTransportista.py`   — agente transportista (lanzar varias instancias con `--nombre` y `--port`)
- `Python/AgenteUsuario.py`         — cliente de prueba del sistema
- `Python/DirectoryService.py`      — servicio de directorio con soporte multi-agente
- `Python/ontologia.py`             — definición del namespace de la ontología (`ECSNS`)
- `Python/data/`                    — datos de productos, centros logísticos y resultados en tiempo de ejecución
- `Ontologias/ontoECSDI.owx`        — ontología del proyecto (Protégé)

### Scripts de demo y pruebas
- `Python/start_demo.sh`   — arranca todo el sistema (DS + Logístico + 3 transportistas) con un solo comando
- `Python/stop_demo.sh`    — para todos los procesos del sistema
- `Python/jp_cliente.py`   — script para ejecutar los juegos de prueba JP1, JP2, JP3 y JP5

### Documentación
- `JuegosDePrueba.md`      — descripción completa de los 5 juegos de prueba (propósito, entrada, salida, comportamiento)

### Material de laboratorio (proporcionado por el profesor)
- `AgentUtil/`       — utilidades para la comunicación entre agentes
- `Examples/`        — plantillas y ejemplos de agentes
- `Ontologias/`      — ontologías de referencia (excepto `ontoECSDI.owx`)
- `Python/*.ipynb`   — notebooks introductorios a Python

---

## Requisitos

```bash
pip install -r requirements.txt
```

---

## Arranque rápido para la demo

```bash
cd Python
bash start_demo.sh
```

Esto arranca automáticamente:
1. `DirectoryService` en el puerto 9000
2. `AgenteLogistico` en el puerto 9003
3. `AgenteTransportista` (RapidExpress) en el puerto 9010
4. `AgenteTransportista` (EcoEnvios) en el puerto 9011
5. `AgenteTransportista` (MensajeriaPlus) en el puerto 9012

Para parar todo:
```bash
bash stop_demo.sh
```

---

## Orden de arranque manual

Si prefieres arrancar los agentes por separado:

```bash
python3 Python/DirectoryService.py --port 9000
python3 Python/AgenteTransportista.py --nombre RapidExpress   --port 9010 --dport 9000
python3 Python/AgenteTransportista.py --nombre EcoEnvios      --port 9011 --dport 9000
python3 Python/AgenteTransportista.py --nombre MensajeriaPlus --port 9012 --dport 9000
python3 Python/AgenteComprador.py      --port 9001 --dport 9000
python3 Python/AgenteGestorPedidos.py  --port 9002 --dport 9000
python3 Python/AgenteLogistico.py      --port 9003 --dport 9000
python3 Python/AgenteUsuario.py        --dport 9000
```

---

## Juegos de prueba

Ver descripción completa en [`JuegosDePrueba.md`](JuegosDePrueba.md).

Con el sistema arrancado (`start_demo.sh`), ejecutar:

```bash
# JP1 — El transportista más barato gana (prioridad normal)
python3 Python/jp_cliente.py --jp 1

# JP2 — Prioridad urgente: gana el más rápido, no el más barato
python3 Python/jp_cliente.py --jp 2

# JP3 — Sin transportistas disponibles (arrancar sin AgenteTransportista)
python3 Python/jp_cliente.py --jp 3

# JP5 — Tres pedidos simultáneos con distintas prioridades
python3 Python/jp_cliente.py --jp 5
```

Esperar ~20 segundos tras cada JP para ver el resultado en los logs y en `Python/data/envios.json`.

---

## Ejecución distribuida (JP4 — agente externo)

Para la demostración con el agente del otro grupo, el otro equipo debe ejecutar su `AgenteTransportista` apuntando al DirectoryService de este grupo:

```bash
# En el PC del otro grupo:
python3 AgenteTransportista.py --port 9013 --dhost <IP_PC1> --dport 9000 --nombre TransportistaExterno
```

Asegurarse de que el puerto 9000 y los puertos de los transportistas (9010–9013) están accesibles en la red local:
```bash
sudo ufw allow 9000/tcp
sudo ufw allow 9010:9013/tcp
```

La ontología compartida usa el namespace:
```
http://www.semanticweb.org/ecsdi/ontologies/2026/e-shop#
```
Predicados acordados: `tienePrecio`, `tieneDestino`, `tienePrioridad`, `tieneTransportista`, `tieneFechaEntrega`.

---

## Resultados generados en tiempo de ejecución

| Fichero | Contenido |
|---|---|
| `Python/data/facturas.json` | Facturas generadas por cada compra |
| `Python/data/envios.json`   | Envíos asignados a transportista con fecha prevista |
| `Python/data/pedidos.json`  | Cola temporal de pedidos (vacío tras procesarse) |
