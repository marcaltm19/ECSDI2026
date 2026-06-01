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

## Guía de ejecución rápida para la demo

```bash
# 1. Arrancar el sistema completo
cd Python
bash start_demo.sh

# 2. Esperar ~3s a que todos los agentes estén registrados

# 3. Ejecutar el juego de prueba elegido
python jp_cliente.py --jp 1   # o --jp 2, 3, 5

# 4. Esperar ~20s y observar los logs en pantalla
# Los logs muestran: ofertas recibidas, ganador seleccionado, envio registrado

# 5. Verificar resultado
cat data/envios.json

# 6. Parar el sistema
bash stop_demo.sh
```

### Para JP4 (agente externo), desde el PC del otro grupo:
```bash
# El otro grupo ejecuta su transportista apuntando a tu DS
python AgenteTransportista.py --port 9013 --dhost <IP_PC1> --dport 9000 --nombre TransportistaExterno
```
