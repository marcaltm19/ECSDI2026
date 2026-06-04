# Juegos de Prueba — ECSDI 2026

Este documento describe los juegos de prueba del sistema multiagente de e-commerce con gestión logística distribuida. Para ejecutarlos, primero arranca el sistema con `cd Python && bash start_demo.sh` y luego usa `python jp_cliente.py --jp <N>`.

---

## JP1 — Selección del transportista más barato

**Propósito:** Verificar que el agente logístico implementa correctamente el protocolo Contract Net y selecciona la propuesta con menor precio entre varios transportistas cuando la prioridad es `normal`.

**Entrada:**
- 1 pedido con prioridad `normal`, destino `Barcelona`, 1 producto de 5 kg.
- 3 transportistas activos (RapidExpress, EcoEnvios, MensajeriaPlus) con rangos de precio aleatorios dentro del rango `normal` (8–15€).

**Salida esperada:**
- El transportista con el precio más bajo recibe `ACL.accept-proposal`.
- Los otros dos reciben `ACL.reject-proposal`.
- En `data/envios.json` aparece un registro con el nombre del transportista ganador.

**Lo que ocurre durante la ejecución:**
1. El AgenteLogistico consulta el DirectoryService para obtener las direcciones de los transportistas.
2. Envía un mensaje CFP (`ECSNS.CFP`) con destino y prioridad a cada transportista.
3. Cada transportista responde con un `ACL.propose` conteniendo precio y fecha de entrega.
4. El logístico compara precios y acepta la oferta más barata, rechazando el resto.
5. Se registra el envío en `data/envios.json`.

**Comando:**
```bash
python jp_cliente.py --jp 1
```

---

## JP2 — Prioridad urgente: gana el más rápido

**Propósito:** Verificar que cuando la prioridad del pedido es `urgente`, el criterio de selección cambia de precio mínimo a días de entrega mínimos, priorizando la rapidez sobre el coste.

**Entrada:**
- 1 pedido con prioridad `urgente`, destino `Madrid`, 1 producto de 2 kg.
- 3 transportistas activos. Con prioridad `urgente`, cada transportista genera precios en el rango 15–30€ y plazos de 1–2 días.

**Salida esperada:**
- El transportista con el menor número de días de entrega recibe `ACL.accept-proposal`.
- Si dos transportistas ofrecen el mismo plazo, gana el de menor precio como desempate.
- El log del AgenteLogistico muestra `GANADOR (urgente): <nombre>` con el menor número de días.

**Lo que ocurre durante la ejecución:**
1. El pedido llega con `prioridad = urgente`.
2. El AgenteLogistico ejecuta `escoger_mejor_oferta(ofertas, 'urgente')`, que ordena por días en lugar de por precio.
3. Se acepta la oferta con la fecha de entrega más próxima.
4. El envío queda registrado con la fecha más temprana posible.

**Comando:**
```bash
python jp_cliente.py --jp 2
```

---

## JP3 — Ningún transportista disponible (caso límite)

**Propósito:** Verificar que el sistema es robusto ante la ausencia de transportistas registrados: no se bloquea, no lanza excepción y responde de forma controlada al agente que realizó el pedido.

**Entrada:**
- 1 pedido con prioridad `normal`, destino `Valencia`.
- **Ningún** AgenteTransportista registrado en el DirectoryService.

**Para prepararlo:**
```bash
# Arrancar solo DS y Logistico, sin transportistas
python DirectoryService.py --port 9000 &
python AgenteLogistico.py --port 9003 --dport 9000 &
```

**Salida esperada:**
- El log del AgenteLogistico muestra `No hay transportistas en el DS`.
- En `data/envios.json` aparece un registro con `transportista: "Desconocido"` y una fecha calculada por defecto (+3 días).
- El sistema sigue respondiendo a peticiones posteriores sin necesidad de reinicio.

