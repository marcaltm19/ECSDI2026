#!/usr/bin/env python3
"""
jp9_jp14.py  —  Juegos de prueba JP9–JP14

    python jp9_jp14.py --jp 9              # Flujo extremo a extremo
    python jp9_jp14.py --jp 10             # Todos los perfiles de transportista
    python jp9_jp14.py --jp 10 --sub r     # Solo RapidExpress (1 día siempre)
    python jp9_jp14.py --jp 10 --sub e     # Solo EcoEnvios (precio bajo, +2 días)
    python jp9_jp14.py --jp 10 --sub m     # Solo MensajeriaPlus (plazo intermedio)
    python jp9_jp14.py --jp 11             # Asignación de centro por ciudad
    python jp9_jp14.py --jp 12             # Flujo de pagos completo
    python jp9_jp14.py --jp 13             # Pedido con producto de vendedor externo
    python jp9_jp14.py --jp 14             # Ciclo de experiencia y recomendaciones
    python jp9_jp14.py --jp 9 --host 192.168.1.5  # apuntar a otro host

Requiere que start_demo.sh esté corriendo.
"""

import argparse
import json
import os
import sys
import time
import uuid
from datetime import date, datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from rdflib import Graph, Literal, Namespace
from rdflib.namespace import RDF
from AgentUtil.ACL import ACL
from AgentUtil.ACLMessages import build_message
from ontologia import ECSNS
import requests as http_requests

agn = Namespace('http://www.agentes.org#')
mss_cnt = 0

# URLs de agentes — sobrescritas por --host en main()
GESTOR_URL           = 'http://localhost:9002/comm'
EXPERIENCIA_URL      = 'http://localhost:9005/comm'
VENDEDOR_URL         = 'http://localhost:9007/comm'
T_RAPIDEXPRESS_URL   = 'http://localhost:9010/comm'
T_ECOENIVIOS_URL     = 'http://localhost:9011/comm'
T_MENSAJERIAPLUS_URL = 'http://localhost:9012/comm'
GESTORPAGOS_URL      = 'http://localhost:9014/comm'

DATA_DIR       = os.path.join(os.path.dirname(__file__), 'data')
FACTURAS_PATH  = os.path.join(DATA_DIR, 'listado_facturas.json')
PAGOS_PATH     = os.path.join(DATA_DIR, 'informacion_pago.json')
HISTORIAL_PATH = os.path.join(DATA_DIR, 'historial_compras.json')
OPINIONES_PATH = os.path.join(DATA_DIR, 'listado_opiniones.json')
CENTROS_PATH   = os.path.join(DATA_DIR, 'centros_logisticos.json')

PASS = '[ OK ]'
FAIL = '[FAIL]'
SKIP = '[SKIP]'

TEST_COMPRADOR = 'TestUserJP'


# ── utilidades comunes ─────────────────────────────────────────────────────────

def _cnt():
    global mss_cnt
    c = mss_cnt
    mss_cnt += 1
    return c


def _get(url, msg_graph, timeout=12):
    """Envía un mensaje ACL y devuelve el grafo de respuesta, o None si falla."""
    try:
        r = http_requests.get(
            url,
            params={'content': msg_graph.serialize(format='xml')},
            timeout=timeout,
        )
        r.raise_for_status()
        gr = Graph()
        gr.parse(data=r.text, format='xml')
        return gr
    except Exception as e:
        print(f'  [ERROR HTTP] {e}')
        return None


def _inform(url, msg_graph):
    """Envía un ACL.inform y devuelve True si el servidor respondió 2xx."""
    try:
        r = http_requests.get(
            url,
            params={'content': msg_graph.serialize(format='xml')},
            timeout=12,
        )
        r.raise_for_status()
        return True
    except Exception as e:
        print(f'  [ERROR HTTP] {e}')
        return False


