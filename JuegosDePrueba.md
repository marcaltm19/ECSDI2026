# Juegos de Prueba — ECSDI 2026

## 1. Introducción

Este documento describe los juegos de prueba definidos para validar el funcionamiento del sistema multiagente de comercio electrónico desarrollado en ECSDI 2026. El sistema implementa una plataforma de e-commerce distribuida basada en el paradigma de sistemas multiagente (SMA), donde cada componente funcional —catálogo, gestión de pedidos, logística, transporte, experiencia de usuario y devoluciones— está encapsulado en un agente autónomo que se comunica mediante mensajes ACL sobre grafos RDF.

El subsistema que centra los juegos de prueba es la **negociación logística**, implementada como un protocolo Contract Net en dos rondas entre el `AgenteLogistico` y los `AgenteTransportista` registrados en el `DirectoryService`.

---

## 2. Protocolo de negociación: Contract Net en dos rondas

Antes de describir los juegos de prueba, se detalla el protocolo que se valida, pues es el núcleo funcional que todos ellos ejercitan.

### Ronda 1 — Solicitud de oferta inicial (CFP)

1. El `AgenteLogistico` consulta el `DirectoryService` para obtener las direcciones de todos los `AgenteTransportista` registrados con tipo `ECSNS['Ag.Transportista']`.
2. Para cada transportista, envía un mensaje `ACL.request` con acción `ECSNS.CFP` que contiene `ECSNS.tieneDestino` y `ECSNS.tienePrioridad`.
3. Cada transportista genera una oferta aleatoria dentro del rango de precio correspondiente a la prioridad recibida:
   - `normal`: 8–15 €, 2–4 días
   - `urgente`: 15–30 €, 1–2 días
   - `economica`: 3–8 €, 4–6 días
4. El transportista responde con `ACL.propose` conteniendo `ECSNS.tienePrecio`, `ECSNS.tieneTransportista` y `ECSNS.tieneFechaEntrega`.

### Ronda 2 — Contraoferta

5. El logístico calcula la **contraoferta** como el 90 % del precio mínimo recibido en R1: `contra_precio = min_R1 × 0,90`.
6. Envía la contraoferta a cada transportista que respondió, mediante `ACL.propose` con acción `ECSNS.ContraOferta`.
7. Cada transportista responde de forma aleatoria con una de las tres acciones del protocolo:
   - **ACEPTA** (`ACL.inform`): acepta el precio de la contraoferta.
   - **PROPONE** (`ACL.propose`): propone un precio intermedio estrictamente entre la contraoferta y su oferta inicial.
   - **RECHAZA** (`ACL['reject-proposal']`): rechaza la contraoferta, quedando excluido del pool final.
8. Si ningún transportista acepta la contraoferta, el logístico utiliza las ofertas de la Ronda 1 como pool final.

### Selección del ganador

9. El criterio de selección depende de la prioridad del pedido:
   - `normal` / `economica`: gana el transportista con el **precio más bajo** del pool final.
   - `urgente`: gana el transportista con el **menor número de días** hasta la entrega; en caso de empate, se aplica el criterio de precio mínimo.
10. El logístico envía `ACL.accept-proposal` al ganador y `ACL.reject-proposal` al resto.
11. Se registra el envío en `data/listado_envios_<centro>.json` y se notifica al `AgenteGestorPedidos` y al `AgenteExperiencia`.

---

## 3. Metodología de selección de los juegos de prueba

Los juegos de prueba han sido seleccionados aplicando una estrategia de **cobertura por categorías de comportamiento**, combinando técnicas de caja negra (particiones de equivalencia, análisis de valores límite) con la cobertura de los requisitos funcionales explícitos de la práctica:

| JP | Categoría | Justificación |
|----|-----------|---------------|
| JP1 | Camino feliz — criterio precio | Valida el flujo completo del Contract Net con prioridad `normal`; establece la línea base de funcionamiento correcto. |
| JP2 | Variación de parámetro clave | La prioridad `urgente` activa la rama alternativa del criterio de selección (días vs. precio), ejercitando una partición de equivalencia diferente del mismo flujo. |
| JP3 | Caso límite / robustez | La ausencia de transportistas en el DS es la condición de fallo más extrema; verifica que el sistema degrada con elegancia sin bloqueos ni excepciones. |
| JP5 | Concurrencia | Tres pedidos simultáneos con prioridades distintas ejercitan el acceso concurrente a las estructuras de datos y detectan posibles condiciones de carrera. |
| JP6 | Funcionalidad avanzada §3.4 | Un pedido con productos de dos centros logísticos distintos debe generar sub-envíos independientes; valida el agrupamiento por centro y la ejecución de múltiples negociaciones. |
| JP4 | Interoperabilidad §3.5 | Un transportista externo de otro grupo compite en la negociación usando la ontología y el protocolo acordados, sin modificar el código del sistema. |