**Lo que ocurre durante la ejecución:**
1. El logístico consulta el DirectoryService y recibe lista vacía de transportistas.
2. La función `negociar_transporte` detecta que `transportistas_addr` está vacío y devuelve el valor de fallback.
3. Se genera el envío con datos por defecto y se registra.

**Comando:**
```bash
python jp_cliente.py --jp 3
```

---

## JP4 — Integración con agente externo (otro grupo)

**Propósito:** Verificar la interoperabilidad entre sistemas de distintos grupos usando la ontología acordada. El agente transportista externo debe participar en la negociación Contract Net y ser seleccionado si ofrece la mejor propuesta.

**Requisitos previos:**
- Servidor XMPP o DirectoryService compartido con el otro grupo.
- El AgenteTransportista externo está registrado en el mismo DirectoryService con tipo `ECSNS['Ag.Transportista']`.
- Ambos grupos usan el namespace `http://www.semanticweb.org/ecsdi/ontologies/2026/e-shop#` y los predicados: `tienePrecio`, `tieneDestino`, `tienePrioridad`, `tieneTransportista`, `tieneFechaEntrega`.

**Entrada:**
- 1 pedido con prioridad `normal`, destino `Zaragoza`.
- 2 transportistas propios + 1 transportista externo del otro grupo (registrado en el DS compartido con precio inferior al de los propios).

**Salida esperada:**
- El transportista externo recibe `ACL.accept-proposal`.
- Los transportistas propios reciben `ACL.reject-proposal`.
- El envío registrado en `data/envios.json` muestra el nombre del transportista externo.

**Lo que ocurre durante la ejecución:**
1. El logístico obtiene del DS la lista de todos los transportistas (propios + externo).
2. Envía el mismo CFP a todos por igual, sin distinguir si son locales o externos.
3. El transportista externo responde con un PROPOSE usando la ontología acordada.
4. El logístico evalúa todas las propuestas con el mismo criterio y selecciona la mejor.

**Nota:** Este JP requiere coordinación previa con el otro grupo para registrar su agente y verificar conectividad de red.

---

## JP5 — Múltiples pedidos simultáneos

**Propósito:** Verificar que el sistema gestiona correctamente varios pedidos concurrentes sin que las negociaciones se interfieran entre sí, y que cada pedido se asigna de forma independiente al transportista correcto según su prioridad.

**Entrada:**
- 3 pedidos enviados simultáneamente:
  - Pedido 1: prioridad `normal`, destino `Barcelona`, 1 producto.
  - Pedido 2: prioridad `urgente`, destino `Madrid`, 3 productos.
  - Pedido 3: prioridad `economica`, destino `Sevilla`, 1 producto.
- 3 transportistas activos.

**Salida esperada:**
- En `data/pedidos.json` aparecen los 3 pedidos ordenados por prioridad (urgente → normal → economica).
- En `data/envios.json` aparecen 3 registros distintos, uno por pedido, cada uno con su transportista asignado.
- El pedido urgente tiene la fecha de entrega más próxima.
- No hay mezcla de propuestas entre pedidos.

**Lo que ocurre durante la ejecución:**
1. Los 3 pedidos llegan casi simultáneamente al AgenteLogistico.
2. Se almacenan y ordenan en `data/pedidos.json` por prioridad.
3. Cada 20 segundos (tick), el logístico procesa todos los pedidos pendientes secuencialmente.
4. Para cada pedido se realiza una negociación Contract Net independiente.
5. Se generan 3 envíos separados en `data/envios.json`.

**Comando:**
```bash
python jp_cliente.py --jp 5
```

---

## JP6 — Pedido multi-centro (dos sub-envíos)

**Propósito:** Verificar que un pedido con productos de centros logísticos distintos genera varias negociaciones y el usuario recibe varios transportistas y fechas.

**Entrada:**
- 1 pedido con productos `p001` (centro Madrid) y `p002` (centro Barcelona).
- 3 transportistas activos con filtrado por ciudad.

