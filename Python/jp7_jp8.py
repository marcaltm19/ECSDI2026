#!/usr/bin/env python3
"""
jp7_jp8.py  —  Juegos de prueba JP7 (búsqueda de productos) y JP8 (devolución de pedido)

Ejecución:
    python jp7_jp8.py --jp 7             # JP7: todos los subtests de búsqueda
    python jp7_jp8.py --jp 8             # JP8: todos los subtests de devolución
    python jp7_jp8.py --jp 7 --sub a     # JP7a: búsqueda por categoría
    python jp7_jp8.py --jp 7 --sub b     # JP7b: búsqueda por precio máximo
    python jp7_jp8.py --jp 8 --sub a     # JP8a: devolución por producto defectuoso
    python jp7_jp8.py --jp 8 --sub c     # JP8c: rechazo por fuera de plazo
    python jp7_jp8.py --jp 7 --host 192.168.1.5  # apuntar a otro host

Requiere que start_demo.sh esté corriendo.
"""

import argparse
import json
import os
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from rdflib import Graph, Literal, Namespace
from rdflib.namespace import RDF
from AgentUtil.ACL import ACL
from AgentUtil.ACLMessages import build_message
from ontologia import ECSNS
import requests as http_requests

agn = Namespace('http://www.agentes.org#')
mss_cnt = 0

COMPRADOR_URL  = 'http://localhost:9001/comm'
DEVOLUCION_URL = 'http://localhost:9006/comm'

DATA_DIR      = os.path.join(os.path.dirname(__file__), 'data')
FACTURAS_PATH = os.path.join(DATA_DIR, 'listado_facturas.json')

TEST_FACTURA_ID = 'FAC-JPTEST-001'
TEST_COMPRADOR  = 'TestUser'

PASS = '[ OK ]'
FAIL = '[FAIL]'


# ── utilidades comunes ─────────────────────────────────────────────────────────

def _next_cnt():
    global mss_cnt
    c = mss_cnt
    mss_cnt += 1
    return c


def _get(url, msg_graph):
    try:
        resp = http_requests.get(
            url,
            params={'content': msg_graph.serialize(format='xml')},
            timeout=10,
        )
        resp.raise_for_status()
        gr = Graph()
        gr.parse(data=resp.text, format='xml')
        return gr
    except Exception as e:
        print(f'  [ERROR] {e}')
        return None


# ── JP7: búsqueda de productos ─────────────────────────────────────────────────

def _enviar_busqueda(categoria=None, precio_max=None, val_min=None):
    g = Graph()
    g.bind('ecsns', ECSNS)
    cnt  = _next_cnt()
    node = agn[f'busq-{cnt}']
    g.add((node, RDF.type,        ECSNS.Busqueda))
    g.add((node, ECSNS.comprador, Literal(TEST_COMPRADOR)))
    if categoria is not None:
        g.add((node, ECSNS.categoria,        Literal(categoria)))
    if precio_max is not None:
        g.add((node, ECSNS.precioMaximo,     Literal(float(precio_max))))
    if val_min is not None:
        g.add((node, ECSNS.valoracionMinima, Literal(float(val_min))))
    msg = build_message(g, perf=ACL.request,
                        sender=agn.ClienteTest,
                        receiver=agn.AgenteComprador,
                        content=node, msgcnt=cnt)
    return _get(COMPRADOR_URL, msg)


def _extraer_productos(gr):
    if gr is None:
        return []
    prods = []
    for prod in gr.subjects(RDF.type, ECSNS.Producto):
        prods.append({
            'id':        str(gr.value(prod, ECSNS.idProducto)  or ''),
            'nombre':    str(gr.value(prod, ECSNS.nombre)       or ''),
            'precio':    float(gr.value(prod, ECSNS.precio)     or 0),
            'categoria': str(gr.value(prod, ECSNS.categoria)    or ''),
            'valoracion': float(gr.value(prod, ECSNS.valoracion) or 0),
        })
    return prods


