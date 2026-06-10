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
| JP4 | Interoperabilidad §3.5 | Un transportista externo de otro grupo compite en la negociación usando la ontología y el protocolo acordados, sin modificar el código del sistema. |
| JP5 | Concurrencia | Tres pedidos simultáneos con prioridades distintas ejercitan el acceso concurrente a las estructuras de datos y detectan posibles condiciones de carrera. |
| JP6 | Funcionalidad avanzada §3.4 | Un pedido con productos de dos centros logísticos distintos debe generar sub-envíos independientes; valida el agrupamiento por centro y la ejecución de múltiples negociaciones. |
| JP7 | Búsqueda con filtros | Cubre las cinco combinaciones de filtros del catálogo (categoría, precio máximo, valoración mínima, combinada, sin resultados); verifica que el AgenteComprador respeta todos los criterios. |
| JP8 | Devoluciones | Cubre los cinco caminos del flujo de devolución: aceptación automática, plazo válido, fuera de plazo, factura inexistente y devolución duplicada. |
| JP9 | Integración extremo a extremo | Valida la cadena completa Usuario → GestorPedidos → Logístico → Transportista en dos fases: síncrona (factura) y asíncrona (envío registrado). |
| JP10 | Perfiles de modalidad de transportista | Verifica que los tres perfiles (premium, eco, estándar) generan precios y plazos correctos, incluyendo el coste por peso. |
| JP11 | Asignación de centro por ciudad | Ejercita las tres ramas del `mapa_zonas`: stock en un único centro (obligatorio), varios centros (preferencia geográfica) y ciudad no reconocida (fallback). |
| JP12 | Ciclo de vida del pago | Verifica la máquina de estados `pendiente → cobrado → devuelto` del AgenteGestorPagos. |
| JP13 | Pedido con vendedor externo | Comprueba que el GestorPedidos enruta correctamente los productos externos al AgenteVendedorExterno y registra el envío en la factura. |
| JP14 | Ciclo de experiencia de usuario | Cubre el flujo completo del AgenteExperiencia: historial de compras, registro de valoraciones, generación de recomendaciones y consulta de historial. |

Esta selección cubre: el flujo nominal completo, las ramas de decisión principales, los casos frontera de fallo de dependencias externas, la concurrencia, los requisitos de nivel avanzado e interoperabilidad, y los subsistemas de pago, vendedores externos y experiencia de usuario.

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

### JP7 — Búsqueda de productos con filtros

**Objetivo:** Verificar que el AgenteComprador aplica correctamente cada uno de los filtros de búsqueda disponibles (categoría, precio máximo, valoración mínima y combinación de varios) y que devuelve una lista vacía cuando ningún producto cumple los criterios.

**Configuración inicial:** sistema completo arrancado con `start_demo.sh`. El catálogo debe tener productos cargados en `data/listado_productos_detallados.json` (se puebla automáticamente cuando el AgenteVendedorExterno anuncia su catálogo).

**Flujo detallado:**

**JP7a — Búsqueda por categoría:**
1. El test envía `ACL.request + ECSNS.Busqueda` al AgenteComprador (puerto 9001) con `ECSNS.categoria = "Electronica"`.
2. Verifica que todos los productos devueltos tienen `categoria == "Electronica"`.
3. Cualquier producto de otra categoría en la respuesta provoca `[FAIL]`.

**JP7b — Búsqueda con precio máximo:**
1. Envía la búsqueda con `ECSNS.precioMaximo = 50.0`.
2. Verifica que ningún producto devuelto supera los 50 €.

**JP7c — Búsqueda con valoración mínima:**
1. Envía la búsqueda con `ECSNS.valoracionMinima = 4.5`.
2. Verifica que todos los productos tienen `valoracion >= 4.5`.

**JP7d — Búsqueda combinada:**
1. Envía la búsqueda con los tres filtros simultáneos: `categoria = "Electronica"`, `precioMaximo = 500.0`, `valoracionMinima = 4.0`.
2. Verifica que cada producto devuelto cumple los tres criterios a la vez.