**Salida esperada:**
- En `data/envios.json`, dos registros con el mismo `pedido_id` y `centro_logistico` distinto.
- En el historial de la UI, dos líneas de envío por factura.

**Comando:**
```bash
python jp_cliente.py --jp 6
```

---

## JP7 — Búsqueda de productos con filtros

**Propósito:** Verificar que el AgenteComprador aplica correctamente los filtros de búsqueda (categoría, precio máximo y valoración mínima) sobre el catálogo de productos, devolviendo únicamente los artículos que cumplen todas las condiciones especificadas, y que responde con una lista vacía cuando ningún producto satisface los criterios sin producir errores.

**Entrada:**
- El catálogo de productos cargado en `data/listado_productos_detallados.json` (incluye productos de distintas categorías, precios y valoraciones).
- Cinco búsquedas independientes enviadas como mensajes ACL `ECSNS.Busqueda` al AgenteComprador:
  - JP7a: filtro por categoría `Electronica` (sin restricción de precio ni valoración).
  - JP7b: precio máximo de 50 €, sin otros filtros.
  - JP7c: valoración mínima de 4.5 estrellas, sin otros filtros.
  - JP7d: combinación de los tres filtros: categoría `Electronica`, precio máximo 500 €, valoración mínima 4.0.
  - JP7e: precio máximo de 0.01 € (filtro imposible, sin resultados esperados).

**Salida esperada:**
- JP7a: todos los productos devueltos tienen `categoria = Electronica`; cualquier producto de otra categoría indica fallo.
- JP7b: todos los productos devueltos tienen `precio ≤ 50`; cualquier producto más caro indica fallo.
- JP7c: todos los productos devueltos tienen `valoracion ≥ 4.5`; cualquier resultado inferior indica fallo.
- JP7d: todos los productos devueltos cumplen simultáneamente los tres filtros; cualquier incumplimiento indica fallo.
- JP7e: la respuesta contiene una lista de productos vacía; el agente no lanza ninguna excepción.
- En todos los casos el script muestra `[ OK ]` si el resultado coincide con lo esperado, o `[FAIL]` con detalle del error.

**Lo que ocurre durante la ejecución:**
1. El script envía un mensaje ACL `ECSNS.Busqueda` al AgenteComprador con los parámetros de filtrado.
2. El AgenteComprador carga el catálogo desde `data/listado_productos_detallados.json` y recorre cada producto aplicando los filtros recibidos.
3. Los productos que superan todos los filtros se añaden a un grafo RDF y se devuelven en la respuesta ACL.
4. El AgenteComprador notifica la búsqueda al AgenteExperiencia (`ECSNS.RegistroBusqueda`) para actualizar el historial del usuario.
5. El script parsea el grafo RDF de la respuesta, comprueba atributo a atributo que cada producto cumple los criterios y presenta el resultado del test por pantalla.

---

## JP8 — Devolución de un pedido

**Propósito:** Verificar el flujo completo de devolución del AgenteDevolucion: que acepta automáticamente devoluciones por producto defectuoso, que acepta devoluciones dentro del plazo legal de 15 días, y que rechaza correctamente los casos de fuera de plazo, factura inexistente y solicitud duplicada sobre una factura ya devuelta.

**Entrada:**
- Una factura de prueba (`FAC-JPTEST-001`, comprador `TestUser`, producto `p001 – Laptop Pro 15`) insertada directamente en `data/listado_facturas.json` antes de ejecutar los subtests.
- Cinco solicitudes de devolución enviadas como mensajes ACL `ECSNS.SolicitudDevolucion` al AgenteDevolucion:
  - JP8a: factura `FAC-JPTEST-001`, razón `"El producto llegó defectuoso"`, recepción hace 3 días.
  - JP8b: factura `FAC-JPTEST-001`, razón neutra `"No me convence"`, recepción hace 7 días.
  - JP8c: factura `FAC-JPTEST-001`, razón neutra `"Ya no me gusta"`, recepción hace 20 días.
  - JP8d: factura `FAC-NOEXISTE-999` (no registrada en el sistema), razón cualquiera.
  - JP8e: factura `FAC-JPTEST-001` previamente marcada como `devuelta = true`, razón `"defectuoso"`.

