# Interoperabilidad — agentes transportistas de otro grupo

Guía para conectar el transportista de otro grupo a nuestra demo usando un
Directory Service compartido y mensajes ACL compatibles.

## 1. Namespace y ontología

```
http://www.semanticweb.org/ecsdi/ontologies/2026/e-shop#
```

Prefijo recomendado: `ecsns:`  
Ontología de referencia: [transporte-interop.ttl](transporte-interop.ttl)

Todos los predicados y clases usados en el protocolo están declarados en esta ontología.
El otro grupo debe usar exactamente los mismos IRIs.

## 2. Cómo se registra un transportista externo

El `AgenteTransportista` se registra en el Directory Service con los siguientes campos:

| Campo | Valor requerido |
|-------|----------------|
| `dso:AgentType` | `ecsns:Ag.Transportista` |
| `foaf:name` | Nombre del transportista (libre) |
| `dso:Address` | `http://<host>:<puerto>/comm` |
| `dso:Uri` | URI del agente |
| `ecsns:ciudad` | Ciudad de cobertura, p. ej. `Madrid` (opcional pero recomendado) |

El campo `ciudad` es importante: el `AgenteLogistico` filtra los transportistas por la ciudad
del centro logístico de origen. Si no se especifica, se incluye en todas las negociaciones.

## 3. Protocolo Contract Net (dos rondas)

### Ronda 1 — Call for Proposals (CFP)

Nuestro `AgenteLogistico` envía:

- **Performative:** `ACL.request`
- **Contenido:** nodo RDF con `rdf:type ecsns:CFP`
- **Propiedades:**
  - `ecsns:tieneDestino` (string) — dirección de entrega
  - `ecsns:tienePrioridad` (string) — `urgente`, `normal` o `economica`

El transportista responde:

- **Performative:** `ACL.propose`
- **Contenido:** nodo RDF con `rdf:type ecsns:Oferta`
- **Propiedades:**
  - `ecsns:tienePrecio` (decimal) — precio ofertado en euros
  - `ecsns:tieneFechaEntrega` (string `YYYY-MM-DD`) — fecha prevista
  - `ecsns:tieneTransportista` (string) — nombre del transportista

### Ronda 2 — Contraoferta

Nuestro logístico envía la mejor contraoferta (≈ 90 % del mínimo de R1):

- **Performative:** `ACL.propose`
- **Contenido:** nodo RDF con `rdf:type ecsns:ContraOferta`
- **Propiedades:**
  - `ecsns:tienePrecio` (decimal) — precio contra-ofertado

El transportista puede responder de tres formas:

| Respuesta | Performative | Contenido |
|-----------|-------------|-----------|
| Acepta la contraoferta | `ACL.inform` | — (sin contenido) |
| Propone un precio intermedio | `ACL.propose` | `ecsns:Oferta` con `tienePrecio` |
| Rechaza | `ACL.reject-proposal` | — |

### Decisión final

El logístico envía a todos los transportistas que participaron en R1:
- `ACL.accept-proposal` al ganador
- `ACL.reject-proposal` al resto

## 4. Arranque en red (ejecución distribuida)

### PC anfitrión (nuestro grupo, ejecuta la demo completa)

```bash
cd Python
export ECSDI_PUBLIC_HOST=<IP_LAN_anfitrión>
export ECSDI_DHOST=localhost
bash start_demo.sh
```

Verificar: `http://<IP_anfitrión>:9000/info` debe listar todos los agentes registrados.

### PC del otro grupo (solo el transportista externo)

El otro grupo arranca únicamente su `AgenteTransportista` apuntando a nuestro DS:

```bash
cd Python
export PYTHONPATH="$(pwd)/..:$PYTHONPATH"
python3 AgenteTransportista.py \
  --port 9013 \
  --dhost <IP_anfitrión> \
  --dport 9000 \
  --nombre TransportistaExterno \
  --ciudad Madrid \
  --open
```

Ajustar `--ciudad` según el centro logístico al que quieran cubrir.  
Comprobar que aparece en `http://<IP_anfitrión>:9000/info`.

### Prueba de integración

```bash
# Desde el PC anfitrión (con la demo corriendo)
python3 jp_cliente.py --jp 1 --host <IP_anfitrión>
```

En los logs del `AgenteLogistico` debe aparecer el transportista externo como candidato
y, si su oferta es la mejor, como ganador.

## 5. Puertos que deben estar abiertos en el firewall (LAN)

| Puerto | Servicio |
|--------|----------|
| 9000 | Directory Service |
| 9003 | AgenteLogistico |
| 9010–9013 | Transportistas |
| 9020 | AgenteUsuario (UI) |

## 6. Checklist antes de la demo conjunta

- [ ] Mismo namespace (`http://www.semanticweb.org/ecsdi/ontologies/2026/e-shop#`)
- [ ] Mismos nombres de clases: `CFP`, `Oferta`, `ContraOferta`
- [ ] Mismos predicados: `tienePrecio`, `tieneDestino`, `tienePrioridad`, `tieneFechaEntrega`, `tieneTransportista`
- [ ] IP del anfitrión y puerto 9000 accesibles desde el otro PC
- [ ] `--open` activado en todos los agentes que reciben conexiones externas
- [ ] `ECSDI_PUBLIC_HOST` apunta a la IP de red (no `localhost`)
- [ ] El transportista externo aparece en `/info` antes de lanzar el pedido de prueba