**JP7e — Búsqueda sin resultados:**
1. Envía la búsqueda con `precioMaximo = 0.01` (filtro imposible).
2. Verifica que la respuesta contiene una lista vacía de productos.

**Salida esperada:** cada subtest imprime `[ OK ]` con el número de productos encontrados, o `[FAIL]` indicando qué productos violan el filtro.

**Criterio de superación:** los cinco subtests pasan. Si el catálogo está vacío, JP7a–JP7d reportan `[FAIL]` indicando que no se encontraron productos.

**Comandos:**
```bash
python jp7_jp8.py --jp 7          # ejecuta los cinco subtests
python jp7_jp8.py --jp 7 --sub a  # JP7a: solo búsqueda por categoría
python jp7_jp8.py --jp 7 --sub b  # JP7b: solo precio máximo
python jp7_jp8.py --jp 7 --sub c  # JP7c: solo valoración mínima
python jp7_jp8.py --jp 7 --sub d  # JP7d: solo búsqueda combinada
python jp7_jp8.py --jp 7 --sub e  # JP7e: solo búsqueda sin resultados
```

---

### JP8 — Devolución de pedido

**Objetivo:** Verificar las cinco ramas del flujo de devolución del AgenteDevolucion: aceptación automática por defecto del producto, aceptación dentro del plazo de 15 días, rechazo por fuera de plazo, rechazo por factura inexistente y rechazo por devolución duplicada.

**Configuración inicial:** sistema completo arrancado con `start_demo.sh`. El test inyecta automáticamente una factura de prueba (`FAC-JPTEST-001`) en `data/listado_facturas.json` antes de ejecutar los subtests y la elimina al terminar.

**Flujo detallado:**

**JP8a — Aceptación automática (producto defectuoso):**
1. Envía `ACL.request + ECSNS.SolicitudDevolucion` con razón `"El producto llegó defectuoso"` y fecha de recepción hace 3 días.
2. La razón contiene la palabra clave `"defectuoso"`, que activa la aceptación automática.
3. Verifica que la respuesta contiene `ECSNS.Devolucion` con `aceptada = True`.

**JP8b — Aceptación dentro del plazo:**
1. Envía la devolución con razón genérica y fecha de recepción hace 7 días (dentro del límite de 15 días).
2. Verifica que la devolución es aceptada.

**JP8c — Rechazo por fuera de plazo:**
1. Envía la devolución con fecha de recepción hace 20 días (superando los 15 días permitidos).
2. Verifica que la respuesta contiene `aceptada = False` con motivo de rechazo por plazo.

**JP8d — Rechazo por factura inexistente:**
1. Envía la devolución con `idFactura = "FAC-NOEXISTE-999"`.
2. Verifica que la respuesta contiene `aceptada = False` porque la factura no existe en el sistema.

**JP8e — Rechazo por devolución duplicada:**
1. Marca la factura de prueba como ya devuelta en el JSON.
2. Intenta devolver la misma factura con razón `"defectuoso"` (que normalmente activaría la aceptación automática).
3. Verifica que la respuesta contiene `aceptada = False` porque la factura ya fue devuelta.

Entre cada subtest que modifica el estado de la factura, el test la resetea a `devuelta = False` para que los siguientes no dependan del resultado anterior.

**Salida esperada:** cada subtest imprime `[ OK ] Devolución ACEPTADA` o `[ OK ] Devolución RECHAZADA correctamente` según el caso esperado.

**Criterio de superación:** los cinco subtests producen el resultado esperado (aceptada o rechazada según corresponda). Un `[FAIL]` indica que el agente devolvió la decisión contraria a la esperada.