Esta selección cubre: el flujo nominal completo, las ramas de decisión principales, los casos frontera de fallo de dependencias externas, la concurrencia, y los dos requisitos de nivel avanzado e interoperabilidad.

---

## 4. Descripción detallada de los juegos de prueba

### JP1 — Selección del transportista más barato (prioridad normal)

**Objetivo:** Verificar que el `AgenteLogistico` ejecuta correctamente el protocolo Contract Net en dos rondas y selecciona al transportista de menor precio cuando la prioridad del pedido es `normal`.

**Configuración inicial:**
- Sistema completo arrancado con `start_demo.sh`: DirectoryService, cuatro AgenteLogistico (Madrid, Barcelona, Valencia, Sevilla) y cuatro AgenteTransportista (RapidExpress·Madrid, EcoEnvios·Barcelona, MensajeriaPlus·Valencia, SurExpress·Sevilla).
- El `jp_cliente.py` envía un pedido con prioridad `normal`, destino `Barcelona`, un producto de 5 kg.

**Flujo detallado:**

1. El `AgenteLogistico` de Centro Madrid recibe el pedido (mensaje `ACL.request`, acción `ECSNS.SolicitudPedido`) y lo almacena en `data/listado_pedidos_centro_madrid.json`.
2. En el ciclo periódico (cada 20 s), invoca `negociar_transporte('normal', 'Barcelona', {'nombre': 'Centro Madrid'})`.
3. Consulta el DS: obtiene la lista de transportistas filtrada por ciudad de cobertura; si no hay coincidencia exacta, negocia con todos.
4. **R1:** envía CFP a cada transportista; cada uno responde con `ACL.propose` conteniendo precio aleatorio en [8, 15] € y fecha en [2, 4] días.
5. Calcula `contra_precio = min_R1 × 0,90` y envía la contraoferta a todos.
6. **R2:** cada transportista acepta, propone precio intermedio o rechaza.
7. El logístico selecciona el ganador por precio mínimo del pool final (o R1 si nadie aceptó).
8. Envía `ACL.accept-proposal` al ganador y `ACL.reject-proposal` al resto.
9. Registra el envío en `data/listado_envios_centro_madrid.json`.

**Salida esperada:**
- Log del AgenteLogistico: líneas `Oferta R1 de <nombre>: <precio>EUR`, `Contra-oferta: <valor>EUR`, `GANADOR: <nombre> -- <precio>EUR`.
- `data/listado_envios_*.json`: un registro con el transportista ganador y la fecha de entrega.

**Criterio de superación:** el transportista con el precio más bajo en el pool final es el registrado como ganador.

**Comando:**
```bash
python jp_cliente.py --jp 1
```

---

### JP2 — Prioridad urgente: gana el más rápido

**Objetivo:** Verificar que cuando la prioridad del pedido es `urgente`, el criterio de selección cambia de precio mínimo a número de días mínimo, priorizando la rapidez de entrega sobre el coste.

**Configuración inicial:** idéntica a JP1.

**Flujo detallado:**

1. El pedido llega con `prioridad = urgente`, destino `Madrid`, 1 producto de 2 kg.
2. En R1, cada transportista genera precios en [15, 30] € y plazos en [1, 2] días (rango para `urgente`).
3. La contraoferta se calcula igualmente como el 90 % del precio mínimo R1.
4. El pool final se construye con las mismas reglas de R2.
5. La función `escoger_mejor_oferta(pool_final, 'urgente')` ordena por `dias` (no por `precio`). En caso de empate en días, desempata por precio mínimo.
6. El log muestra: `GANADOR (urgente): <nombre> -- <dias> dias -- <precio>EUR`.

**Diferencia observable respecto a JP1:** es posible que el transportista ganador no sea el más barato, pero sí el que ofrece entrega en menos días. Si se observa que el ganador tiene el precio más bajo *y* el menor número de días, el resultado es también correcto (coinciden ambos criterios por azar).

**Salida esperada:**
- Log: `GANADOR (urgente): <nombre>` con el valor de días más bajo del pool.
- `data/listado_envios_*.json`: fecha de entrega más próxima entre todas las ofertas.

**Criterio de superación:** el ganador tiene el número de días de entrega mínimo del pool final.

**Comando:**
```bash
python jp_cliente.py --jp 2
```

---

### JP3 — Ningún transportista disponible (caso límite)

