# ECSDI 2026 — Plataforma de e-commerce multiagente

Sistema de comercio electrónico distribuido desarrollado con el paradigma de sistemas multiagente (SMA) usando Python, FIPA-ACL y RDFlib. Cada componente funcional —catálogo, pedidos, logística, transporte, pagos, experiencia de usuario y devoluciones— está encapsulado en un agente autónomo que se comunica mediante mensajes ACL sobre grafos RDF. El sistema incluye una interfaz web completa accesible desde el navegador.

---

## Funcionalidades implementadas (nivel avanzado)

1. **Registro y descubrimiento de transportistas** — Los agentes transportistas se registran en el `DirectoryService` al arrancar. El `AgenteLogistico` los descubre dinámicamente en tiempo de ejecución sin configuración estática, lo que permite añadir o retirar transportistas sin modificar el código.

2. **Negociación Contract Net en dos rondas** — El `AgenteLogistico` emite un CFP a todos los transportistas disponibles (Ronda 1), calcula una contraoferta al 90 % del precio mínimo recibido y la envía a todos (Ronda 2). Cada transportista responde aceptando, proponiendo un precio intermedio o rechazando. El ganador se selecciona por precio mínimo (prioridad normal/económica) o por plazo mínimo (prioridad urgente).

3. **Pedidos multi-centro con sub-envíos independientes** — Un pedido cuyos productos están en distintos centros logísticos genera un sub-envío y una negociación Contract Net independiente por centro. La interfaz web muestra el transportista y la fecha de entrega de cada sub-envío.

4. **Gestión de pagos** — El `AgenteGestorPagos` gestiona el ciclo de vida financiero completo: registra el pago al confirmar el pedido (`pendiente`), cobra al usuario cuando el pedido sale del almacén (`cobrado`) y procesa el reembolso al aceptar una devolución (`devuelto`). Si el pedido incluye productos de un vendedor externo, también consulta su pasarela de pago y le transfiere el importe correspondiente.

5. **Experiencia de usuario** — El `AgenteExperiencia` registra el historial de compras y búsquedas de cada usuario, solicita valoraciones de forma proactiva tras la entrega estimada del pedido y genera recomendaciones personalizadas periódicas basadas en múltiples estrategias (valoraciones de comunidad, popularidad, categorías del historial, etc.).

---

## Estructura del proyecto

```
ECSDI2026/
├── Python/
│   ├── DirectoryService.py          Registro y localización de agentes (puerto 9000)
│   ├── AgenteComprador.py           Catálogo unificado (tienda propia + externos) y búsqueda
│   ├── AgenteGestorPedidos.py       Gestión de pedidos, facturas y asignación de centros logísticos
│   ├── AgenteGestorPagos.py         Ciclo de vida del pago: cobro, pago a vendedor y reembolso
│   ├── AgenteLogistico.py           Gestión de envíos y negociación Contract Net con transportistas
│   ├── AgenteTransportista.py       Transportista parametrizable con perfiles eco/estandar/premium
│   ├── AgenteExperiencia.py         Historial, valoraciones, feedback y recomendaciones personalizadas
│   ├── AgenteDevolucion.py          Evaluación y tramitación de devoluciones
│   ├── AgenteVendedorExterno.py     Vendedor externo: catálogo, pedidos y pagos propios
│   ├── AgenteUsuario.py             Interfaz web Flask (puerto 9020)
│   ├── ontologia.py                 Definición del namespace y términos de la ontología ECSNS
│   ├── start_demo.sh                Arranca los 16 agentes en local de forma ordenada
│   ├── stop_demo.sh                 Para todos los agentes en ejecución
│   ├── jp_cliente.py                Scripts de prueba JP1–JP6 (negociación logística)
│   ├── jp7_jp8.py                   Scripts de prueba JP7–JP8 (búsqueda y devoluciones)
│   ├── jp9_jp14.py                  Scripts de prueba JP9–JP14 (integración, pagos, experiencia)
│   ├── templates/                   Plantillas HTML de la interfaz web
│   └── data/                        Ficheros JSON de persistencia (ver sección Bases de datos)
├── AgentUtil/                       Utilidades compartidas: ACL, Flask, DS, logging
├── Ontologias/                      Ontología OWL del proyecto
├── JuegosDePrueba.md                Descripción detallada de los juegos de prueba JP1–JP14
├── requirements.txt                 Dependencias Python
└── README.md                        Este fichero
```

---

## Instalación

```bash
# Clonar el repositorio y situarse en la raíz del proyecto
git clone <url-del-repositorio>
cd ECSDI2026

# Crear y activar el entorno virtual
bash                            # OPCIONAL: Ejecutar si la terminal no es bash
python3 -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate

# Instalar dependencias
pip install -r requirements.txt

# Añadir el paquete AgentUtil al path de Python
export PYTHONPATH=$(pwd)         # Windows: set PYTHONPATH=%CD%
```

