import argparse
import json
import logging
import os
import signal
import socket
import sys
import threading
import time
from multiprocessing import Process, Queue

from flask import Flask, request
from rdflib import Graph, Literal, Namespace
from rdflib.namespace import FOAF, RDF

import requests as http_requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from AgentUtil.ACL import ACL
from AgentUtil.ACLMessages import build_message, get_message_properties, send_message
from AgentUtil.Agent import Agent
from AgentUtil.DSO import DSO
from AgentUtil.FlaskServer import shutdown_server
from AgentUtil.Logging import config_logger
from ontologia import ECSNS

parser = argparse.ArgumentParser()
parser.add_argument('--open', action='store_true', default=False)
parser.add_argument('--verbose', action='store_true', default=False)
parser.add_argument('--port', type=int, default=9014)
parser.add_argument('--dhost', default=None)
parser.add_argument('--dport', type=int, default=9000)
args = parser.parse_args()

logger = config_logger(level=1)
port = args.port
hostname = socket.gethostname()
hostaddr = os.environ.get('ECSDI_PUBLIC_HOST') or hostname
flask_host = '0.0.0.0' if args.open else hostname
dport = args.dport
dhostname = os.environ.get('ECSDI_DHOST') or args.dhost or socket.gethostname()

app = Flask(__name__)
if not args.verbose:
    logging.getLogger('werkzeug').setLevel(logging.ERROR)

agn = Namespace('http://www.agentes.org#')
mss_cnt = 0

PAGOS_PATH = os.path.join(os.path.dirname(__file__), 'data', 'informacion_pago.json')
FACTURAS_PATH = os.path.join(os.path.dirname(__file__), 'data', 'listado_facturas.json')
_pagos_lock = threading.Lock()

PagosAgent = Agent(
    'AgenteGestorPagos',
    agn.AgenteGestorPagos,
    'http://%s:%d/comm' % (hostaddr, port),
    'http://%s:%d/Stop' % (hostaddr, port),
)
DirectoryAgent = Agent(
    'DirectoryAgent',
    agn.Directory,
    'http://%s:%d/Register' % (dhostname, dport),
    'http://%s:%d/Stop' % (dhostname, dport),
)

cola1 = Queue()
experiencia_address = None


def register_message():
    global mss_cnt
    gmess = Graph()
    gmess.bind('foaf', FOAF)
    gmess.bind('dso', DSO)
    reg_obj = agn[PagosAgent.name + '-Register']
    gmess.add((reg_obj, RDF.type,      DSO.Register))
    gmess.add((reg_obj, DSO.Uri,       PagosAgent.uri))
    gmess.add((reg_obj, FOAF.name,     Literal(PagosAgent.name)))
    gmess.add((reg_obj, DSO.Address,   Literal(PagosAgent.address)))
    gmess.add((reg_obj, DSO.AgentType, ECSNS['Ag.GestorDePagos']))
    gr = send_message(
        build_message(gmess, perf=ACL.request, sender=PagosAgent.uri,
                      receiver=DirectoryAgent.uri, content=reg_obj, msgcnt=mss_cnt),
        DirectoryAgent.address,
    )
    mss_cnt += 1
    return gr


def get_agent_address(agent_type):
    global mss_cnt
    gmess = Graph()
    gmess.bind('dso', DSO)
    search_obj = agn['Search-' + str(mss_cnt)]
    gmess.add((search_obj, RDF.type,      DSO.Search))
    gmess.add((search_obj, DSO.AgentType, agent_type))
    msg = build_message(gmess, perf=ACL.request, sender=PagosAgent.uri,
                        receiver=DirectoryAgent.uri, content=search_obj, msgcnt=mss_cnt)
    response = http_requests.get(
        DirectoryAgent.address,
        params={'content': msg.serialize(format='xml')},
        timeout=5,
    )
    mss_cnt += 1
    gr = Graph()
    gr.parse(data=response.text, format='xml')
    for s, p, o in gr:
        if p == DSO.Address:
            return str(o)
    return None


