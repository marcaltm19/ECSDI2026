# Guion de demostración — nivel avanzado

## 1. Arranque

```bash
cd Python && bash start_demo.sh
```

Mostrar `http://localhost:9000/info` — al menos tres transportistas registrados con `ciudad`.

## 2. Inicio de sesión (usuario persistente)

1. Abrir `http://localhost:9020/`
2. Introducir un nombre de usuario (p. ej. `DemoUser`). No se necesita contraseña.
3. El nombre queda guardado en la sesión: todas las acciones (compra, devolución, valoración, recomendaciones) se asocian automáticamente a ese usuario.
4. Para cambiar de usuario, pulsar **Salir** en la barra superior.

## 3. Pedido multi-centro

1. Ir a **Catálogo** y añadir al carrito **Laptop Pro 15** (`p001`, centro Madrid) y **Auriculares Bluetooth** (`p002`, centro Barcelona).
2. Pulsar **Carrito → Tramitar pedido**. El comprador ya está relleno con el usuario de la sesión.
3. En **Pedidos**: dos líneas de envío (transportista + centro + fecha distintos).

Alternativa: `python3 jp_cliente.py --jp 6` y revisar `data/listado_envios.json`.

## 4. Negociación en logs

En la terminal del `AgenteLogistico`, señalar:

- Ofertas R1 de varios transportistas
- Contraoferta (≈ 90 % del mínimo R1)
- Respuestas aceptar / proponer / rechazar
- Ganador y `accept-proposal`

## 5. Recomendaciones proactivas

- Tras una compra y una búsqueda por categoría, ir a **Recomendaciones**.
- El agente de experiencia usa historial de compras y búsquedas; el usuario de la sesión se pre-rellena automáticamente.
- Las recomendaciones también aparecen de forma proactiva (~30 s) sin necesidad de solicitarlas.

## 6. Feedback proactivo

- Tras asignar envíos (~20 s en demo), aparece el banner **Valoración solicitada** en cualquier página.
- Pulsar el enlace para valorar el producto en **Valoraciones** (por pedido + producto).

## 7. Devolución

1. Ir a **Devoluciones**. El nombre de comprador se pre-rellena con el usuario de sesión.
2. Seleccionar una factura elegible e introducir el motivo y la fecha de recepción.
3. Si se acepta: AgenteGestorPedidos marca la factura como devuelta, AgenteExperiencia elimina la compra del historial y se notifica a la tienda externa si corresponde.

## 8. Nota extra — transportista externo

Con el otro grupo conectado en LAN (ver [Ontologias/INTEROPERABILIDAD_TRANSPORTE.md](Ontologias/INTEROPERABILIDAD_TRANSPORTE.md)):

- Mostrar el cuarto transportista en `/info`
- Ejecutar JP1 o una compra desde la UI y mostrar en los logs que el agente externo gana la negociación

## Capturas sugeridas para el informe

- DS (`/info`) con tres o más transportistas
- Historial con dos sub-envíos de centros distintos
- Extracto de log de negociación (R1, contraoferta, ganador)
- Banner de feedback y página de recomendaciones
- DS con el transportista del otro grupo