---

## Ejecución local (un solo ordenador)

```bash
cd Python
bash start_demo.sh
```

El script arranca los 16 agentes en el orden correcto con los tiempos de espera necesarios entre ellos.

- **Interfaz web:** http://localhost:9020/
- **DirectoryService:** http://localhost:9000/info

Para parar todos los agentes:

```bash
bash stop_demo.sh
```

> Si solo hay un ordenador disponible, `start_demo.sh` es suficiente para ejecutar el sistema completo. Las secciones siguientes aplican únicamente cuando se desea distribuir los agentes entre varias máquinas.

---

## Ejecución distribuida (varios ordenadores en LAN)

### Variables de entorno

| Variable | Descripción | Valor por defecto |
|---|---|---|
| `ECSDI_PUBLIC_HOST` | IP o hostname **de la máquina donde corre este agente**, que se publica en el DirectoryService para que otros agentes puedan contactarle | `localhost` |
| `ECSDI_DHOST` | IP o hostname de la máquina donde está el **DirectoryService** | `localhost` |
| `ECSDI_DPORT` | Puerto del DirectoryService | `9000` |

`ECSDI_PUBLIC_HOST` debe configurarse en **cada máquina** antes de lanzar sus agentes. `ECSDI_DHOST` debe apuntar siempre a la máquina que ejecuta el DirectoryService.

### Orden de inicialización obligatorio

El DirectoryService debe estar en marcha antes que cualquier otro agente. El resto debe arrancarse en este orden:

```
1. DirectoryService
2. Infraestructura: AgenteComprador, AgenteGestorPedidos, AgenteGestorPagos,
                    AgenteExperiencia, AgenteDevolucion, AgenteVendedorExterno
3. AgenteLogistico  (uno por centro; esperan registrarse en el DS)
4. AgenteTransportista  (se registran y quedan a la escucha de CFPs)
5. AgenteUsuario    (siempre el último; necesita que el resto esté registrado)
```

### Ejemplo con dos máquinas

**Máquina A — IP: `192.168.1.10`**
Ejecuta: DirectoryService, todos los agentes de infraestructura y AgenteUsuario.

**Máquina B — IP: `192.168.1.11`**
Ejecuta: los 4 AgenteLogistico y los 4 AgenteTransportista.

---

#### Máquina A (`192.168.1.10`)

```bash
# Configurar variables de entorno (una sola vez por sesión de terminal)
export ECSDI_PUBLIC_HOST=192.168.1.10
export ECSDI_DHOST=192.168.1.10
export PYTHONPATH=$(pwd)/..    # ejecutar desde Python/

# 1. DirectoryService
python3 DirectoryService.py --port 9000 --open &

# Esperar ~2 s a que el DS esté listo
sleep 2

# 2. Agentes de infraestructura
python3 AgenteComprador.py       --port 9001 --dport 9000 --open &
python3 AgenteGestorPedidos.py   --port 9002 --dport 9000 --open &
python3 AgenteGestorPagos.py     --port 9014 --dport 9000 --open &
python3 AgenteExperiencia.py     --port 9005 --dport 9000 --open &
python3 AgenteDevolucion.py      --port 9006 --dport 9000 --open &
python3 AgenteVendedorExterno.py --port 9007 --dport 9000 --nombre VendedorExterno1 --open &

sleep 1

# 5. AgenteUsuario (siempre el último)
python3 AgenteUsuario.py --port 9020 --dport 9000 --open &
```

---

#### Máquina B (`192.168.1.11`)

```bash
# Configurar variables de entorno
export ECSDI_PUBLIC_HOST=192.168.1.11
export ECSDI_DHOST=192.168.1.10     # apunta al DS en Máquina A
export PYTHONPATH=$(pwd)/..

# 3. AgenteLogistico (uno por centro)
python3 AgenteLogistico.py --port 9003 --dport 9000 --centro "Centro Madrid"    --open &
python3 AgenteLogistico.py --port 9004 --dport 9000 --centro "Centro Barcelona" --open &
python3 AgenteLogistico.py --port 9008 --dport 9000 --centro "Centro Valencia"  --open &
python3 AgenteLogistico.py --port 9009 --dport 9000 --centro "Centro Sevilla"   --open &

sleep 1

# 4. AgenteTransportista (uno por modalidad)
python3 AgenteTransportista.py --port 9010 --dport 9000 --nombre RapidExpress   --modalidad premium   --open &
python3 AgenteTransportista.py --port 9011 --dport 9000 --nombre EcoEnvios      --modalidad eco       --open &
python3 AgenteTransportista.py --port 9012 --dport 9000 --nombre MensajeriaPlus --modalidad estandar  --open &
python3 AgenteTransportista.py --port 9013 --dport 9000 --nombre SurExpress     --modalidad estandar  --open &
```