**Salida esperada:**
- JP8a: devolución **aceptada** — la palabra clave `"defectuoso"` activa la aprobación automática independientemente del plazo; empresa asignada: `MensajeriaRapida S.L.`
- JP8b: devolución **aceptada** — 7 días está dentro del plazo de 15 días; empresa asignada: `MensajeriaEstandar S.A.`
- JP8c: devolución **rechazada** — han transcurrido 20 días, superando el plazo establecido.
- JP8d: devolución **rechazada** — el AgenteGestorPedidos no encuentra la factura y devuelve verificación negativa.
- JP8e: devolución **rechazada** — el AgenteGestorPedidos detecta que la factura ya fue devuelta con anterioridad.
- En todos los casos el script muestra `[ OK ]` si el resultado coincide con lo esperado, o `[FAIL]` con el motivo recibido.
- Las devoluciones aceptadas quedan registradas en `data/listado_devoluciones.json`.

**Lo que ocurre durante la ejecución:**
1. El script inserta la factura de prueba `FAC-JPTEST-001` en `data/listado_facturas.json`.
2. Para cada subtest, envía un mensaje `ECSNS.SolicitudDevolucion` al AgenteDevolucion con la factura, la razón y la fecha de recepción correspondientes.
3. El AgenteDevolucion consulta al AgenteGestorPedidos mediante `ECSNS.VerificarCompra` para comprobar que la factura existe, pertenece al comprador indicado y no ha sido devuelta antes.
4. Si la verificación es positiva, evalúa la razón: primero busca palabras clave de producto defectuoso (`defectuoso`, `roto`, `dañado`, etc.); si no hay coincidencia, calcula los días transcurridos desde la fecha de recepción y los compara con el plazo de 15 días.
5. Si la devolución se acepta, el AgenteDevolucion notifica al AgenteGestorPedidos para marcar la factura como devuelta, al AgenteExperiencia para eliminar la compra del historial del usuario, y al AgenteUsuario con el ID de devolución y la empresa de mensajería asignada.
6. El resultado (aceptada o rechazada, con su motivo) se persiste en `data/listado_devoluciones.json` y se devuelve al script en un grafo RDF.
7. El script parsea el campo `aceptada` de la respuesta, lo compara con el valor esperado y muestra el veredicto.
8. Al finalizar todos los subtests, el script elimina la factura de prueba del sistema.

---

## Guía de ejecución rápida para la demo

```bash
# 1. Arrancar el sistema completo
cd Python
bash start_demo.sh

# 2. Esperar ~3s a que todos los agentes estén registrados

# 3. Ejecutar el juego de prueba elegido
python jp_cliente.py --jp 1   # o --jp 2, 3, 5, 6
python jp7_jp8.py --jp 7      # o --jp 8

# 4. Esperar ~20s y observar los logs en pantalla
# Los logs muestran: ofertas recibidas, ganador seleccionado, envío registrado

# 5. Verificar resultado
cat data/listado_envios.json
cat data/listado_devoluciones.json

# 6. Parar el sistema
bash stop_demo.sh
```

### Para JP4 (agente externo), desde el PC del otro grupo:
```bash
# Anfitrión: export ECSDI_PUBLIC_HOST=<IP_LAN> && bash start_demo.sh
# Otro grupo:
export PYTHONPATH="$(pwd)/..:$PYTHONPATH"
python3 AgenteTransportista.py --port 9013 --dhost <IP_anfitrión> --dport 9000 \
  --nombre TransportistaExterno --ciudad Madrid --open
```

Guía completa: [Ontologias/INTEROPERABILIDAD_TRANSPORTE.md](Ontologias/INTEROPERABILIDAD_TRANSPORTE.md)