**Comandos:**
```bash
python jp7_jp8.py --jp 8          # ejecuta los cinco subtests
python jp7_jp8.py --jp 8 --sub a  # JP8a: solo aceptación automática
python jp7_jp8.py --jp 8 --sub b  # JP8b: solo aceptación dentro del plazo
python jp7_jp8.py --jp 8 --sub c  # JP8c: solo rechazo por fuera de plazo
python jp7_jp8.py --jp 8 --sub d  # JP8d: solo rechazo por factura inexistente
python jp7_jp8.py --jp 8 --sub e  # JP8e: solo rechazo por devolución duplicada
```

---

### JP9 — Flujo extremo a extremo

**Objetivo:** Verificar la integración completa entre AgenteGestorPedidos, AgenteLogistico y AgenteTransportista enviando un pedido real desde el cliente de prueba y comprobando que la factura se genera y el envío queda registrado.

**Configuración inicial:** sistema completo arrancado con `start_demo.sh`.

**Flujo detallado:**

1. El script envía una `SolicitudPedido` al AgenteGestorPedidos (puerto 9002) con ciudad `Madrid`, prioridad `normal` y un producto de tienda (`p001`).
2. **Fase 1 (síncrona):** el GestorPedidos genera la factura y responde con un grafo `ECSNS.Factura`. El test comprueba que el `idFactura` está presente en la respuesta.
3. El GestorPedidos llama a `notificar_logistico`, que envía el sub-pedido al AgenteLogistico de Centro Madrid.
4. **Fase 2 (asíncrona):** el AgenteLogistico almacena el pedido y, en su ciclo periódico (~20 s), ejecuta la negociación Contract Net con los transportistas registrados.
5. El AgenteLogistico envía `ResultadoEnvio` al GestorPedidos, que actualiza el campo `envios_logistico` de la factura en `listado_facturas.json`.
6. El test hace polling sobre `listado_facturas.json` con timeout de 35 s hasta detectar `envios_logistico` no vacío.

**Salida esperada:**
- Fase 1: `[ OK ] GestorPedidos devolvió factura FAC-XXXXXX`.
- Fase 2: `[ OK ] Envío registrado` con centro, transportista y fecha de entrega.
- `listado_facturas.json`: la factura contiene al menos un elemento en `envios_logistico`.

**Criterio de superación:** ambas fases pasan; si la Fase 2 supera el timeout, se reporta `[FAIL]` pero la Fase 1 puede ser válida de forma independiente.

**Comando:**
```bash
python jp9_jp14.py --jp 9
```

---

### JP10 — Perfiles de modalidad de transportista

**Objetivo:** Verificar que los tres perfiles de modalidad (`premium`, `eco`, `estandar`) generan ofertas con precio y fecha de entrega correctos, incluyendo el coste variable por peso del paquete.

**Configuración inicial:** sistema completo arrancado con `start_demo.sh`. Los transportistas deben estar lanzados con su modalidad correspondiente:

```bash
python AgenteTransportista.py --nombre RapidExpress  --modalidad premium  --port 9010
python AgenteTransportista.py --nombre EcoEnvios     --modalidad eco      --port 9011
python AgenteTransportista.py --nombre MensajeriaPlus --modalidad estandar --port 9012
```

**Flujo detallado:**

1. El script envía un CFP (`ACL.request + ECSNS.CFP`) directamente a cada transportista, indicando prioridad y peso del paquete (`ECSNS.tienePrioridad`, `ECSNS.tienePeso`).
2. Cada transportista responde con `ACL.propose + ECSNS.Oferta` conteniendo `tienePrecio` y `tieneFechaEntrega`.
3. El test comprueba que el precio cae en el rango esperado y que la fecha coincide exactamente con `hoy + días_esperado`.

**Reglas de cada modalidad:**

| Modalidad | Precio base | Coste peso | Días de entrega |
|-----------|-------------|------------|-----------------|
| `premium` | `uniform(12, 22)` | `× 2.0` | **1 siempre** |
| `eco` | `uniform(3, 8)` | `× 0.5` | días prioridad + 2 |
| `estandar` | `uniform(3–30)` según prioridad | `× 1.0` | aleatorio según prioridad |