def _buscar_address_vendedor(nombre_vendedor):
    global mss_cnt
    gmess = Graph()
    gmess.bind('dso', DSO)
    search_obj = agn[f'SearchVend-{mss_cnt}']
    gmess.add((search_obj, RDF.type,      DSO.Search))
    gmess.add((search_obj, DSO.AgentType, ECSNS['Ag.VendedorExterno']))
    msg = build_message(gmess, perf=ACL.request, sender=PagosAgent.uri,
                        receiver=DirectoryAgent.uri, content=search_obj, msgcnt=mss_cnt)
    mss_cnt += 1
    try:
        r = http_requests.get(DirectoryAgent.address,
                              params={'content': msg.serialize(format='xml')}, timeout=5)
        gr_ds = Graph()
        gr_ds.parse(data=r.text, format='xml')
        for entry in gr_ds.subjects(DSO.Uri):
            uri = gr_ds.value(entry, DSO.Uri)
            addr = gr_ds.value(entry, DSO.Address)
            if uri and str(uri).endswith(nombre_vendedor) and addr:
                return str(addr)
        for entry in gr_ds.subjects(DSO.Uri):
            addr = gr_ds.value(entry, DSO.Address)
            if addr:
                return str(addr)
    except Exception as e:
        logger.warning(f'[GestorPagos] Error buscando vendedor {nombre_vendedor}: {e}')
    return None


def cargar_pagos():
    with _pagos_lock:
        if not os.path.exists(PAGOS_PATH):
            return []
        try:
            with open(PAGOS_PATH) as f:
                return json.load(f)
        except (json.JSONDecodeError, ValueError):
            return []


def guardar_pagos(pagos):
    with _pagos_lock:
        os.makedirs(os.path.dirname(PAGOS_PATH), exist_ok=True)
        with open(PAGOS_PATH, 'w') as f:
            json.dump(pagos, f, indent=2, ensure_ascii=False)


def buscar_pago(order_id):
    for p in cargar_pagos():
        if p.get('orderId') == order_id:
            return p
    return None


def upsert_pago(entry):
    pagos = cargar_pagos()
    found = False
    for i, p in enumerate(pagos):
        if p.get('orderId') == entry.get('orderId'):
            pagos[i] = {**p, **entry}
            found = True
            break
    if not found:
        pagos.append(entry)
    guardar_pagos(pagos)
    return buscar_pago(entry['orderId'])


def actualizar_pago(order_id, **kwargs):
    pagos = cargar_pagos()
    for p in pagos:
        if p.get('orderId') == order_id:
            p.update(kwargs)
            guardar_pagos(pagos)
            return p
    return None


def _cargar_factura(order_id):
    if not os.path.exists(FACTURAS_PATH):
        return None
    try:
        with open(FACTURAS_PATH) as f:
            facturas = json.load(f)
    except (json.JSONDecodeError, ValueError):
        return None
    for fac in facturas:
        if fac.get('id') == order_id:
            return fac
    return None


def procesar_informacion_pago(gm, content):
    order_id = str(gm.value(content, ECSNS.idPedido) or '')
    user_id = str(gm.value(content, ECSNS.comprador) or '')
    metodo = str(gm.value(content, ECSNS.metodoPago) or '')
    total_lit = gm.value(content, ECSNS.total)
    total = float(total_lit) if total_lit is not None else 0.0
    vendedor = str(gm.value(content, ECSNS.vendedorExternoId)
                   or gm.value(content, ECSNS.vendedor) or '') or None

    entry = {
        'orderId': order_id,
        'userId': user_id,
        'metodoPago': metodo,
        'proveedorPagoVendedor': None,
        'vendedorExternoId': vendedor,
        'importeTotal': round(total, 2),
        'estadoPago': 'pendiente',
    }
    upsert_pago(entry)
    logger.info(f'[GestorPagos] Información de pago registrada — pedido {order_id}, estado pendiente')