def jp7a():
    """Búsqueda por categoría: sólo deben devolverse productos de esa categoría."""
    print('\n--- JP7a: Búsqueda por categoría "Electronica" ---')
    gr    = _enviar_busqueda(categoria='Electronica')
    prods = _extraer_productos(gr)
    fuera = [p for p in prods if p['categoria'].lower() != 'electronica']
    if gr is None:
        print(f'  {FAIL} Sin respuesta del AgenteComprador')
    elif not prods:
        print(f'  {FAIL} No se encontraron productos (¿catálogo vacío?)')
    elif fuera:
        print(f'  {FAIL} Productos fuera de categoría: {[p["nombre"] for p in fuera]}')
    else:
        print(f'  {PASS} {len(prods)} producto(s), todos de categoría Electronica')
        for p in prods:
            print(f'         • {p["nombre"]}  €{p["precio"]}  ★{p["valoracion"]}')


def jp7b():
    """Búsqueda con precio máximo: ningún resultado puede superar el límite."""
    print('\n--- JP7b: Búsqueda con precio máximo 50 € ---')
    gr    = _enviar_busqueda(precio_max=50.0)
    prods = _extraer_productos(gr)
    caros = [p for p in prods if p['precio'] > 50.0]
    if gr is None:
        print(f'  {FAIL} Sin respuesta del AgenteComprador')
    elif caros:
        print(f'  {FAIL} Productos que superan el precio máximo: {[p["nombre"] for p in caros]}')
    else:
        print(f'  {PASS} {len(prods)} producto(s) con precio ≤ 50 €')
        for p in prods:
            print(f'         • {p["nombre"]}  €{p["precio"]}')


def jp7c():
    """Búsqueda con valoración mínima: ningún resultado puede estar por debajo."""
    print('\n--- JP7c: Búsqueda con valoración mínima 4.5 ---')
    gr    = _enviar_busqueda(val_min=4.5)
    prods = _extraer_productos(gr)
    bajos = [p for p in prods if p['valoracion'] < 4.5]
    if gr is None:
        print(f'  {FAIL} Sin respuesta del AgenteComprador')
    elif bajos:
        print(f'  {FAIL} Productos por debajo de la valoración mínima: {[p["nombre"] for p in bajos]}')
    else:
        print(f'  {PASS} {len(prods)} producto(s) con valoración ≥ 4.5')
        for p in prods:
            print(f'         • {p["nombre"]}  ★{p["valoracion"]}')


def jp7d():
    """Búsqueda combinada: todos los filtros deben aplicarse simultáneamente."""
    print('\n--- JP7d: Búsqueda combinada (Electronica, máx 500 €, valoración ≥ 4.0) ---')
    gr    = _enviar_busqueda(categoria='Electronica', precio_max=500.0, val_min=4.0)
    prods = _extraer_productos(gr)
    fallos = []
    for p in prods:
        if p['categoria'].lower() != 'electronica':
            fallos.append(f'{p["nombre"]}: categoría "{p["categoria"]}" ≠ Electronica')
        if p['precio'] > 500.0:
            fallos.append(f'{p["nombre"]}: €{p["precio"]} > 500')
        if p['valoracion'] < 4.0:
            fallos.append(f'{p["nombre"]}: ★{p["valoracion"]} < 4.0')
    if gr is None:
        print(f'  {FAIL} Sin respuesta del AgenteComprador')
    elif fallos:
        print(f'  {FAIL} Filtros no respetados:')
        for f in fallos:
            print(f'         ! {f}')
    else:
        print(f'  {PASS} {len(prods)} producto(s) cumplen todos los filtros')
        for p in prods:
            print(f'         • {p["nombre"]}  €{p["precio"]}  ★{p["valoracion"]}')


