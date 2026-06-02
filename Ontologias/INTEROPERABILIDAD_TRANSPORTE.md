# Acuerdo de interoperabilidad — agentes transportistas

Documento para coordinar con otro grupo: un único Directory Service y mensajes ACL compatibles.

## 1. Namespace

```
http://www.semanticweb.org/ecsdi/ontologies/2026/e-shop#
```

Prefijo recomendado: `ecsns:`

Ontología de referencia: [transporte-interop.ttl](transporte-interop.ttl)

## 2. Registro en el Directory Service

| Campo | Valor |
|-------|--------|
| `dso:AgentType` | `ecsns:Ag.Transportista` |
| `foaf:name` | Nombre del transportista |
| `dso:Address` | URL `http://<host>:<puerto>/comm` |
| `dso:Uri` | URI del agente |
| `ecsns:ciudad` | (opcional) Madrid, Barcelona, Valencia… |

## 3. Protocolo Contract Net (dos rondas)

### Ronda 1 — CFP

- **Performative:** `ACL.request`
- **Contenido:** `rdf:type ecsns:CFP`
- **Propiedades:** `ecsns:tieneDestino`, `ecsns:tienePrioridad`

**Respuesta del transportista:** `ACL.propose` con `rdf:type ecsns:Oferta` y `tienePrecio`, `tieneFechaEntrega`, `tieneTransportista`.

### Ronda 2 — Contraoferta

- **Performative:** `ACL.propose`
- **Contenido:** `rdf:type ecsns:ContraOferta`
- **Propiedades:** `ecsns:tienePrecio` (≈ 90 % del mínimo de la ronda 1)

**Respuestas posibles:**

| Respuesta | Performative | Contenido |
|----------|--------------|-----------|
| Acepta | `inform` | — |
| Propone otro precio | `propose` | `ecsns:Oferta` (precio > contraoferta y < oferta R1) |
| Rechaza | `reject-proposal` | — |

### Decisión final

El logístico envía `accept-proposal` al ganador y `reject-proposal` al resto.

## 4. Prueba en red (JP4)

**PC anfitrión** (grupo que hace la demo completa):

```bash
cd Python
export ECSDI_PUBLIC_HOST=<IP_LAN_anfitrión>
export ECSDI_DHOST=localhost
bash start_demo.sh
```

**PC del otro grupo** (solo transportista):

```bash
cd Python
export PYTHONPATH="$(pwd)/..:$PYTHONPATH"
python3 AgenteTransportista.py --port 9013 --dport 9000 --dhost <IP_anfitrión> \
  --nombre TransportistaExterno --ciudad Madrid --open
```

Comprobar: `http://<IP_anfitrión>:9000/info` debe listar el transportista externo.

Ejecutar pedido de prueba:

```bash
python3 jp_cliente.py --jp 1
# o compra en http://<IP_anfitrión>:9020/
```

## 5. Puertos a abrir en firewall (LAN)

| Puerto | Servicio |
|--------|----------|
| 9000 | Directory Service |
| 9003 | AgenteLogistico |
| 9010–9013 | Transportistas |
| 9020 | AgenteUsuario (UI) |

## 6. Checklist reunión entre grupos

- [ ] Mismo namespace y clases (`CFP`, `Oferta`, `ContraOferta`)
- [ ] Mismos nombres de predicados (`tienePrecio`, `tieneDestino`, …)
- [ ] IP del anfitrión y puerto del DS acordados
- [ ] Nombre y `ciudad` del transportista externo definidos
- [ ] Prueba JP4 registrada (captura de `/info` + log del ganador)