def cobrar_usuario(pago):
    metodo = pago.get('metodoPago', '')
    importe = pago.get('importeTotal', 0)
    logger.info(f'[GestorPagos] Cobro simulado: {importe} EUR con {metodo} — usuario {pago.get("userId")}')
    actualizar_pago(pago['orderId'], estadoPago='cobrado')
    return True


def consultar_proveedor_vendedor(vendedor_nombre, order_id):
    global mss_cnt
    addr = _buscar_address_vendedor(vendedor_nombre)
    if addr is None:
        logger.warning(f'[GestorPagos] Vendedor {vendedor_nombre} no encontrado para proveedor de pago')
        return None
    gmess = Graph()
    gmess.bind('ecsns', ECSNS)
    node = ECSNS[f'prov-pago-{order_id}']
    gmess.add((node, RDF.type,       ECSNS.ObtenerProveedorPago))
    gmess.add((node, ECSNS.idPedido, Literal(order_id)))
    gmess.add((node, ECSNS.vendedor, Literal(vendedor_nombre)))
    try:
        gr_resp = send_message(
            build_message(gmess, perf=ACL.request, sender=PagosAgent.uri,
                          content=node, msgcnt=mss_cnt),
            addr,
        )
        mss_cnt += 1
        for s, p, o in gr_resp.triples((None, RDF.type, ECSNS.ReciboProveedorPago)):
            proveedor = str(gr_resp.value(s, ECSNS.datosProveedor) or '')
            if proveedor:
                actualizar_pago(order_id, proveedorPagoVendedor=proveedor)
                logger.info(f'[GestorPagos] Proveedor de pago de {vendedor_nombre}: {proveedor}')
                return proveedor
    except Exception as e:
        mss_cnt += 1
        logger.warning(f'[GestorPagos] Error consultando proveedor de {vendedor_nombre}: {e}')
    return None


def pagar_vendedor_externo(pago):
    global mss_cnt
    vendedor = pago.get('vendedorExternoId')
    if not vendedor:
        return True
    proveedor = pago.get('proveedorPagoVendedor')
    if not proveedor:
        proveedor = consultar_proveedor_vendedor(vendedor, pago['orderId'])
        pago = buscar_pago(pago['orderId']) or pago
    if not proveedor:
        return False
    addr = _buscar_address_vendedor(vendedor)
    if addr is None:
        return False
    gmess = Graph()
    gmess.bind('ecsns', ECSNS)
    node = ECSNS['pagar-vend-' + pago['orderId']]
    gmess.add((node, RDF.type,       ECSNS.PagarVendedor))
    gmess.add((node, ECSNS.idPedido, Literal(pago['orderId'])))
    gmess.add((node, ECSNS.vendedor, Literal(vendedor)))
    gmess.add((node, ECSNS.total,    Literal(pago.get('importeTotal', 0))))
    gmess.add((node, ECSNS.datosProveedor, Literal(proveedor)))
    try:
        send_message(
            build_message(gmess, perf=ACL.request, sender=PagosAgent.uri,
                          content=node, msgcnt=mss_cnt),
            addr,
        )
        mss_cnt += 1
        logger.info(
            f'[GestorPagos] Pago simulado al vendedor {vendedor} '
            f'({proveedor}): {pago.get("importeTotal")} EUR'
        )
        return True
    except Exception as e:
        mss_cnt += 1
        logger.warning(f'[GestorPagos] Error pagando a vendedor {vendedor}: {e}')
        return False


