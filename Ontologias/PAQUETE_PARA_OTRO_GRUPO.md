# Interoperabilidad — agente transportista (ECSDI 2026)

Documento para el **otro grupo** (nota extra §3.5): intercambiamos el script Python del agente transportista y la **ontología de transporte**; cada grupo adapta su copia y en la demo un equipo aloja el Directory Service y el otro conecta su transportista al mismo DS.

---

## Qué os enviamos (y qué os enviáis a nosotros)

Según el criterio del curso, basta con intercambiar **dos cosas**:

| Qué | Fichero nuestro | Qué hacéis vosotros |
|-----|-----------------|---------------------|
| Script del agente transportista | `Python/AgenteTransportista.py` | Nos mandáis **vuestro** `AgenteTransportista.py` (o equivalente) |
| Ontología de transporte | `Ontologias/transporte-interop.ttl` | Nos mandáis **vuestra** ontología de transporte (TTL/OWX o EL MISMO `transporte-interop.ttl` si acordáis el vocabulario) |

No hace falta el proyecto completo ni el resto de agentes (comprador, logístico, UI, etc.).

### Dependencias mínimas para ejecutar nuestro script

Si usáis nuestro `AgenteTransportista.py` tal cual (o como base), necesitáis además lo que ya usa el material del curso:

- `Python/ontologia.py` (namespace RDF)
- carpeta `AgentUtil/` (mensajes ACL, registro en DS, Flask)
- `requirements.txt` del repo (`rdflib`, `flask`, …)

Eso no sustituye el intercambio acordado: lo importante para el profesor es el **transportista + ontología de transporte**.

---

## Acuerdo previo (rellenad juntos)

| Campo | Grupo anfitrión (DS + demo) | Grupo visitante (transportista) |
|-------|----------------------------|----------------------------------|
| Nombre del grupo | | |
| IP LAN del PC anfitrión | `_______________` | — |
| Puerto DS | `9000` | — |
| Nombre del transportista | | |
| Puerto del transportista | 9010–9012 (nosotros) | p. ej. `9013` |
| Ciudad (`--ciudad`) | | |

La **ciudad** debe coincidir con el centro del pedido de prueba (p. ej. `Madrid` si el envío sale de Madrid).

---

## Ontología de transporte (`transporte-interop.ttl`)

**Namespace (obligatorio en ambos grupos):**

```
http://www.semanticweb.org/ecsdi/ontologies/2026/e-shop#
```

Prefijo: `ecsns:`

| Clase / predicado | Uso en el protocolo |
|-------------------|---------------------|
| `ecsns:CFP` | Solicitud de oferta (ronda 1): `tieneDestino`, `tienePrioridad` (`normal`, `urgente`, `economica`) |
| `ecsns:Oferta` | Respuesta con `tienePrecio`, `tieneFechaEntrega` (`YYYY-MM-DD`), `tieneTransportista` |
| `ecsns:ContraOferta` | Contraoferta del logístico: solo `tienePrecio` |
| `ecsns:ciudad` | Al registrarse en el DS (cobertura geográfica) |
| `ecsns:Ag.Transportista` | Tipo de agente en el Directory Service |

El detalle formal está en **`transporte-interop.ttl`**. Adaptad vuestro agente para usar **los mismos IRIs**; si cambiáis nombres de clase, avisad antes de la demo.

---

## Qué hace nuestro `AgenteTransportista.py` (para que adaptéis el vuestro)

1. Se **registra** en el DS con `Ag.Transportista`, nombre, dirección `/comm` y opcionalmente `ciudad`.
2. **Ronda 1:** recibe `ACL.request` + `CFP` → responde `ACL.propose` + `Oferta` (precio y fecha según prioridad).
3. **Ronda 2:** recibe `ACL.propose` + `ContraOferta` → responde `inform` (acepta), `propose` + `Oferta` (nuevo precio) o `reject-proposal`.
4. **Final:** recibe `accept-proposal` o `reject-proposal` del logístico.

Parámetros útiles al arrancar:

```text
--port      Puerto HTTP del agente
--dhost     IP del PC donde corre el DS del otro grupo
--dport     Puerto del DS (9000)
--nombre    Nombre del transportista (debe ser distinto por instancia)
--ciudad    Madrid | Barcelona | Valencia | ...
--open      Escuchar en 0.0.0.0 (necesario si el logístico está en otro PC)
--precio-factor   Opcional: multiplicador de precios (para la demo)
```

---

## Día de la demo

### Grupo que aloja el sistema (anfitrión)

```bash
cd Python
export ECSDI_PUBLIC_HOST=<IP_LAN_de_este_PC>
export ECSDI_DHOST=localhost
bash start_demo.sh
```

Comprobar: `http://<IP_LAN>:9000/info`

### Grupo que aporta el transportista externo

```bash
cd Python
export PYTHONPATH="$(pwd)/..:$PYTHONPATH"
export ECSDI_PUBLIC_HOST=<IP_LAN_de_vuestro_PC>
python3 AgenteTransportista.py \
  --port 9013 \
  --dhost <IP_LAN_anfitrión> \
  --dport 9000 \
  --nombre <VuestroNombre> \
  --ciudad <CiudadAcordada> \
  --open
```

Comprobar en el anfitrión: vuestro agente aparece en `http://<IP_anfitrión>:9000/info`.

### Prueba rápida

- Pedido desde la UI del anfitrión (`http://<IP_anfitrión>:9020/`) o `jp_cliente.py --jp 1`.
- En los logs del logístico deben salir vuestras ofertas; si ganáis, recibís `accept-proposal`.

### Firewall (LAN)

| PC | Puertos a abrir |
|----|-----------------|
| Anfitrión | 9000 (DS), 9003 (logístico), 9020 (UI, opcional) |
| Visitante | Puerto de vuestro transportista (p. ej. 9013) |

---

## Qué confirmar por escrito antes del día D

- [ ] Mismo namespace y vocabulario de `transporte-interop.ttl` (o diferencias acordadas)
- [ ] Tabla de acuerdo rellena (IPs, nombres, puertos, ciudad)
- [ ] Transportista externo visible en `/info`
- [ ] Una prueba de pedido sin errores de conexión

---

## Resumen para el informe

Interoperabilidad demostrada con: intercambio de **`AgenteTransportista.py`** + **ontología de transporte** (`transporte-interop.ttl`), registro en un DS común y participación de ambos transportistas en la negociación Contract Net.
