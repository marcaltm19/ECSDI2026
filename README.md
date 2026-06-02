# ECSDI 2025/2026

Práctica de Enginyeria del Coneixement i Sistemes Distribuïts Intel·ligents,
cuatrimestre de primavera 2025/2026.

## Nivel avanzado (§3.4) implementado

1. **Registro y descubrimiento de transportistas** — `DirectoryService` + varias instancias de `AgenteTransportista` con condiciones y ciudades distintas.
2. **Negociación Contract Net en dos rondas** — CFP, contraoferta al 90 % del mínimo, respuestas aceptar/proponer/rechazar (`AgenteLogistico` + `AgenteTransportista`).
3. **Pedidos multi-centro** — un envío y transportista por centro logístico; la UI muestra varias fechas/transportistas.
4. **Experiencia de usuario** — valoraciones proactivas tras la entrega prevista y recomendaciones periódicas (historial de compra y búsquedas).

**Nota extra (§3.5):** ver [Ontologias/INTEROPERABILIDAD_TRANSPORTE.md](Ontologias/INTEROPERABILIDAD_TRANSPORTE.md) para integrar el transportista de otro grupo.

## Estructura del proyecto

### Código de la práctica
- `Python/AgenteComprador.py`      — búsqueda y catálogo
- `Python/AgenteGestorPedidos.py`  — pedidos, facturas, notificaciones
- `Python/AgenteLogistico.py`      — envíos y negociación con transportistas
- `Python/AgenteTransportista.py`  — transportista (varias instancias)
- `Python/AgenteExperiencia.py`    — valoraciones, historial, recomendaciones, feedback
- `Python/AgenteDevolucion.py`     — devoluciones
- `Python/AgenteVendedorExterno.py`— catálogo externo
- `Python/AgenteUsuario.py`        — interfaz web (puerto 9020)
- `Python/DirectoryService.py`     — servicio de directorio
- `Python/jp_cliente.py`           — juegos de prueba JP1–JP6
- `Python/start_demo.sh` / `stop_demo.sh` — arranque completo
- `Ontologias/ontoECSDI.owx`       — ontología del proyecto
- `Ontologias/transporte-interop.ttl` — fragmento para interoperabilidad

### Material de laboratorio
- `AgentUtil/` — utilidades ACL/Flask
- `Examples/` — ejemplos del profesor
- `JuegosDePrueba.md` — descripción de los JP

## Instalación

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
export PYTHONPATH=$(pwd)
```

## Demo rápida

```bash
cd Python
bash start_demo.sh
```

- UI: http://localhost:9020/
- DS: http://localhost:9000/info

Juegos de prueba (con la demo en marcha):

```bash
python3 jp_cliente.py --jp 1   # transportista más barato
python3 jp_cliente.py --jp 6   # pedido multi-centro
```

## Puertos por defecto

| Puerto | Agente |
|--------|--------|
| 9000 | DirectoryService |
| 9001 | AgenteComprador |
| 9002 | AgenteGestorPedidos |
| 9003 | AgenteLogistico |
| 9005 | AgenteExperiencia |
| 9006 | AgenteDevolucion |
| 9007 | AgenteVendedorExterno |
| 9010–9012 | Transportistas (RapidExpress, EcoEnvios, MensajeriaPlus) |
| 9020 | AgenteUsuario (web) |

Para demo en LAN con otro grupo: `export ECSDI_PUBLIC_HOST=<tu_IP>` antes de `start_demo.sh`.

## Datos generados

- `Python/data/facturas.json`
- `Python/data/envios.json`
- `Python/data/valoraciones.json`
- `Python/data/historial_compras.json`
- `Python/data/historial_busquedas.json`