def notificar_experiencia_compra(comprador, factura, productos):
    global mss_cnt, experiencia_address
    if experiencia_address is None:
        experiencia_address = get_agent_address(ECSNS['Ag.Experiencia'])
    if experiencia_address is None:
        logger.warning('[GestorPagos] AgenteExperiencia no encontrado en DS')
        return
    gmess = Graph()
    gmess.bind('ecsns', ECSNS)
    compra_node = ECSNS['compra-' + factura['id']]
    gmess.add((compra_node, RDF.type,        ECSNS.CompraFinalizada))
    gmess.add((compra_node, ECSNS.comprador, Literal(comprador)))
    gmess.add((compra_node, ECSNS.idPedido,  Literal(factura['id'])))
    gmess.add((compra_node, ECSNS.total,     Literal(factura['total'])))
    gmess.add((compra_node, ECSNS.fecha,     Literal(factura['fecha'])))
    for i, p in enumerate(productos):
        pn = ECSNS['compra-prod-' + str(i)]
        gmess.add((compra_node, ECSNS.tieneProducto, pn))
        gmess.add((pn, ECSNS.idProducto, Literal(p['id'])))
        gmess.add((pn, ECSNS.nombre,     Literal(p.get('nombre', ''))))
        gmess.add((pn, ECSNS.precio,     Literal(p.get('precio', 0))))
        gmess.add((pn, ECSNS.cantidad,   Literal(p.get('cantidad', 1))))
    send_message(
        build_message(gmess, perf=ACL.inform, sender=PagosAgent.uri,
                      receiver=agn.AgenteExperiencia, content=compra_node, msgcnt=mss_cnt),
        experiencia_address,
    )
    mss_cnt += 1
    logger.info(f'[GestorPagos] Compra finalizada notificada a Experiencia — {comprador}')


def finalizar_compra(order_id):
    pago = buscar_pago(order_id)
    if not pago:
        logger.warning(f'[GestorPagos] No hay registro de pago para {order_id}')
        return
    if pago.get('estadoPago') == 'cobrado':
        factura = _cargar_factura(order_id)
        if factura:
            notificar_experiencia_compra(
                pago.get('userId', factura.get('comprador', '')),
                factura,
                factura.get('productos', []),
            )
        return

    cobrar_usuario(pago)
    pago = buscar_pago(order_id)
    if pago.get('vendedorExternoId'):
        pagar_vendedor_externo(pago)

    factura = _cargar_factura(order_id)
    if factura:
        notificar_experiencia_compra(
            pago.get('userId', factura.get('comprador', '')),
            factura,
            factura.get('productos', []),
        )


def procesar_confirmacion_envio(gm, content):
    order_id = str(gm.value(content, ECSNS.idPedido) or '')
    pago = buscar_pago(order_id)
    if not pago:
        logger.warning(f'[GestorPagos] Confirmación envío sin registro de pago: {order_id}')
        return
    if pago.get('estadoPago') != 'pendiente':
        logger.info(f'[GestorPagos] Confirmación envío ignorada (estado={pago.get("estadoPago")}) — {order_id}')
        return
    total_lit = gm.value(content, ECSNS.total)
    if total_lit is not None:
        actualizar_pago(order_id, importeTotal=round(float(total_lit), 2))
    vendedor = str(gm.value(content, ECSNS.vendedorExternoId)
                   or gm.value(content, ECSNS.vendedor) or '')
    if vendedor and not pago.get('vendedorExternoId'):
        actualizar_pago(order_id, vendedorExternoId=vendedor)
    finalizar_compra(order_id)


def procesar_recibo_proveedor(gm, content):
    order_id = str(gm.value(content, ECSNS.idPedido) or '')
    proveedor = str(gm.value(content, ECSNS.datosProveedor) or '')
    if order_id and proveedor:
        actualizar_pago(order_id, proveedorPagoVendedor=proveedor)
        logger.info(f'[GestorPagos] Proveedor guardado para {order_id}: {proveedor}')


