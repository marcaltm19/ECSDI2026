# Guion de demostración — nivel avanzado

## 1. Arranque

```bash
cd Python && bash start_demo.sh
```

Mostrar http://localhost:9000/info — al menos tres transportistas registrados con `ciudad`.

## 2. Pedido multi-centro

1. Abrir http://localhost:9020/
2. Buscar y añadir al carrito **Laptop Pro 15** (`p001`, Madrid) y **Auriculares Bluetooth** (`p002`, Barcelona).
3. Completar compra con nombre de comprador fijo (ej. `DemoUser`).
4. En **Pedidos / historial**: dos líneas de envío (transportista + centro + fecha distintos).

Alternativa automatizada: `python3 jp_cliente.py --jp 6` y revisar `data/envios.json`.

## 3. Negociación en logs

En la terminal del `AgenteLogistico`, señalar:

- Ofertas R1 de varios transportistas
- Contraoferta (~90 % del mínimo)
- Respuestas aceptar / proponer / rechazar
- Ganador y `accept-proposal`

## 4. Recomendaciones proactivas

- Tras una compra y una búsqueda por categoría, esperar ~30 s o ir a **Recomendaciones**.
- Explicar que el agente de experiencia usa historial de compras y búsquedas.

## 5. Feedback proactivo

- Tras asignar envíos (~45 s en demo si la fecha de entrega es lejana), aparece banner **Valoración solicitada** en la web.
- Enviar valoración en **Valoraciones** (por pedido + producto).

## 6. Nota extra — transportista externo

Con el otro grupo conectado en LAN (ver [Ontologias/INTEROPERABILIDAD_TRANSPORTE.md](Ontologias/INTEROPERABILIDAD_TRANSPORTE.md)):

- Mostrar cuarto transportista en `/info`
- Pedido donde gane el agente externo (`jp_cliente.py --jp 1` o compra UI)

## Capturas sugeridas para el informe / Prometheus

- Pantalla del DS con transportistas
- Historial con dos envíos
- Extracto de log de negociación
- Banner de feedback y página de recomendaciones
- DS con transportista del otro grupo