def jp7e():
    """Búsqueda sin resultados: filtro imposible → lista vacía."""
    print('\n--- JP7e: Búsqueda sin resultados (precio máximo 0.01 €) ---')
    gr    = _enviar_busqueda(precio_max=0.01)
    prods = _extraer_productos(gr)
    if gr is None:
        print(f'  {FAIL} Sin respuesta del AgenteComprador')
    elif prods:
        print(f'  {FAIL} Se esperaba lista vacía, se obtuvieron {len(prods)} producto(s)')
    else:
        print(f'  {PASS} Lista vacía — ningún producto cumple el filtro (correcto)')


def jp7():
    print('\n=== JP7: Búsqueda de productos con filtros ===')
    jp7a()
    jp7b()
    jp7c()
    jp7d()
    jp7e()
    print('\nJP7 completado.')


# ── JP8: devolución de pedido ──────────────────────────────────────────────────

def _insertar_factura_test():
    """Inyecta una factura de prueba en listado_facturas.json."""
    try:
        facturas = []
        if os.path.exists(FACTURAS_PATH):
            with open(FACTURAS_PATH) as f:
                facturas = json.load(f)
        facturas = [fac for fac in facturas if fac.get('id') != TEST_FACTURA_ID]
        facturas.append({
            'id':       TEST_FACTURA_ID,
            'comprador': TEST_COMPRADOR,
            'fecha':    '2026-05-01T10:00:00',
            'productos': [
                {
                    'id': 'p001', 'nombre': 'Laptop Pro 15', 'precio': 1200.0,
                    'cantidad': 1, 'peso': 2.1, 'vendedor': 'tienda',
                    'gestion_envio': 'tienda',
                }
            ],
            'total':        1200.0,
            'direccion':    'Calle Test 1',
            'metodo_pago':  'tarjeta',
            'devuelta':     False,
        })
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(FACTURAS_PATH, 'w') as f:
            json.dump(facturas, f, indent=2)
        return True
    except Exception as e:
        print(f'  [ERROR] No se pudo insertar la factura de prueba: {e}')
        return False


def _eliminar_factura_test():
    try:
        if not os.path.exists(FACTURAS_PATH):
            return
        with open(FACTURAS_PATH) as f:
            facturas = json.load(f)
        facturas = [fac for fac in facturas if fac.get('id') != TEST_FACTURA_ID]
        with open(FACTURAS_PATH, 'w') as f:
            json.dump(facturas, f, indent=2)
    except Exception as e:
        print(f'  [WARN] No se pudo eliminar la factura de prueba: {e}')


def _resetear_factura_test():
    """Marca la factura de prueba como no devuelta para el siguiente subtest."""
    try:
        if not os.path.exists(FACTURAS_PATH):
            return
        with open(FACTURAS_PATH) as f:
            facturas = json.load(f)
        for fac in facturas:
            if fac.get('id') == TEST_FACTURA_ID:
                fac['devuelta'] = False
                fac.pop('fecha_devolucion', None)
                break
        with open(FACTURAS_PATH, 'w') as f:
            json.dump(facturas, f, indent=2)
    except Exception as e:
        print(f'  [WARN] No se pudo resetear la factura de prueba: {e}')


def _marcar_factura_devuelta():
    """Marca la factura de prueba como ya devuelta (para JP8e)."""
    try:
        with open(FACTURAS_PATH) as f:
            facturas = json.load(f)
        for fac in facturas:
            if fac.get('id') == TEST_FACTURA_ID:
                fac['devuelta'] = True
                break
        with open(FACTURAS_PATH, 'w') as f:
            json.dump(facturas, f, indent=2)
    except Exception as e:
        print(f'  [ERROR] No se pudo preparar el estado de la factura: {e}')