**Subtest `--sub r` (RapidExpress / premium):** envía CFP con las tres prioridades y comprueba que la fecha siempre es `hoy + 1` y el precio está en `[12 + peso×2, 22 + peso×2]`.

**Subtest `--sub e` (EcoEnvios / eco):** comprueba que los días son `1+2=3`, `2+2=4` y `4+2=6` para urgente, normal y económica respectivamente.

**Subtest `--sub m` (MensajeriaPlus / estandar):** comprueba precios y días intermedios para cada prioridad.

**Salida esperada:** cada subtest imprime `[ OK ]` para precio y fecha, o `[FAIL]` con el valor obtenido vs. el esperado.

**Criterio de superación:** precio y fecha correctos en los tres perfiles y las tres prioridades.

**Comandos:**
```bash
python jp9_jp14.py --jp 10          # ejecuta los tres perfiles
python jp9_jp14.py --jp 10 --sub r  # solo premium
python jp9_jp14.py --jp 10 --sub e  # solo eco
python jp9_jp14.py --jp 10 --sub m  # solo estandar
```

---

### JP11 — Asignación de centro logístico por ciudad

**Objetivo:** Verificar las tres ramas de la lógica `mapa_zonas` implementada en `notificar_logistico`: asignación obligatoria por stock único, preferencia geográfica con varios centros y fallback para ciudad no reconocida.

**Configuración inicial:** sistema completo arrancado con `start_demo.sh` y `data/centros_logisticos.json` poblado con al menos un producto por centro.

**Mapa de zonas configurado:**

| Ciudad | Centro asignado |
|--------|----------------|
| Barcelona, Zaragoza | Centro Barcelona |
| Valencia | Centro Valencia |
| Madrid, Bilbao | Centro Madrid |
| Sevilla | Centro Sevilla |

**Flujo detallado:**

**JP11a — Stock en un único centro (asignación obligatoria):**
1. El test lee `centros_logisticos.json` y busca un producto que solo aparezca en un centro.
2. Envía un pedido al GestorPedidos con una ciudad cuyo `mapa_zonas` apuntaría a un centro *diferente*.
3. Espera 2 s y comprueba que el sub-pedido llegó al único centro con stock, ignorando la ciudad.

**JP11b — Stock en varios centros (preferencia geográfica):**
1. El test busca un producto presente en más de un centro.
2. Envía el pedido con una ciudad que `mapa_zonas` mapea a uno de esos centros.
3. Verifica que el sub-pedido llegó al centro geográficamente preferido.

**JP11c — Ciudad no reconocida (fallback):**
1. Envía un pedido con `ciudad = "CiudadInventada"`.
2. Verifica que el sub-pedido llega a `Centro Madrid` (valor por defecto del fallback).

En los tres subtests el test lee `data/listado_pedidos_<centro>.json` antes y después del envío y detecta nuevas entradas.

**Salida esperada:**
- JP11a: `[ OK ] Sub-pedido enrutado a <centro_unico> (stock único)`.
- JP11b: `[ OK ] Sub-pedido enrutado a <centro_preferido> por preferencia geográfica`.
- JP11c: `[ OK ] Ciudad desconocida enrutada a Centro Madrid (fallback correcto)`.

**Criterio de superación:** los tres subtests pasan. Si `centros_logisticos.json` no existe o no hay productos con la distribución necesaria, el subtest afectado se marca `[SKIP]`.

**Comando:**
```bash
python jp9_jp14.py --jp 11
```

---

### JP12 — Ciclo de vida del pago

**Objetivo:** Verificar la máquina de estados del AgenteGestorPagos comprobando que las tres transiciones (`pendiente → cobrado → devuelto`) se producen correctamente en respuesta a los mensajes ACL correspondientes.

**Configuración inicial:** AgenteGestorPagos en puerto 9014 arrancado.

**Flujo detallado:**