def _load_json(path, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return default


def _save_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w') as f:
        json.dump(data, f, indent=2)


def _pedido_msg(pedido_id, ciudad, prioridad, metodo_pago, productos):
    """Construye el mensaje RDF SolicitudPedido para AgenteGestorPedidos."""
    cnt = _cnt()
    g = Graph()
    g.bind('ecsns', ECSNS)
    sol = agn[f'sol-{pedido_id}']
    ped = agn[f'ped-{pedido_id}']
    g.add((sol, RDF.type,          ECSNS.SolicitudPedido))
    g.add((sol, ECSNS.tienePedido, ped))
    g.add((ped, RDF.type,          ECSNS.Pedido))
    g.add((ped, ECSNS.idPedido,    Literal(pedido_id)))
    g.add((ped, ECSNS.comprador,   Literal(TEST_COMPRADOR)))
    g.add((ped, ECSNS.direccion,   Literal('Calle Test 1')))
    g.add((ped, ECSNS.ciudad,      Literal(ciudad)))
    g.add((ped, ECSNS.prioridad,   Literal(prioridad)))
    g.add((ped, ECSNS.metodoPago,  Literal(metodo_pago)))
    for i, p in enumerate(productos):
        pn = agn[f'{pedido_id}-p{i}']
        g.add((ped, ECSNS.tieneProducto, pn))
        g.add((pn, ECSNS.idProducto,   Literal(p['id'])))
        g.add((pn, ECSNS.nombre,       Literal(p.get('nombre', p['id']))))
        g.add((pn, ECSNS.precio,       Literal(float(p.get('precio', 10.0)))))
        g.add((pn, ECSNS.cantidad,     Literal(int(p.get('cantidad', 1)))))
        g.add((pn, ECSNS.peso,         Literal(float(p.get('peso', 1.0)))))
        g.add((pn, ECSNS.vendedor,     Literal(p.get('vendedor', 'tienda'))))
        g.add((pn, ECSNS.gestionEnvio, Literal(p.get('gestion_envio', 'tienda'))))
    return build_message(g, perf=ACL.request,
                         sender=agn.ClienteTest, receiver=agn.AgenteGestorPedidos,
                         content=sol, msgcnt=cnt)


def _cfp_msg(prioridad):
    """Construye un mensaje CFP para enviarlo directamente a un AgenteTransportista."""
    cnt = _cnt()
    g = Graph()
    g.bind('ecsns', ECSNS)
    node = agn[f'cfp-{cnt}']
    g.add((node, RDF.type,             ECSNS.CFP))
    g.add((node, ECSNS.tienePrioridad, Literal(prioridad)))
    g.add((node, ECSNS.tienePeso,      Literal(0.0)))
    return build_message(g, perf=ACL.request,
                         sender=agn.ClienteTest, receiver=agn.AgenteTransportista,
                         content=node, msgcnt=cnt)


def _factura_id_de_respuesta(gr):
    """Extrae el idFactura del grafo de respuesta del GestorPedidos."""
    if gr is None:
        return None
    for s in gr.subjects(RDF.type, ECSNS.Factura):
        fid = str(gr.value(s, ECSNS.idFactura) or '')
        if fid:
            return fid
    return None


def _poll_envios_logistico(factura_id, timeout=35):
    """Espera hasta que listado_facturas.json tenga envios_logistico para la factura."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        for fac in _load_json(FACTURAS_PATH, []):
            if fac.get('id') == factura_id and fac.get('envios_logistico'):
                return fac['envios_logistico']
        time.sleep(2)
    return None


def _limpiar_factura(factura_id):
    facturas = [f for f in _load_json(FACTURAS_PATH, []) if f.get('id') != factura_id]
    _save_json(FACTURAS_PATH, facturas)


def _limpiar_pago(order_id):
    pagos = [p for p in _load_json(PAGOS_PATH, []) if p.get('orderId') != order_id]
    _save_json(PAGOS_PATH, pagos)


def _pedidos_en_centro(nombre_centro):
    """Devuelve el conjunto de ids de pedidos con envío registrado en el centro dado."""
    key  = nombre_centro.lower().replace(' ', '_')   # "Centro Madrid" → "centro_madrid"
    path = os.path.join(DATA_DIR, f'listado_envios_{key}.json')
    return {e.get('pedido_id', '') for e in _load_json(path, [])}


# ── JP9: flujo extremo a extremo ───────────────────────────────────────────────

def jp9():
    print('\n=== JP9: Flujo extremo a extremo (GestorPedidos → Logístico → Transportista) ===')
    pedido_id = f'PED-JP9-{uuid.uuid4().hex[:6].upper()}'
    print(f'  Pedido: {pedido_id} | ciudad: Madrid | prioridad: normal')

    # Fase 1 (síncrona): GestorPedidos debe responder con Factura
    gr = _get(GESTOR_URL, _pedido_msg(pedido_id, 'Madrid', 'normal', 'tarjeta', [
        {'id': 'p001', 'nombre': 'Producto JP9', 'precio': 50.0,
         'cantidad': 1, 'peso': 1.0, 'vendedor': 'tienda', 'gestion_envio': 'tienda'},
    ]))
    factura_id = _factura_id_de_respuesta(gr)
    if factura_id:
        print(f'  {PASS} Fase 1 — GestorPedidos devolvió factura {factura_id}')
    else:
        print(f'  {FAIL} Fase 1 — No se recibió Factura del GestorPedidos')
        return

    # Fase 2 (asíncrona): el AgenteLogistico procesa en su ciclo (~20 s)
    print(f'  Esperando ciclo del AgenteLogístico (~25 s)...')
    envios = _poll_envios_logistico(factura_id, timeout=35)
    if envios:
        print(f'  {PASS} Fase 2 — Envío registrado en la factura:')
        for e in envios:
            print(f'         Centro: {e.get("centro")} | '
                  f'Transportista: {e.get("transportista")} | '
                  f'Entrega: {e.get("fecha")}')
    else:
        print(f'  {FAIL} Fase 2 — Timeout: no se registraron envios_logistico '
              f'en {factura_id} tras 35 s')

    _limpiar_factura(factura_id)
    print('  Factura de prueba eliminada.')


# ── JP10: perfiles de transportista ───────────────────────────────────────────

def _verificar_perfil(url, nombre, prioridad, precio_min, precio_max, dias_esperado):
    """Envía un CFP y verifica que precio y fecha de entrega encajan con el perfil.

    dias_esperado puede ser un int (día exacto) o una tupla (min_dias, max_dias).
    """
    print(f'\n--- JP10 — {nombre} · prioridad "{prioridad}" ---')
    gr = _get(url, _cfp_msg(prioridad))
    if gr is None:
        print(f'  {FAIL} Sin respuesta de {nombre}')
        return

    oferta = next(gr.subjects(RDF.type, ECSNS.Oferta), None)
    if oferta is None:
        print(f'  {FAIL} Respuesta recibida pero sin nodo ECSNS.Oferta')
        return

    precio    = float(gr.value(oferta, ECSNS.tienePrecio) or -1)
    fecha_str = str(gr.value(oferta, ECSNS.tieneFechaEntrega) or '')

    if precio_min <= precio <= precio_max:
        print(f'  {PASS} Precio: {precio:.2f} € (rango esperado [{precio_min}, {precio_max}])')
    else:
        print(f'  {FAIL} Precio: {precio:.2f} € fuera del rango [{precio_min}, {precio_max}]')

    if isinstance(dias_esperado, tuple):
        min_dias, max_dias = dias_esperado
        fecha_min = (date.today() + timedelta(days=min_dias)).isoformat()
        fecha_max = (date.today() + timedelta(days=max_dias)).isoformat()
        if fecha_min <= fecha_str <= fecha_max:
            print(f'  {PASS} Fecha entrega: {fecha_str} (rango esperado [{fecha_min}, {fecha_max}])')
        else:
            print(f'  {FAIL} Fecha entrega: {fecha_str} fuera del rango [{fecha_min}, {fecha_max}]')
    else:
        fecha_esp = (date.today() + timedelta(days=dias_esperado)).isoformat()
        if fecha_str == fecha_esp:
            print(f'  {PASS} Fecha entrega: {fecha_str} (esperado {fecha_esp})')
        else:
            print(f'  {FAIL} Fecha entrega: {fecha_str} (esperado {fecha_esp})')


def jp10_r():
    """RapidExpress: entrega en 1 día siempre, precio 12–22 €."""
    print('\n--- JP10r: RapidExpress — 1 día independientemente de la prioridad ---')
    for prioridad in ('normal', 'urgente', 'economica'):
        _verificar_perfil(T_RAPIDEXPRESS_URL, 'RapidExpress', prioridad,
                          precio_min=12.0, precio_max=22.0, dias_esperado=1)


def jp10_e():
    """EcoEnvios: precio 3–8 €, entrega = días estándar de la prioridad + 2."""
    print('\n--- JP10e: EcoEnvios — precio barato, +2 días sobre el plazo estándar ---')
    # dias_base: urgente→1, normal→2, economica→4 (+2 en cada caso)
    for prioridad, dias in (('urgente', 3), ('normal', 4), ('economica', 6)):
        _verificar_perfil(T_ECOENIVIOS_URL, 'EcoEnvios', prioridad,
                          precio_min=3.0, precio_max=8.0, dias_esperado=dias)


def jp10_m():
    """MensajeriaPlus: precio y plazo intermedios según prioridad."""
    print('\n--- JP10m: MensajeriaPlus — precio y plazo intermedios ---')
    # precio: random.uniform base (peso=0 en el CFP)
    # dias:   random.randint range del perfil estandar
    casos = [
        ('urgente',  15.0, 30.0, (1, 2)),
        ('normal',    8.0, 15.0, (2, 4)),
        ('economica', 3.0,  8.0, (4, 6)),
    ]
    for prioridad, pmin, pmax, dias in casos:
        _verificar_perfil(T_MENSAJERIAPLUS_URL, 'MensajeriaPlus', prioridad,
                          precio_min=pmin, precio_max=pmax, dias_esperado=dias)


def jp10():
    print('\n=== JP10: Perfiles de transportista ===')
    jp10_r()
    jp10_e()
    jp10_m()
    print('\nJP10 completado.')


# ── JP11: asignación de centro logístico por ciudad ───────────────────────────

MAPA_ZONAS = {
    'Barcelona': 'Centro Barcelona',
    'Zaragoza':  'Centro Barcelona',
    'Valencia':  'Centro Valencia',
    'Madrid':    'Centro Madrid',
    'Bilbao':    'Centro Madrid',
    'Sevilla':   'Centro Sevilla',
}
TODOS_CENTROS = set(MAPA_ZONAS.values())


def _enviar_pedido_y_esperar(pedido_id, ciudad, prod_id):
    """Envía un pedido al GestorPedidos con un producto de tienda y espera 2 s."""
    antes = {cn: _pedidos_en_centro(cn) for cn in TODOS_CENTROS}
    gr = _get(GESTOR_URL, _pedido_msg(pedido_id, ciudad, 'normal', 'tarjeta', [
        {'id': prod_id, 'nombre': f'Prod {prod_id}', 'precio': 10.0,
         'vendedor': 'tienda', 'gestion_envio': 'tienda'},
    ]), timeout=120)
    factura_id = _factura_id_de_respuesta(gr)
    time.sleep(2)
    despues = {cn: _pedidos_en_centro(cn) for cn in TODOS_CENTROS}
    nuevos  = {cn: despues[cn] - antes[cn] for cn in TODOS_CENTROS}
    centros_con_nuevo = [cn for cn, ids in nuevos.items() if ids]
    return factura_id, centros_con_nuevo


def jp11():
    print('\n=== JP11: Asignación de centro logístico por ciudad (mapa_zonas + stock) ===')

    centros = _load_json(CENTROS_PATH, None)
    if not centros:
        print(f'  {SKIP} {CENTROS_PATH} no existe o está vacío — '
              f'el sistema aún no tiene centros configurados.')
        return

    # ── JP11a: producto exclusivo de un solo centro → asignación obligatoria ──
    print('\n--- JP11a: Producto en un solo centro (asignación obligatoria) ---')
    prod_unico   = None
    centro_unico = None
    for c in centros:
        for pid in c.get('productos', []):
            if sum(1 for cx in centros if pid in cx.get('productos', [])) == 1:
                prod_unico   = pid
                centro_unico = c['nombre']
                break
        if prod_unico:
            break

    if not prod_unico:
        print(f'  {SKIP} No hay ningún producto exclusivo de un solo centro')
    else:
        # Elegir una ciudad que el mapa_zonas NO apuntaría a ese centro
        ciudad_otra = next(
            (c for c, cn in MAPA_ZONAS.items() if cn != centro_unico), 'Barcelona'
        )
        print(f'  Producto "{prod_unico}" solo en {centro_unico}.')
        print(f'  Ciudad del pedido: {ciudad_otra} '
              f'(el mapa apuntaría a {MAPA_ZONAS.get(ciudad_otra, "?")}, '
              f'pero debe ir a {centro_unico})')
        pedido_id = f'PED-JP11A-{uuid.uuid4().hex[:6].upper()}'
        factura_id, centros_nuevos = _enviar_pedido_y_esperar(pedido_id, ciudad_otra, prod_unico)
        if centro_unico in centros_nuevos:
            print(f'  {PASS} Sub-pedido enrutado a {centro_unico} (stock único)')
        elif centros_nuevos:
            print(f'  {FAIL} Sub-pedido fue a {centros_nuevos}, se esperaba {centro_unico}')
        else:
            print(f'  {FAIL} No se detectó ningún nuevo pedido en los archivos de centro '
                  f'(¿están los logísticos arrancados?)')
        if factura_id:
            _limpiar_factura(factura_id)

    # ── JP11b: producto en varios centros → preferencia geográfica ─────────────
    print('\n--- JP11b: Producto en varios centros (preferencia geográfica) ---')
    prod_multi    = None
    centros_multi = []
    for c in centros:
        for pid in c.get('productos', []):
            en = [cx['nombre'] for cx in centros if pid in cx.get('productos', [])]
            if len(en) > 1:
                prod_multi    = pid
                centros_multi = en
                break
        if prod_multi:
            break

    if not prod_multi:
        print(f'  {SKIP} No hay ningún producto compartido entre varios centros')
    else:
        ciudad_test = next(
            (c for c, cn in MAPA_ZONAS.items() if cn in centros_multi), None
        )
        if not ciudad_test:
            print(f'  {SKIP} Ninguna ciudad del mapa apunta a los centros con ese producto')
        else:
            centro_esp = MAPA_ZONAS[ciudad_test]
            print(f'  Producto "{prod_multi}" en: {centros_multi}')
            print(f'  Ciudad del pedido: {ciudad_test} → esperado {centro_esp}')
            pedido_id = f'PED-JP11B-{uuid.uuid4().hex[:6].upper()}'
            factura_id, centros_nuevos = _enviar_pedido_y_esperar(pedido_id, ciudad_test, prod_multi)
            if centro_esp in centros_nuevos:
                print(f'  {PASS} Sub-pedido enrutado a {centro_esp} por preferencia geográfica')
            elif centros_nuevos:
                print(f'  {FAIL} Sub-pedido fue a {centros_nuevos}, se esperaba {centro_esp}')
            else:
                print(f'  {FAIL} No se detectó ningún nuevo pedido en los archivos de centro')
            if factura_id:
                _limpiar_factura(factura_id)

    # ── JP11c: ciudad no reconocida → fallback a Centro Madrid ─────────────────
    print('\n--- JP11c: Ciudad no reconocida → fallback a Centro Madrid ---')
    pedido_id = f'PED-JP11C-{uuid.uuid4().hex[:6].upper()}'
    factura_id, centros_nuevos = _enviar_pedido_y_esperar(
        pedido_id, 'CiudadInventada', 'PROD-FALLBACK-JP11'
    )
    if 'Centro Madrid' in centros_nuevos:
        print(f'  {PASS} Ciudad desconocida enrutada a Centro Madrid (fallback correcto)')
    elif centros_nuevos:
        print(f'  {FAIL} Fue a {centros_nuevos}, se esperaba Centro Madrid como fallback')
    else:
        print(f'  {FAIL} No se detectó ningún nuevo pedido en los archivos de centro')
    if factura_id:
        _limpiar_factura(factura_id)

    print('\nJP11 completado.')


# ── JP12: flujo de pagos ───────────────────────────────────────────────────────

def jp12():
    print('\n=== JP12: Flujo de pagos (pendiente → cobrado → devuelto) ===')
    order_id = f'JP12-{uuid.uuid4().hex[:6].upper()}'

    # JP12a: InformacionPago → estadoPago debe ser 'pendiente'
    print('\n--- JP12a: InformacionPago crea el registro en estado pendiente ---')
    cnt = _cnt()
    g = Graph()
    g.bind('ecsns', ECSNS)
    node = ECSNS[f'info-pago-{order_id}']
    g.add((node, RDF.type,         ECSNS.InformacionPago))
    g.add((node, ECSNS.idPedido,   Literal(order_id)))
    g.add((node, ECSNS.comprador,  Literal(TEST_COMPRADOR)))
    g.add((node, ECSNS.metodoPago, Literal('tarjeta')))
    g.add((node, ECSNS.total,      Literal(99.99)))
    msg = build_message(g, perf=ACL.inform,
                        sender=agn.ClienteTest, receiver=agn.AgenteGestorPagos,
                        content=node, msgcnt=cnt)
    _inform(GESTORPAGOS_URL, msg)
    time.sleep(1)

    pagos  = _load_json(PAGOS_PATH, [])
    entrada = next((p for p in pagos if p.get('orderId') == order_id), None)
    if entrada and entrada.get('estadoPago') == 'pendiente':
        print(f'  {PASS} Pago registrado con estadoPago=pendiente')
    elif entrada:
        print(f'  {FAIL} Pago registrado pero estadoPago={entrada.get("estadoPago")} '
              f'(esperado pendiente)')
    else:
        print(f'  {FAIL} No se encontró entrada con orderId={order_id} en {PAGOS_PATH}')

    # JP12b: ConfirmacionEnvio → estadoPago debe pasar a 'cobrado'
    print('\n--- JP12b: ConfirmacionEnvio activa el cobro al usuario ---')
    cnt = _cnt()
    g = Graph()
    g.bind('ecsns', ECSNS)
    node = ECSNS[f'conf-envio-{order_id}']
    g.add((node, RDF.type,        ECSNS.ConfirmacionEnvio))
    g.add((node, ECSNS.idPedido,  Literal(order_id)))
    g.add((node, ECSNS.comprador, Literal(TEST_COMPRADOR)))
    g.add((node, ECSNS.total,     Literal(99.99)))
    msg = build_message(g, perf=ACL.inform,
                        sender=agn.ClienteTest, receiver=agn.AgenteGestorPagos,
                        content=node, msgcnt=cnt)
    _inform(GESTORPAGOS_URL, msg)
    time.sleep(1)

    pagos  = _load_json(PAGOS_PATH, [])
    entrada = next((p for p in pagos if p.get('orderId') == order_id), None)
    if entrada and entrada.get('estadoPago') == 'cobrado':
        print(f'  {PASS} estadoPago actualizado a cobrado')
    elif entrada:
        print(f'  {FAIL} estadoPago={entrada.get("estadoPago")} (esperado cobrado)')
    else:
        print(f'  {FAIL} Entrada de pago no encontrada tras ConfirmacionEnvio')

    # JP12c: SolicitudReembolso → estadoPago debe pasar a 'devuelto'
    print('\n--- JP12c: SolicitudReembolso procesa la devolución del dinero ---')
    cnt = _cnt()
    g = Graph()
    g.bind('ecsns', ECSNS)
    node = ECSNS[f'reemb-{order_id}']
    g.add((node, RDF.type,        ECSNS.SolicitudReembolso))
    g.add((node, ECSNS.idPedido,  Literal(order_id)))
    g.add((node, ECSNS.comprador, Literal(TEST_COMPRADOR)))
    msg = build_message(g, perf=ACL.request,
                        sender=agn.ClienteTest, receiver=agn.AgenteGestorPagos,
                        content=node, msgcnt=cnt)
    gr = _get(GESTORPAGOS_URL, msg)

    ack = next(gr.subjects(RDF.type, ECSNS.AckActualizacion), None) if gr else None
    actualizado = bool(gr.value(ack, ECSNS.actualizado)) if ack else False
    if actualizado:
        print(f'  {PASS} AckActualizacion recibido con actualizado=True')
    else:
        print(f'  {FAIL} No se recibió AckActualizacion válido del GestorPagos')

    time.sleep(1)
    pagos  = _load_json(PAGOS_PATH, [])
    entrada = next((p for p in pagos if p.get('orderId') == order_id), None)
    if entrada and entrada.get('estadoPago') == 'devuelto':
        print(f'  {PASS} estadoPago actualizado a devuelto')
    elif entrada:
        print(f'  {FAIL} estadoPago={entrada.get("estadoPago")} (esperado devuelto)')
    else:
        print(f'  {FAIL} Entrada de pago no encontrada tras SolicitudReembolso')

    _limpiar_pago(order_id)
    print('\n  Registro de pago de prueba eliminado.')
    print('JP12 completado.')


# ── JP13: pedido con producto de vendedor externo ─────────────────────────────

def jp13():
    print('\n=== JP13: Pedido con producto de vendedor externo ===')
    pedido_id = f'PED-JP13-{uuid.uuid4().hex[:6].upper()}'
    print(f'  Pedido: {pedido_id} | vendedor: VendedorExterno1')

    gr = _get(GESTOR_URL, _pedido_msg(pedido_id, 'Madrid', 'normal', 'paypal', [
        {
            'id': 'EXT-PROD-001', 'nombre': 'Producto Externo Test',
            'precio': 75.0, 'cantidad': 1, 'peso': 0.5,
            'vendedor': 'VendedorExterno1', 'gestion_envio': 'externo',
        },
    ]))
    factura_id = _factura_id_de_respuesta(gr)
    if not factura_id:
        print(f'  {FAIL} No se recibió Factura del GestorPedidos')
        return
    print(f'  {PASS} Factura recibida: {factura_id}')

    # Verificar que la factura contiene envios_vendedor
    time.sleep(1)
    fac = next((f for f in _load_json(FACTURAS_PATH, []) if f.get('id') == factura_id), None)
    if not fac:
        print(f'  {FAIL} Factura {factura_id} no encontrada en listado_facturas.json')
    elif fac.get('envios_vendedor'):
        ev = fac['envios_vendedor'][0]
        print(f'  {PASS} envios_vendedor presente en la factura:')
        print(f'         Vendedor:       {ev.get("vendedor")}')
        print(f'         Transportista:  {ev.get("transportista")}')
        print(f'         Fecha prevista: {ev.get("fecha_prevista")}')
    else:
        print(f'  {FAIL} La factura existe pero envios_vendedor está vacío '
              f'— ¿está AgenteVendedorExterno arrancado y registrado?')

    _limpiar_factura(factura_id)
    print('  Factura de prueba eliminada.')


# ── JP14: ciclo de experiencia ─────────────────────────────────────────────────

def jp14():
    print('\n=== JP14: Ciclo de experiencia (historial, valoraciones, recomendaciones) ===')
    pedido_id = f'JP14-{uuid.uuid4().hex[:6].upper()}'

    # JP14a: CompraFinalizada → historial_compras.json debe incluir el pedido
    print('\n--- JP14a: CompraFinalizada actualiza el historial del usuario ---')
    cnt = _cnt()
    g = Graph()
    g.bind('ecsns', ECSNS)
    node = ECSNS[f'compra-{pedido_id}']
    pn   = ECSNS[f'cp-prod-{pedido_id}']
    g.add((node, RDF.type,            ECSNS.CompraFinalizada))
    g.add((node, ECSNS.comprador,     Literal(TEST_COMPRADOR)))
    g.add((node, ECSNS.idPedido,      Literal(pedido_id)))
    g.add((node, ECSNS.total,         Literal(120.0)))
    g.add((node, ECSNS.fecha,         Literal(datetime.now().isoformat())))
    g.add((node, ECSNS.tieneProducto, pn))
    g.add((pn,   ECSNS.idProducto,    Literal('PROD-EXP-001')))
    g.add((pn,   ECSNS.nombre,        Literal('Producto Experiencia Test')))
    g.add((pn,   ECSNS.precio,        Literal(120.0)))
    g.add((pn,   ECSNS.cantidad,      Literal(1)))
    msg = build_message(g, perf=ACL.inform,
                        sender=agn.ClienteTest, receiver=agn.AgenteExperiencia,
                        content=node, msgcnt=cnt)
    _inform(EXPERIENCIA_URL, msg)
    time.sleep(1)

    historial = _load_json(HISTORIAL_PATH, {})
    compras   = historial.get(TEST_COMPRADOR, [])
    if any(c.get('pedido_id') == pedido_id or c.get('idPedido') == pedido_id
           for c in compras):
        print(f'  {PASS} Compra {pedido_id} registrada en el historial de {TEST_COMPRADOR}')
    else:
        print(f'  {FAIL} Compra no encontrada en historial_compras.json '
              f'para el usuario {TEST_COMPRADOR}')

    # JP14b: NuevaValoracion → listado_opiniones.json debe incluir la reseña
    print('\n--- JP14b: NuevaValoracion registra la reseña del producto ---')
    cnt = _cnt()
    g = Graph()
    g.bind('ecsns', ECSNS)
    node = ECSNS[f'val-{pedido_id}']
    g.add((node, RDF.type,         ECSNS.NuevaValoracion))
    g.add((node, ECSNS.comprador,  Literal(TEST_COMPRADOR)))
    g.add((node, ECSNS.idProducto, Literal('PROD-EXP-001')))
    g.add((node, ECSNS.idPedido,   Literal(pedido_id)))
    g.add((node, ECSNS.puntuacion, Literal(5)))
    g.add((node, ECSNS.comentario, Literal('Test automatizado JP14')))
    msg = build_message(g, perf=ACL.request,
                        sender=agn.ClienteTest, receiver=agn.AgenteExperiencia,
                        content=node, msgcnt=cnt)
    _get(EXPERIENCIA_URL, msg)
    time.sleep(1)

    opiniones = _load_json(OPINIONES_PATH, {})
    # Structure: { product_id: { "valoraciones": [{...}], ... } }
    vals = []
    if isinstance(opiniones, dict):
        for datos in opiniones.values():
            if isinstance(datos, dict):
                vals.extend(datos.get('valoraciones', []))
            elif isinstance(datos, list):
                vals.extend(datos)
    encontrada = any(
        v.get('idPedido') == pedido_id or v.get('pedido_id') == pedido_id
        or v.get('pedido_id') == pedido_id.upper()
        for v in vals if isinstance(v, dict)
    )
    if encontrada:
        print(f'  {PASS} Valoración registrada en listado_opiniones.json')
    else:
        print(f'  {FAIL} Valoración no encontrada en listado_opiniones.json '
              f'(puede que la estructura interna sea diferente a la esperada)')

    # JP14c: PedirRecomendaciones → debe devolver al menos un producto
    print('\n--- JP14c: PedirRecomendaciones devuelve productos relevantes ---')
    cnt = _cnt()
    g = Graph()
    g.bind('ecsns', ECSNS)
    node = ECSNS[f'recs-{cnt}']
    g.add((node, RDF.type,        ECSNS.PedirRecomendaciones))
    g.add((node, ECSNS.comprador, Literal(TEST_COMPRADOR)))
    msg = build_message(g, perf=ACL.request,
                        sender=agn.ClienteTest, receiver=agn.AgenteExperiencia,
                        content=node, msgcnt=cnt)
    gr = _get(EXPERIENCIA_URL, msg)
    recs = []
    if gr:
        for rec_node in gr.subjects(RDF.type, ECSNS.Recomendaciones):
            recs.extend(gr.objects(rec_node, ECSNS.tieneRecomendacion))
    if recs:
        print(f'  {PASS} {len(recs)} recomendación(es) recibida(s):')
        for r in recs[:3]:
            nombre = str(gr.value(r, ECSNS.nombre) or gr.value(r, ECSNS.idProducto) or r)
            print(f'         • {nombre}')
    else:
        print(f'  {FAIL} No se recibieron recomendaciones '
              f'(¿tiene {TEST_COMPRADOR} historial suficiente y hay catálogo cargado?)')

    # JP14d: ConsultaHistorial → debe devolver el pedido añadido en JP14a
    print('\n--- JP14d: ConsultaHistorial devuelve las compras del usuario ---')
    cnt = _cnt()
    g = Graph()
    g.bind('ecsns', ECSNS)
    node = ECSNS[f'hist-{cnt}']
    g.add((node, RDF.type,        ECSNS.ConsultaHistorial))
    g.add((node, ECSNS.comprador, Literal(TEST_COMPRADOR)))
    msg = build_message(g, perf=ACL.request,
                        sender=agn.ClienteTest, receiver=agn.AgenteExperiencia,
                        content=node, msgcnt=cnt)
    gr = _get(EXPERIENCIA_URL, msg)
    encontrado = any(str(o) == pedido_id for _, _, o in gr) if gr else False
    if encontrado:
        print(f'  {PASS} Pedido {pedido_id} presente en la respuesta de ConsultaHistorial')
    else:
        print(f'  {FAIL} Pedido {pedido_id} no encontrado en la respuesta de ConsultaHistorial')

    print('\nJP14 completado.')


# ── main ────────────────────────────────────────────────────────────────────────

JP10_SUBS = {'r': jp10_r, 'e': jp10_e, 'm': jp10_m}

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='JP9–JP14 — pruebas del sistema distribuido')
    parser.add_argument('--jp',  type=int, required=True, choices=range(9, 15),
                        help='Número del juego de prueba (9–14)')
    parser.add_argument('--sub', default=None, choices=['r', 'e', 'm'],
                        help='Subtransportista para JP10: r=RapidExpress, e=EcoEnvios, m=MensajeriaPlus')
    parser.add_argument('--host', default='localhost',
                        help='Host donde corren los agentes (default: localhost)')
    args = parser.parse_args()

    h = args.host
    GESTOR_URL           = f'http://{h}:9002/comm'
    EXPERIENCIA_URL      = f'http://{h}:9005/comm'
    VENDEDOR_URL         = f'http://{h}:9007/comm'
    T_RAPIDEXPRESS_URL   = f'http://{h}:9010/comm'
    T_ECOENIVIOS_URL     = f'http://{h}:9011/comm'
    T_MENSAJERIAPLUS_URL = f'http://{h}:9012/comm'
    GESTORPAGOS_URL      = f'http://{h}:9014/comm'

    jp_map = {9: jp9, 10: jp10, 11: jp11, 12: jp12, 13: jp13, 14: jp14}

    if args.jp == 10 and args.sub:
        JP10_SUBS[args.sub]()
    else:
        jp_map[args.jp]()