def _enviar_devolucion(factura_id, razon, fecha_recepcion, comprador=TEST_COMPRADOR):
    g = Graph()
    g.bind('ecsns', ECSNS)
    cnt  = _next_cnt()
    node = agn[f'dev-{cnt}']
    g.add((node, RDF.type,              ECSNS.SolicitudDevolucion))
    g.add((node, ECSNS.comprador,       Literal(comprador)))
    g.add((node, ECSNS.idFactura,       Literal(factura_id)))
    g.add((node, ECSNS.razonDevolucion, Literal(razon)))
    g.add((node, ECSNS.fechaRecepcion,  Literal(str(fecha_recepcion))))
    msg = build_message(g, perf=ACL.request,
                        sender=agn.ClienteTest,
                        receiver=agn.AgenteDevolucion,
                        content=node, msgcnt=cnt)
    return _get(DEVOLUCION_URL, msg)


def _extraer_devolucion(gr):
    """Returns (aceptada, motivo, dev_id) from a return response graph."""
    if gr is None:
        return None, None, None
    for node in gr.subjects(RDF.type, ECSNS.Devolucion):
        aceptada_lit = gr.value(node, ECSNS.aceptada)
        motivo       = str(gr.value(node, ECSNS.motivoDevolucion) or '')
        dev_id       = str(gr.value(node, ECSNS.idDevolucion)      or '')
        if aceptada_lit is not None:
            return aceptada_lit.toPython(), motivo, dev_id
    return None, None, None


def jp8a():
    """Devolución automáticamente aceptada: la razón contiene la palabra clave "defectuoso"."""
    print('\n--- JP8a: Aceptación automática (producto defectuoso) ---')
    _resetear_factura_test()
    fecha = date.today() - timedelta(days=3)
    print(f'  Factura: {TEST_FACTURA_ID} | razón: "El producto llegó defectuoso" | recepción: {fecha}')
    gr = _enviar_devolucion(TEST_FACTURA_ID, 'El producto llegó defectuoso', fecha)
    aceptada, motivo, dev_id = _extraer_devolucion(gr)
    if aceptada is True:
        print(f'  {PASS} Devolución ACEPTADA — {motivo} (ID: {dev_id})')
    elif aceptada is False:
        print(f'  {FAIL} Devolución rechazada — se esperaba aceptación automática. Motivo: {motivo}')
    else:
        print(f'  {FAIL} Sin respuesta válida del AgenteDevolucion')


def jp8b():
    """Devolución aceptada dentro del plazo de 15 días."""
    print('\n--- JP8b: Aceptación dentro del plazo (7 días desde recepción) ---')
    _resetear_factura_test()
    fecha = date.today() - timedelta(days=7)
    print(f'  Factura: {TEST_FACTURA_ID} | razón: "No me convence" | recepción: {fecha}')
    gr = _enviar_devolucion(TEST_FACTURA_ID, 'No me convence el producto', fecha)
    aceptada, motivo, dev_id = _extraer_devolucion(gr)
    if aceptada is True:
        print(f'  {PASS} Devolución ACEPTADA — {motivo}')
    elif aceptada is False:
        print(f'  {FAIL} Devolución rechazada — debería estar dentro del plazo de 15 días. Motivo: {motivo}')
    else:
        print(f'  {FAIL} Sin respuesta válida del AgenteDevolucion')


def jp8c():
    """Devolución rechazada porque han pasado más de 15 días desde la recepción."""
    print('\n--- JP8c: Rechazo por fuera de plazo (20 días desde recepción) ---')
    _resetear_factura_test()
    fecha = date.today() - timedelta(days=20)
    print(f'  Factura: {TEST_FACTURA_ID} | razón: "Ya no me gusta" | recepción: {fecha}')
    gr = _enviar_devolucion(TEST_FACTURA_ID, 'Ya no me gusta', fecha)
    aceptada, motivo, dev_id = _extraer_devolucion(gr)
    if aceptada is False:
        print(f'  {PASS} Devolución RECHAZADA correctamente — {motivo}')
    elif aceptada is True:
        print(f'  {FAIL} Devolución aceptada — debería rechazarse (>15 días). Motivo: {motivo}')
    else:
        print(f'  {FAIL} Sin respuesta válida del AgenteDevolucion')