**Objetivo:** Verificar que el sistema es robusto ante la ausencia total de transportistas registrados en el DirectoryService: no se bloquea, no lanza excepción y retorna un resultado de fallback controlado.

**Configuración especial:** arrancar únicamente el DS y un AgenteLogistico, sin ningún AgenteTransportista.

```bash
python DirectoryService.py --port 9000
# (en otra terminal)
python AgenteLogistico.py --port 9003 --dport 9000 --centro "Centro Madrid"
```

**Flujo detallado:**

1. El logístico recibe el pedido y llama a `negociar_transporte`.
2. La función `_buscar_transportistas()` consulta el DS y recibe una lista vacía.
3. Se ejecuta el bloque de fallback: devuelve `('Desconocido', fecha_actual + 3 días)` sin realizar ningún CFP.
4. Se registra un envío con `transportista: "Desconocido"` y fecha a +3 días.
5. Se notifica igualmente al GestorPedidos y al AgenteExperiencia (si están disponibles).

**Salida esperada:**
- Log: `[Logistico] No hay transportistas en el DS, usando fallback`.
- `data/listado_envios_*.json`: registro con `"transportista": "Desconocido"` y fecha calculada por defecto.
- El agente continúa respondiendo a peticiones posteriores sin necesidad de reinicio.

**Criterio de superación:** el sistema responde sin excepción y el pedido queda registrado con los valores de fallback.

**Comando:**
```bash
python jp_cliente.py --jp 3
```

---

### JP4 — Integración con agente transportista externo (interoperabilidad)

**Objetivo:** Verificar la interoperabilidad entre sistemas de distintos grupos mediante la ontología y el protocolo Contract Net acordados. Un AgenteTransportista del otro grupo debe poder participar en la negociación y ser seleccionado si ofrece la mejor propuesta.

**Requisitos previos:**
- El otro grupo tiene su AgenteTransportista externo accesible en la red local (LAN).
- El transportista externo se registra en el DirectoryService del grupo anfitrión con tipo `ECSNS['Ag.Transportista']`.
- Ambos grupos utilizan el namespace `http://www.semanticweb.org/ecsdi/ontologies/2026/e-shop#` y los predicados: `tienePrecio`, `tieneDestino`, `tienePrioridad`, `tieneTransportista`, `tieneFechaEntrega`.

**Arranque del transportista externo** (ejecutado desde el PC del otro grupo):
```bash
export PYTHONPATH="$(pwd)/..:$PYTHONPATH"
python3 AgenteTransportista.py --port 9014 --dhost <IP_anfitrión> --dport 9000 \
  --nombre TransportistaExterno --ciudad Madrid --open
```

**Flujo detallado:**

1. El logístico consulta el DS y obtiene la lista que incluye al transportista externo junto a los propios.
2. Envía el mismo CFP a todos los transportistas (propios y externos) sin distinción de origen.
3. El transportista externo responde con `ACL.propose` usando la ontología acordada.
4. El logístico evalúa todas las propuestas con el mismo criterio y selecciona la mejor.
5. Si el transportista externo ofrece el precio más bajo (o los menos días, si la prioridad es urgente), recibe `ACL.accept-proposal`.

**Salida esperada:**
- El DS (`/info`) muestra al transportista externo registrado junto a los propios.
- Log del logístico: el nombre del transportista externo aparece en la línea de oferta R1.
- `data/listado_envios_*.json`: si el externo gana, su nombre aparece como `transportista`.

**Criterio de superación:** el transportista externo participa en la negociación y puede ganarla, sin que sea necesario ningún cambio en el código del AgenteLogistico.

**Nota:** este JP requiere coordinación previa con el otro grupo para verificar conectividad de red y compatibilidad de la ontología.

---

### JP5 — Múltiples pedidos simultáneos

**Objetivo:** Verificar que el sistema gestiona correctamente varios pedidos concurrentes sin que las negociaciones se interfieran entre sí, y que cada pedido se asigna de forma independiente al transportista correcto según su prioridad.

**Configuración inicial:** sistema completo arrancado con `start_demo.sh`.

**Flujo detallado:**

1. El script `jp_cliente.py --jp 5` lanza tres hilos simultáneos, cada uno enviando un pedido:
   - Pedido 1: `prioridad = normal`, destino `Barcelona`.
   - Pedido 2: `prioridad = urgente`, destino `Madrid`.
   - Pedido 3: `prioridad = economica`, destino `Sevilla`.