def reembolsar_vendedor(pago, productos=None):
    global mss_cnt
    vendedor = pago.get('vendedorExternoId')
    if not vendedor:
        return True
    proveedor = pago.get('proveedorPagoVendedor')
    if not proveedor:
        proveedor = consultar_proveedor_vendedor(vendedor, pago['orderId'])
    addr = _buscar_address_vendedor(vendedor)
    if addr is None:
        return False
    gmess = Graph()
    gmess.bind('ecsns', ECSNS)
    node = ECSNS['reemb-vend-' + pago['orderId']]
    gmess.add((node, RDF.type,        ECSNS.PedirReembolso))
    gmess.add((node, ECSNS.idFactura, Literal(pago['orderId'])))
    gmess.add((node, ECSNS.comprador, Literal(pago.get('userId', ''))))
    if productos:
        for i, pid in enumerate(productos):
            pn = ECSNS[f'reemb-prod-{i}']
            gmess.add((node, ECSNS.tieneProducto, pn))
            gmess.add((pn, ECSNS.idProducto, Literal(pid)))
    try:
        send_message(
            build_message(gmess, perf=ACL.request, sender=PagosAgent.uri,
                          content=node, msgcnt=mss_cnt),
            addr,
        )
        mss_cnt += 1
        logger.info(f'[GestorPagos] Reembolso solicitado al vendedor {vendedor} vía {proveedor}')
        return True
    except Exception as e:
        mss_cnt += 1
        logger.warning(f'[GestorPagos] Error reembolso vendedor: {e}')
        return False


def devolver_dinero_usuario(pago):
    metodo = pago.get('metodoPago', '')
    importe = pago.get('importeTotal', 0)
    logger.info(
        f'[GestorPagos] Devolución simulada al usuario {pago.get("userId")}: '
        f'{importe} EUR con {metodo}'
    )
    return True


def procesar_solicitud_reembolso(gm, content, sender):
    order_id = str(gm.value(content, ECSNS.idPedido) or '')
    user_id = str(gm.value(content, ECSNS.comprador) or '')
    pago = buscar_pago(order_id)
    if not pago:
        logger.warning(f'[GestorPagos] Reembolso sin registro de pago: {order_id}')
        return build_message(Graph(), ACL.failure, sender=PagosAgent.uri,
                             receiver=sender, msgcnt=mss_cnt)
    if (pago.get('userId') or '').strip().casefold() != (user_id or '').strip().casefold():
        logger.warning(f'[GestorPagos] Reembolso: comprador no coincide para {order_id}')
        return build_message(Graph(), ACL.failure, sender=PagosAgent.uri,
                             receiver=sender, msgcnt=mss_cnt)
    if pago.get('estadoPago') == 'devuelto':
        gr = Graph()
        gr.bind('ecsns', ECSNS)
        ack = ECSNS['ack-reemb-' + order_id]
        gr.add((ack, RDF.type,          ECSNS.AckActualizacion))
        gr.add((ack, ECSNS.idFactura,   Literal(order_id)))
        gr.add((ack, ECSNS.actualizado, Literal(True)))
        return build_message(gr, ACL.inform, sender=PagosAgent.uri,
                             receiver=sender, content=ack, msgcnt=mss_cnt)

    factura = _cargar_factura(order_id)
    productos_ext = []
    if factura:
        productos_ext = [
            p['id'] for p in factura.get('productos', [])
            if p.get('vendedor', 'tienda') != 'tienda'
        ]

    if pago.get('vendedorExternoId'):
        reembolsar_vendedor(pago, productos_ext or None)
    devolver_dinero_usuario(pago)
    actualizar_pago(order_id, estadoPago='devuelto')

    gr = Graph()
    gr.bind('ecsns', ECSNS)
    ack = ECSNS['ack-reemb-' + order_id]
    gr.add((ack, RDF.type,          ECSNS.AckActualizacion))
    gr.add((ack, ECSNS.idFactura,   Literal(order_id)))
    gr.add((ack, ECSNS.actualizado, Literal(True)))
    logger.info(f'[GestorPagos] Devolución procesada — pedido {order_id}')
    return build_message(gr, ACL.inform, sender=PagosAgent.uri,
                         receiver=sender, content=ack, msgcnt=mss_cnt)