**JP12a — `InformacionPago` crea el registro en estado `pendiente`:**
1. El test envía `ACL.inform + ECSNS.InformacionPago` con `idPedido`, `comprador`, `metodoPago` y `total`.
2. Espera 1 s y lee `data/informacion_pago.json`.
3. Verifica que existe una entrada con `orderId` igual al pedido de prueba y `estadoPago = pendiente`.

**JP12b — `ConfirmacionEnvio` activa el cobro:**
1. El test envía `ACL.inform + ECSNS.ConfirmacionEnvio` con el mismo `idPedido`.
2. Verifica que `estadoPago` ha pasado a `cobrado`.

**JP12c — `SolicitudReembolso` procesa la devolución:**
1. El test envía `ACL.request + ECSNS.SolicitudReembolso` con `idPedido` y `comprador`.
2. Verifica que la respuesta contiene `ECSNS.AckActualizacion` con `actualizado = True`.
3. Verifica que `estadoPago` ha pasado a `devuelto`.

Al terminar, el test elimina la entrada de prueba de `informacion_pago.json`.

**Salida esperada:** `[ OK ]` en cada uno de los tres subtests con el estado correspondiente.

**Criterio de superación:** las tres transiciones de estado se producen en orden y el AckActualizacion es recibido en JP12c.

**Comando:**
```bash
python jp9_jp14.py --jp 12
```

---

### JP13 — Pedido con producto de vendedor externo

**Objetivo:** Verificar que el AgenteGestorPedidos identifica correctamente los productos de vendedores externos, los enruta al AgenteVendedorExterno y registra la información de envío en la factura.

**Configuración inicial:** sistema completo arrancado con `start_demo.sh`, incluyendo AgenteVendedorExterno en puerto 9007.

**Flujo detallado:**

1. El test envía una `SolicitudPedido` al GestorPedidos con un producto cuyo campo `vendedor = "VendedorExterno1"` y `gestion_envio = "externo"`.
2. El GestorPedidos detecta que el producto no es de tienda propia y llama a `realizar_pedido_externo`, contactando al AgenteVendedorExterno.
3. El GestorPedidos responde con la factura generada y registra el envío en `envios_vendedor`.
4. El test verifica que la factura contiene el campo `envios_vendedor` con al menos una entrada indicando vendedor, transportista y fecha prevista.

**Salida esperada:**
- `[ OK ] Factura recibida: FAC-XXXXXX`.
- `[ OK ] envios_vendedor presente` con datos del vendedor y fecha de entrega.

**Criterio de superación:** la factura existe y `envios_vendedor` está poblado. Si el AgenteVendedorExterno no está arrancado, el campo aparecerá vacío y se reporta `[FAIL]` con mensaje explicativo.

**Comando:**
```bash
python jp9_jp14.py --jp 13
```

---

### JP14 — Ciclo de experiencia de usuario

**Objetivo:** Verificar el flujo completo del AgenteExperiencia: registro de compra en el historial, almacenamiento de valoraciones, generación de recomendaciones personalizadas y consulta del historial.

**Configuración inicial:** sistema completo arrancado con `start_demo.sh`, incluyendo AgenteExperiencia (puerto 9005) y AgenteComprador (puerto 9001) para que haya catálogo disponible.

**Flujo detallado:**

**JP14a — `CompraFinalizada` actualiza el historial:**
1. El test envía `ACL.inform + ECSNS.CompraFinalizada` al AgenteExperiencia con comprador, pedido, total y productos.
2. Verifica que `data/historial_compras.json` contiene el `pedido_id` de prueba bajo la clave del comprador.

**JP14b — `NuevaValoracion` registra la reseña:**
1. El test envía `ACL.request + ECSNS.NuevaValoracion` con puntuación 5 y comentario.
2. Verifica que `data/listado_opiniones.json` contiene la valoración asociada al pedido de prueba.