> **Tres o más ordenadores:** todos los agentes pueden distribuirse libremente (preferentemente los transportistas y logísticos). Cada instancia solo necesita `ECSDI_PUBLIC_HOST` apuntando a su propia máquina y `ECSDI_DHOST` apuntando al DirectoryService, independientemente del puerto que use.

---

## Tabla de puertos

| Puerto | Agente | Instancias |
|--------|--------|-----------|
| 9000 | DirectoryService | 1 |
| 9001 | AgenteComprador | 1 |
| 9002 | AgenteGestorPedidos | 1 |
| 9003 | AgenteLogistico — Centro Madrid | 1 |
| 9004 | AgenteLogistico — Centro Barcelona | 1 |
| 9005 | AgenteExperiencia | 1 |
| 9006 | AgenteDevolucion | 1 |
| 9007 | AgenteVendedorExterno | 1+ |
| 9008 | AgenteLogistico — Centro Valencia | 1 |
| 9009 | AgenteLogistico — Centro Sevilla | 1 |
| 9010 | AgenteTransportista — RapidExpress (`premium`) | 1 |
| 9011 | AgenteTransportista — EcoEnvios (`eco`) | 1 |
| 9012 | AgenteTransportista — MensajeriaPlus (`estandar`) | 1 |
| 9013 | AgenteTransportista — SurExpress (`estandar`) | 1 |
| 9014 | AgenteGestorPagos | 1 |
| 9020 | AgenteUsuario (interfaz web) | 1 |

Los puertos son configurables con `--port`. El puerto del DirectoryService se pasa con `--dport` a todos los demás agentes.

### Perfiles de modalidad de transportista

| Modalidad | Precio base | Coste por peso | Días de entrega |
|-----------|-------------|----------------|-----------------|
| `premium` | 12–22 € | × 2,0 €/kg | 1 día siempre |
| `eco` | 3–8 € | × 0,5 €/kg | días prioridad + 2 |
| `estandar` | 3–30 € (según prioridad) | × 1,0 €/kg | aleatorio según prioridad |

---

## Bases de datos

Todos los ficheros se encuentran en `Python/data/`. Se inicializan vacíos al ejecutar `start_demo.sh`.

| Fichero | Agente propietario | Acceso | Contenido |
|---|---|---|---|
| `listado_productos_detallados.json` | AgenteComprador | R/W | Catálogo unificado: productos propios y de vendedores externos |
| `centros_logisticos.json` | AgenteGestorPedidos | R | Centros logísticos disponibles y productos que almacena cada uno |
| `listado_facturas.json` | AgenteGestorPedidos | R/W | Facturas generadas con productos, totales y envíos asociados |
| `listado_pedidos_centro_madrid.json` | AgenteLogistico (Madrid) | R/W | Pedidos pendientes de envío en el Centro Madrid |
| `listado_pedidos_centro_barcelona.json` | AgenteLogistico (Barcelona) | R/W | Pedidos pendientes de envío en el Centro Barcelona |
| `listado_pedidos_centro_valencia.json` | AgenteLogistico (Valencia) | R/W | Pedidos pendientes de envío en el Centro Valencia |
| `listado_pedidos_centro_sevilla.json` | AgenteLogistico (Sevilla) | R/W | Pedidos pendientes de envío en el Centro Sevilla |
| `listado_envios_centro_madrid.json` | AgenteLogistico (Madrid) | W | Envíos confirmados: transportista, fecha y productos del Centro Madrid |
| `listado_envios_centro_barcelona.json` | AgenteLogistico (Barcelona) | W | Envíos confirmados del Centro Barcelona |
| `listado_envios_centro_valencia.json` | AgenteLogistico (Valencia) | W | Envíos confirmados del Centro Valencia |
| `listado_envios_centro_sevilla.json` | AgenteLogistico (Sevilla) | W | Envíos confirmados del Centro Sevilla |
| `informacion_pago.json` | AgenteGestorPagos | R/W | Registros de pago con estado (`pendiente` / `cobrado` / `devuelto`) |
| `historial_compras.json` | AgenteExperiencia | R/W | Compras completadas por usuario, usadas para recomendaciones |
| `historial_busquedas.json` | AgenteExperiencia | R/W | Búsquedas realizadas por usuario, usadas para recomendaciones |
| `listado_opiniones.json` | AgenteExperiencia | R/W | Valoraciones enviadas y pendientes de enviar por producto |
| `listado_devoluciones.json` | AgenteDevolucion | W | Solicitudes de devolución procesadas con estado y motivo |
| `catalogo_VendedorExterno1.json` | AgenteVendedorExterno | R/W | Catálogo propio del vendedor externo con stock |
| `pedidos_VendedorExterno1.json` | AgenteVendedorExterno | R/W | Pedidos recibidos por el vendedor externo con estado |