def tidyup():
    cola1.put(0)


def _signal_handler(signum, frame):
    logger.info(f'[GestorPagos] Señal {signum}, parada limpia...')
    tidyup()


@app.route('/stop')
@app.route('/Stop')
def stop():
    tidyup()
    shutdown_server()
    return 'Parando AgenteGestorPagos'


@app.route('/comm', methods=['GET', 'POST'])
def comunicacion():
    global mss_cnt
    logger.info('[GestorPagos] Mensaje recibido')
    message = request.args.get('content') or request.form.get('content')
    if not message:
        return ('<html><head><title>AgenteGestorPagos</title></head>'
                '<body style="font-family:sans-serif;padding:32px"><h2>AgenteGestorPagos</h2>'
                '<p><strong>Estado:</strong> activo &nbsp;|&nbsp; <strong>Puerto:</strong> '
                + str(port) + '</p>'
                '<p style="color:#666">Endpoint ACL/RDF entre agentes.</p>'
                '</body></html>'), 200, {'Content-Type': 'text/html; charset=utf-8'}
    try:
        gm = Graph()
        gm.parse(data=message, format='xml')
    except Exception as e:
        logger.warning(f'[GestorPagos] /comm parse error: {e}')
        gr = build_message(Graph(), ACL['not-understood'], sender=PagosAgent.uri, msgcnt=mss_cnt)
        mss_cnt += 1
        return gr.serialize(format='xml')

    msgdic = get_message_properties(gm)
    if msgdic is None:
        gr = build_message(Graph(), ACL['not-understood'],
                           sender=PagosAgent.uri, msgcnt=mss_cnt)
        mss_cnt += 1
        return gr.serialize(format='xml')

    perf = msgdic.get('performative')
    content = msgdic.get('content')
    accion = gm.value(subject=content, predicate=RDF.type) if content else None
    sender = msgdic.get('sender')

    if perf == ACL.inform and accion == ECSNS.InformacionPago:
        procesar_informacion_pago(gm, content)
        gr = build_message(Graph(), ACL.confirm,
                           sender=PagosAgent.uri, receiver=sender, msgcnt=mss_cnt)

    elif perf == ACL.inform and accion == ECSNS.ConfirmacionEnvio:
        procesar_confirmacion_envio(gm, content)
        gr = build_message(Graph(), ACL.confirm,
                           sender=PagosAgent.uri, receiver=sender, msgcnt=mss_cnt)

    elif perf == ACL.inform and accion == ECSNS.ReciboProveedorPago:
        procesar_recibo_proveedor(gm, content)
        gr = build_message(Graph(), ACL.confirm,
                           sender=PagosAgent.uri, receiver=sender, msgcnt=mss_cnt)

    elif perf == ACL.request and accion == ECSNS.SolicitudReembolso:
        gr = procesar_solicitud_reembolso(gm, content, sender)

    else:
        gr = build_message(Graph(), ACL['not-understood'],
                           sender=PagosAgent.uri, msgcnt=mss_cnt)

    mss_cnt += 1
    return gr.serialize(format='xml')


def agentbehavior1(cola):
    register_message()
    logger.info('[GestorPagos] Registrado y escuchando')
    fin = False
    while not fin:
        time.sleep(1)
        if not cola.empty() and cola.get() == 0:
            fin = True


if __name__ == '__main__':
    os.makedirs(os.path.join(os.path.dirname(__file__), 'data'), exist_ok=True)
    if not os.path.exists(PAGOS_PATH):
        guardar_pagos([])
    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)
    ab1 = Process(target=agentbehavior1, args=(cola1,))
    ab1.start()
    app.run(host=flask_host, port=port)
    ab1.join()
    logger.info('[GestorPagos] Fin')