**JP14c — `PedirRecomendaciones` devuelve productos:**
1. El test envía `ACL.request + ECSNS.PedirRecomendaciones` con el nombre del comprador de prueba.
2. Verifica que la respuesta contiene al menos un nodo `ECSNS.Producto`.

**JP14d — `ConsultaHistorial` devuelve las compras:**
1. El test envía `ACL.request + ECSNS.ConsultaHistorial` con el nombre del comprador.
2. Verifica que el `pedido_id` añadido en JP14a aparece en el grafo de respuesta.

**Salida esperada:** `[ OK ]` en los cuatro subtests con los datos correspondientes impresos.

**Criterio de superación:** los cuatro subtests pasan. JP14c puede fallar si el catálogo está vacío o si el historial del comprador de prueba no tiene suficientes datos para el motor de recomendaciones; en ese caso se reporta `[FAIL]` con mensaje explicativo.

**Comando:**
```bash
python jp9_jp14.py --jp 14
```

---

## 5. Guía de ejecución para la demo

```bash
# 1. Arrancar el sistema completo
cd Python
bash start_demo.sh

# 2. Esperar ~5 s a que todos los agentes estén registrados
#    Verificar en http://localhost:9000/info que aparecen los 4 transportistas

# 3. JP1–JP6: negociación logística (jp_cliente.py, requiere leer logs)
python jp_cliente.py --jp 1   # JP1: más barato gana (normal)
python jp_cliente.py --jp 2   # JP2: más rápido gana (urgente)
python jp_cliente.py --jp 5   # JP5: tres pedidos simultáneos
python jp_cliente.py --jp 6   # JP6: pedido multi-centro

# 4. Observar en los logs de AgenteLogistico:
#    - Ofertas R1 de cada transportista (precio + fecha)
#    - Cálculo de la contraoferta (90 % del mínimo R1)
#    - Respuestas de R2 (acepta / propone / rechaza)
#    - Línea "GANADOR" con nombre, precio y días

# 5. JP7–JP8: búsqueda y devoluciones (jp7_jp8.py, salida PASS/FAIL automática)
python jp7_jp8.py --jp 7             # JP7: todos los subtests de búsqueda
python jp7_jp8.py --jp 7 --sub a     # JP7a: solo búsqueda por categoría
python jp7_jp8.py --jp 8             # JP8: todos los subtests de devolución
python jp7_jp8.py --jp 8 --sub c     # JP8c: solo rechazo por fuera de plazo

# 6. JP9–JP14: integración, pagos y experiencia (jp9_jp14.py, salida PASS/FAIL automática)
python jp9_jp14.py --jp 9            # JP9:  flujo extremo a extremo (~35 s)
python jp9_jp14.py --jp 10           # JP10: perfiles de modalidad (premium/eco/estandar)
python jp9_jp14.py --jp 10 --sub r   # JP10r: solo RapidExpress (premium)
python jp9_jp14.py --jp 10 --sub e   # JP10e: solo EcoEnvios (eco)
python jp9_jp14.py --jp 10 --sub m   # JP10m: solo MensajeriaPlus (estandar)
python jp9_jp14.py --jp 11           # JP11: asignación de centro por ciudad
python jp9_jp14.py --jp 12           # JP12: ciclo de pagos completo
python jp9_jp14.py --jp 13           # JP13: pedido con vendedor externo
python jp9_jp14.py --jp 14           # JP14: ciclo de experiencia de usuario

# 7. Verificar ficheros de datos
cat Python/data/listado_envios_centro_madrid.json
cat Python/data/informacion_pago.json
cat Python/data/historial_compras.json

# 8. Parar el sistema
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
python3 AgenteTransportista.py --port 9015 \
  --dhost <IP_anfitrión> --dport 9000 \
  --nombre TransportistaExterno --modalidad estandar --open
```

Guía de ontología e interoperabilidad: [Ontologias/INTEROPERABILIDAD_TRANSPORTE.md](Ontologias/INTEROPERABILIDAD_TRANSPORTE.md)