2. Los tres pedidos llegan casi simultáneamente al AgenteLogistico correspondiente y son almacenados en `listado_pedidos_<centro>.json`.
3. La función `guardar_pedidos` aplica un `sort` por prioridad (`urgente=0`, `normal=1`, `economica=2`), garantizando que el pedido urgente se procesa primero.
4. En el ciclo periódico, `realizar_envios()` procesa cada pedido secuencialmente: para cada uno se realiza una negociación Contract Net independiente con su propio conjunto de mensajes CFP y contraoferta.
5. Se generan tres envíos separados, uno por pedido.

**Salida esperada:**
- `data/listado_envios_*.json`: tres registros con `pedido_id` distintos.
- El pedido urgente tiene la fecha de entrega más próxima entre los tres.
- No hay mezcla de transportistas ni de ofertas entre pedidos distintos.

**Criterio de superación:** tres envíos registrados, el urgente con menor fecha, sin mezcla de datos entre pedidos.

**Comando:**
```bash
python jp_cliente.py --jp 5
```

---

### JP6 — Pedido multi-centro (dos sub-envíos independientes)

**Objetivo:** Verificar que un pedido con productos pertenecientes a centros logísticos distintos genera negociaciones y envíos independientes, uno por centro, tal como exige el nivel avanzado §3.4 de la práctica.

**Configuración inicial:** sistema completo arrancado con `start_demo.sh` (incluye AgenteLogistico para Madrid y para Barcelona).

**Flujo detallado:**

1. El pedido incluye dos productos: `p001` (asignado a Centro Madrid) y `p002` (asignado a Centro Barcelona).
2. El `AgenteGestorPedidos` enruta cada producto al AgenteLogistico del centro correspondiente mediante el campo `ECSNS.tieneCentro` de cada nodo producto.
3. El AgenteLogistico de Madrid recibe `p001` y ejecuta la negociación filtrando transportistas con `ciudad = Madrid` → negocia con RapidExpress.
4. El AgenteLogistico de Barcelona recibe `p002` y ejecuta la negociación filtrando transportistas con `ciudad = Barcelona` → negocia con EcoEnvios.
5. Cada logístico genera su propio envío (con ID distinto) y lo notifica al GestorPedidos con `ECSNS.ResultadoEnvio`.
6. El GestorPedidos acumula los sub-envíos y asocia ambos al mismo `pedido_id` original.

**Salida esperada:**
- `data/listado_envios_centro_madrid.json`: un envío con `pedido_id = PED-JP6-001` y `centro_logistico = Centro Madrid`.
- `data/listado_envios_centro_barcelona.json`: un envío con el mismo `pedido_id` y `centro_logistico = Centro Barcelona`.
- En la interfaz web (http://localhost:9020/), el historial del pedido muestra dos líneas de envío con transportistas y fechas distintos.

**Criterio de superación:** dos registros de envío con el mismo `pedido_id`, centros distintos y transportistas distintos.

**Comando:**
```bash
python jp_cliente.py --jp 6
```

---

## 5. Guía de ejecución para la demo

```bash
# 1. Arrancar el sistema completo
cd Python
bash start_demo.sh

# 2. Esperar ~5 s a que todos los agentes estén registrados
#    Verificar en http://localhost:9000/info que aparecen los 4 transportistas

# 3. Ejecutar el juego de prueba elegido
python jp_cliente.py --jp 1   # JP1: más barato gana
python jp_cliente.py --jp 2   # JP2: prioridad urgente (más rápido gana)
python jp_cliente.py --jp 5   # JP5: tres pedidos simultáneos
python jp_cliente.py --jp 6   # JP6: pedido multi-centro

# 4. Observar en los logs de AgenteLogistico:
#    - Ofertas R1 de cada transportista
#    - Cálculo de la contraoferta (90 % del mínimo R1)
#    - Respuestas de R2 (acepta / propone / rechaza)
#    - Línea "GANADOR" con nombre, precio y días

# 5. Verificar el resultado en los ficheros de datos
Get-Content Python\data\listado_envios_centro_madrid.json

# 6. Parar el sistema
bash stop_demo.sh
```

### Preparación especial para JP3 (sin transportistas)

```bash
python DirectoryService.py --port 9000 --open
# (en otra terminal)
python AgenteLogistico.py --port 9003 --dport 9000 --centro "Centro Madrid" --open
python jp_cliente.py --jp 3
```

### Preparación especial para JP4 (transportista externo)

```bash
# En el PC del otro grupo:
export ECSDI_PUBLIC_HOST=<tu_IP_LAN>
python3 AgenteTransportista.py --port 9014 \
  --dhost <IP_anfitrión> --dport 9000 \
  --nombre TransportistaExterno --ciudad Madrid --open
```

Guía de ontología e interoperabilidad: [Ontologias/INTEROPERABILIDAD_TRANSPORTE.md](Ontologias/INTEROPERABILIDAD_TRANSPORTE.md)