def jp8d():
    """Devolución rechazada porque la factura no existe en el sistema."""
    print('\n--- JP8d: Rechazo por factura inexistente ---')
    fecha = date.today() - timedelta(days=2)
    print(f'  Factura: FAC-NOEXISTE-999 (no registrada en el sistema)')
    gr = _enviar_devolucion('FAC-NOEXISTE-999', 'Quiero devolver este pedido', fecha)
    aceptada, motivo, dev_id = _extraer_devolucion(gr)
    if aceptada is False:
        print(f'  {PASS} Devolución RECHAZADA correctamente — {motivo}')
    elif aceptada is True:
        print(f'  {FAIL} Devolución aceptada — no debería aceptarse una factura inexistente')
    else:
        print(f'  {FAIL} Sin respuesta válida del AgenteDevolucion')


def jp8e():
    """Devolución rechazada porque la factura ya fue devuelta anteriormente."""
    print('\n--- JP8e: Rechazo por devolución duplicada (factura ya devuelta) ---')
    _marcar_factura_devuelta()
    fecha = date.today() - timedelta(days=2)
    print(f'  Factura: {TEST_FACTURA_ID} (marcada como ya devuelta) | razón: "defectuoso"')
    gr = _enviar_devolucion(TEST_FACTURA_ID, 'defectuoso', fecha)
    aceptada, motivo, dev_id = _extraer_devolucion(gr)
    if aceptada is False:
        print(f'  {PASS} Devolución RECHAZADA correctamente — {motivo}')
    elif aceptada is True:
        print(f'  {FAIL} Devolución aceptada — no debería permitirse devolver dos veces')
    else:
        print(f'  {FAIL} Sin respuesta válida del AgenteDevolucion')


def jp8():
    print('\n=== JP8: Devolución de pedido ===')
    print('  Insertando factura de prueba...')
    if not _insertar_factura_test():
        print('  Abortando JP8: no se pudo preparar el entorno de prueba.')
        return
    try:
        jp8a()
        jp8b()
        jp8c()
        jp8d()
        jp8e()
    finally:
        _eliminar_factura_test()
        print('\n  Factura de prueba eliminada.')
    print('\nJP8 completado.')


# ── main ────────────────────────────────────────────────────────────────────────

JP7_SUBS = {'a': jp7a, 'b': jp7b, 'c': jp7c, 'd': jp7d, 'e': jp7e}
JP8_SUBS = {'a': jp8a, 'b': jp8b, 'c': jp8c, 'd': jp8d, 'e': jp8e}

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='JP7/JP8 — búsqueda de productos y devolución')
    parser.add_argument('--jp',  type=int, required=True, choices=[7, 8],
                        help='Número del juego de prueba (7 o 8)')
    parser.add_argument('--sub', default=None, choices=list('abcde'),
                        help='Subtest específico (a–e). Sin --sub ejecuta todos.')
    parser.add_argument('--host', default='localhost',
                        help='Host donde corren los agentes (default: localhost)')
    parser.add_argument('--comprador_port', type=int, default=9001)
    parser.add_argument('--devolucion_port', type=int, default=9006)
    args = parser.parse_args()

    COMPRADOR_URL  = f'http://{args.host}:{args.comprador_port}/comm'
    DEVOLUCION_URL = f'http://{args.host}:{args.devolucion_port}/comm'

    if args.jp == 7:
        if args.sub:
            JP7_SUBS[args.sub]()
        else:
            jp7()
    else:
        if args.sub:
            if not _insertar_factura_test():
                sys.exit(1)
            try:
                JP8_SUBS[args.sub]()
            finally:
                _eliminar_factura_test()
        else:
            jp8()
