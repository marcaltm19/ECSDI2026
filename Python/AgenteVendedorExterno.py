import argparse
import json
import logging
import os
import socket
import sys
import time
import uuid
from datetime import datetime, timedelta
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
parser.add_argument('--port', type=int, default=9007)
parser.add_argument('--dhost', default=None)
parser.add_argument('--dport', type=int, default=9000)
parser.add_argument('--nombre', type=str, default='VendedorExterno1')
args = parser.parse_args()

logger = config_logger(level=1)
port = args.port
hostname = socket.gethostname()
hostaddr = os.environ.get('ECSDI_PUBLIC_HOST') or hostname
flask_host = '0.0.0.0' if args.open else hostname
dport = args.dport
dhostname = os.environ.get('ECSDI_DHOST') or args.dhost or socket.gethostname()
nombre_vendedor = args.nombre

app = Flask(__name__)
if not args.verbose:
    logging.getLogger('werkzeug').setLevel(logging.ERROR)

agn = Namespace('http://www.agentes.org#')
mss_cnt = 0

CATA_PATH    = os.path.join(os.path.dirname(__file__), 'data', f'catalogo_{nombre_vendedor}.json')
PEDIDOS_PATH = os.path.join(os.path.dirname(__file__), 'data', f'pedidos_{nombre_vendedor}.json')

VendedorAgent = Agent(
    nombre_vendedor,
    agn[nombre_vendedor],
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
compr_address = None

CATALOGO_INICIAL = [
    {'id': f'EXT-{nombre_vendedor}-001', 'nombre': 'Auriculares Premium BT',
     'categoria': 'electronica', 'precio': 89.99, 'peso': 0.3, 'stock': 15,
     'valoracion': 4.3, 'vendedor': nombre_vendedor, 'gestion_envio': 'tienda'},
    {'id': f'EXT-{nombre_vendedor}-002', 'nombre': 'Mochila Urbana 30L',
     'categoria': 'hogar', 'precio': 45.50, 'peso': 0.8, 'stock': 30,
     'valoracion': 4.1, 'vendedor': nombre_vendedor, 'gestion_envio': 'vendedor'},
    {'id': f'EXT-{nombre_vendedor}-003', 'nombre': 'Libro Sistemas Distribuidos',
     'categoria': 'libros', 'precio': 32.00, 'peso': 0.5, 'stock': 50,
     'valoracion': 4.8, 'vendedor': nombre_vendedor, 'gestion_envio': 'tienda'},
]


def register_message():
    global mss_cnt
    gmess = Graph()
    gmess.bind('foaf', FOAF)
    gmess.bind('dso', DSO)
    reg_obj = agn[VendedorAgent.name + '-Register']
    gmess.add((reg_obj, RDF.type,       DSO.Register))
    gmess.add((reg_obj, DSO.Uri,        VendedorAgent.uri))
    gmess.add((reg_obj, FOAF.name,      Literal(VendedorAgent.name)))
    gmess.add((reg_obj, DSO.Address,    Literal(VendedorAgent.address)))
    gmess.add((reg_obj, DSO.AgentType,  ECSNS['Ag.VendedorExterno']))
    gr = send_message(
        build_message(gmess, perf=ACL.request, sender=VendedorAgent.uri,
                      receiver=DirectoryAgent.uri, content=reg_obj, msgcnt=mss_cnt),
        DirectoryAgent.address,
    )
    mss_cnt += 1
    return gr


def cargar_catalogo():
    if os.path.exists(CATA_PATH):
        with open(CATA_PATH) as f:
            return json.load(f)
    return list(CATALOGO_INICIAL)


def guardar_catalogo(cat):
    with open(CATA_PATH, 'w') as f:
        json.dump(cat, f, indent=2)


def cargar_pedidos():
    if os.path.exists(PEDIDOS_PATH):
        with open(PEDIDOS_PATH) as f:
            return json.load(f)
    return []


def guardar_pedidos(peds):
    with open(PEDIDOS_PATH, 'w') as f:
        json.dump(peds, f, indent=2)


def get_comprador_address():
    global mss_cnt
    gmess = Graph()
    gmess.bind('dso', DSO)
    search_obj = agn['Search-' + str(mss_cnt)]
    gmess.add((search_obj, RDF.type,      DSO.Search))
    gmess.add((search_obj, DSO.AgentType, ECSNS['Ag.Comprador']))
    msg = build_message(gmess, perf=ACL.request, sender=VendedorAgent.uri,
                        receiver=DirectoryAgent.uri, content=search_obj, msgcnt=mss_cnt)
    r = http_requests.get(DirectoryAgent.address,
                          params={'content': msg.serialize(format='xml')})
    mss_cnt += 1
    gr = Graph()
    gr.parse(data=r.text, format='xml')
    for s, p, o in gr:
        if p == DSO.Address:
            return str(o)
    return None


def anunciar_catalogo():
    global mss_cnt, compr_address
    if compr_address is None:
        compr_address = get_comprador_address()
    if compr_address is None:
        logger.warning(f'[{nombre_vendedor}] AgenteComprador no encontrado en DS')
        return

    catalogo = cargar_catalogo()
    gmess = Graph()
    gmess.bind('ecsns', ECSNS)
    cat_node = ECSNS[f'catalogo-{nombre_vendedor}']
    gmess.add((cat_node, RDF.type,              ECSNS.CatalogoExterno))
    gmess.add((cat_node, ECSNS.nombreVendedor,  Literal(nombre_vendedor)))

    activos = 0
    for prod in catalogo:
        if prod.get('stock', 0) <= 0:
            continue
        p_node = ECSNS[prod['id']]
        gmess.add((cat_node, ECSNS.tieneProducto, p_node))
        gmess.add((p_node, RDF.type,              ECSNS.ProductoExterno))
        gmess.add((p_node, ECSNS.idProducto,      Literal(prod['id'])))
        gmess.add((p_node, ECSNS.nombre,          Literal(prod['nombre'])))
        gmess.add((p_node, ECSNS.categoria,       Literal(prod['categoria'])))
        gmess.add((p_node, ECSNS.precio,          Literal(prod['precio'])))
        gmess.add((p_node, ECSNS.peso,            Literal(prod['peso'])))
        gmess.add((p_node, ECSNS.valoracion,      Literal(prod['valoracion'])))
        gmess.add((p_node, ECSNS.vendedor,        Literal(nombre_vendedor)))
        gmess.add((p_node, ECSNS.gestionEnvio,    Literal(prod['gestion_envio'])))
        activos += 1

    send_message(
        build_message(gmess, perf=ACL.inform, sender=VendedorAgent.uri,
                      receiver=agn.AgenteComprador, content=cat_node, msgcnt=mss_cnt),
        compr_address,
    )
    mss_cnt += 1
    logger.info(f'[{nombre_vendedor}] Catálogo anunciado ({activos} productos activos)')


def procesar_pedido_externo(gm, content):
    pedido_id = str(gm.value(content, ECSNS.idPedido)   or 'SIN-ID')
    comprador = str(gm.value(content, ECSNS.comprador)   or 'Anonimo')
    direccion = str(gm.value(content, ECSNS.direccion)   or '')
    prioridad = str(gm.value(content, ECSNS.prioridad)   or 'normal')

    productos = []
    for prod_node in gm.objects(content, ECSNS.tieneProducto):
        productos.append({
            'id':       str(gm.value(prod_node, ECSNS.idProducto)),
            'cantidad': int(gm.value(prod_node, ECSNS.cantidad) or 1),
        })

    dias_map = {'urgente': 1, 'normal': 4, 'economica': 10}
    dias = dias_map.get(prioridad, 4)
    fecha_entrega = (datetime.now() + timedelta(days=dias)).strftime('%Y-%m-%d')

    pedidos = cargar_pedidos()
    pedidos.append({
        'pedido_id': pedido_id, 'comprador': comprador, 'direccion': direccion,
        'prioridad': prioridad, 'productos': productos,
        'fecha_entrega': fecha_entrega, 'fecha_registro': datetime.now().isoformat(),
        'estado': 'en_proceso',
    })
    guardar_pedidos(pedidos)
    logger.info(f'[{nombre_vendedor}] Pedido {pedido_id} — entrega: {fecha_entrega}')

    gr = Graph()
    gr.bind('ecsns', ECSNS)
    resp = ECSNS['resp-' + pedido_id]
    gr.add((resp, RDF.type,             ECSNS.RespuestaPedidoExterno))
    gr.add((resp, ECSNS.idPedido,       Literal(pedido_id)))
    gr.add((resp, ECSNS.fechaEntrega,   Literal(fecha_entrega)))
    gr.add((resp, ECSNS.transportista,  Literal(f'Mensajería {nombre_vendedor}')))
    gr.add((resp, ECSNS.estado,         Literal('confirmado')))
    return gr


def procesar_obtener_proveedor_pago(gm, content):
    pedido_id = str(gm.value(content, ECSNS.idPedido) or '')
    gr = Graph()
    gr.bind('ecsns', ECSNS)
    resp = ECSNS['recibo-prov-' + (pedido_id or nombre_vendedor)]
    proveedor = f'paypal_{nombre_vendedor}'
    gr.add((resp, RDF.type,             ECSNS.ReciboProveedorPago))
    gr.add((resp, ECSNS.idPedido,       Literal(pedido_id)))
    gr.add((resp, ECSNS.datosProveedor, Literal(proveedor)))
    logger.info(f'[{nombre_vendedor}] Proveedor de pago: {proveedor}')
    return gr


def procesar_pagar_vendedor(gm, content):
    pedido_id = str(gm.value(content, ECSNS.idPedido) or '')
    proveedor = str(gm.value(content, ECSNS.datosProveedor) or '')
    total = gm.value(content, ECSNS.total)
    importe = float(total) if total is not None else 0.0
    logger.info(
        f'[{nombre_vendedor}] Pago recibido — pedido {pedido_id}, '
        f'{importe} EUR vía {proveedor}'
    )
    gr = Graph()
    gr.bind('ecsns', ECSNS)
    ack = ECSNS['ack-pago-' + pedido_id]
    gr.add((ack, RDF.type,          ECSNS.AckActualizacion))
    gr.add((ack, ECSNS.idPedido,    Literal(pedido_id)))
    gr.add((ack, ECSNS.actualizado, Literal(True)))
    return gr


def procesar_devolucion(gm, content):
    factura_id = str(gm.value(content, ECSNS.idFactura) or '')
    comprador  = str(gm.value(content, ECSNS.comprador) or '')
    ids_devueltos = [str(gm.value(pn, ECSNS.idProducto) or '')
                     for pn in gm.objects(content, ECSNS.tieneProducto)]

    pedidos = cargar_pedidos()
    for p in pedidos:
        if p.get('pedido_id') == factura_id:
            p['estado'] = 'devuelto'
            p['fecha_devolucion'] = datetime.now().isoformat()
            break
    guardar_pedidos(pedidos)
    logger.info(f'[{nombre_vendedor}] Devolución registrada — factura {factura_id} '
                f'productos {ids_devueltos}')

    gr = Graph()
    gr.bind('ecsns', ECSNS)
    ack = ECSNS['ack-dev-' + factura_id]
    gr.add((ack, RDF.type,          ECSNS.AckActualizacion))
    gr.add((ack, ECSNS.idFactura,   Literal(factura_id)))
    gr.add((ack, ECSNS.actualizado, Literal(True)))
    return gr


def procesar_actualizacion(gm, content):
    catalogo  = cargar_catalogo()
    prod_id   = str(gm.value(content, ECSNS.idProducto) or '')
    new_stock  = gm.value(content, ECSNS.stock)
    new_precio = gm.value(content, ECSNS.precio)
    actualizado = False
    for prod in catalogo:
        if prod['id'] == prod_id:
            if new_stock  is not None: prod['stock']  = int(new_stock)
            if new_precio is not None: prod['precio'] = float(new_precio)
            actualizado = True
            break
    guardar_catalogo(catalogo)
    logger.info(f'[{nombre_vendedor}] Catálogo actualizado — {prod_id}: ok={actualizado}')

    gr = Graph()
    gr.bind('ecsns', ECSNS)
    ack = ECSNS['ack-catalogo']
    gr.add((ack, RDF.type,          ECSNS.AckActualizacion))
    gr.add((ack, ECSNS.idProducto,  Literal(prod_id)))
    gr.add((ack, ECSNS.actualizado, Literal(actualizado)))
    return gr


@app.route('/stop')
def stop():
    cola1.put(0)
    shutdown_server()
    return f'Parando {nombre_vendedor}'


@app.route('/comm', methods=['GET', 'POST'])
def comunicacion():
    global mss_cnt
    logger.info(f'[{nombre_vendedor}] Mensaje recibido')
    message = request.args.get('content') or request.form.get('content')
    if not message:
        return ('<html><head><title>AgenteVendedorExterno</title></head>'
                '<body style="font-family:sans-serif;padding:32px"><h2>AgenteVendedorExterno — ' + nombre_vendedor + '</h2>'
                '<p><strong>Estado:</strong> activo &nbsp;|&nbsp; <strong>Puerto:</strong> ' + str(port) + '</p>'
                '<p style="color:#666">Endpoint ACL/RDF entre agentes.</p>'
                '</body></html>'), 200, {'Content-Type': 'text/html; charset=utf-8'}
    try:
        gm = Graph()
        gm.parse(data=message, format='xml')
    except Exception as e:
        logger.warning(f'[{nombre_vendedor}] /comm parse error: {e}')
        mss_cnt += 1
        return Graph().serialize(format='xml')
    msgdic = get_message_properties(gm)

    if msgdic is None:
        gr = build_message(Graph(), ACL['not-understood'],
                           sender=VendedorAgent.uri, msgcnt=mss_cnt)
    else:
        content = msgdic.get('content')
        accion  = gm.value(subject=content, predicate=RDF.type)
        perf    = msgdic.get('performative')

        if perf == ACL.request and accion == ECSNS.PedidoExterno:
            resp = procesar_pedido_externo(gm, content)
            gr   = build_message(resp, ACL.inform, sender=VendedorAgent.uri,
                                 receiver=msgdic['sender'], msgcnt=mss_cnt)
        elif perf == ACL.request and accion == ECSNS.ActualizarCatalogo:
            resp = procesar_actualizacion(gm, content)
            gr   = build_message(resp, ACL.inform, sender=VendedorAgent.uri,
                                 receiver=msgdic['sender'], msgcnt=mss_cnt)
        elif perf == ACL.request and accion == ECSNS.ObtenerProveedorPago:
            resp = procesar_obtener_proveedor_pago(gm, content)
            gr   = build_message(resp, ACL.inform, sender=VendedorAgent.uri,
                                 receiver=msgdic['sender'], msgcnt=mss_cnt)
        elif perf == ACL.request and accion == ECSNS.PagarVendedor:
            resp = procesar_pagar_vendedor(gm, content)
            gr   = build_message(resp, ACL.inform, sender=VendedorAgent.uri,
                                 receiver=msgdic['sender'], msgcnt=mss_cnt)
        elif perf == ACL.request and accion == ECSNS.PedirReembolso:
            resp = procesar_devolucion(gm, content)
            gr   = build_message(resp, ACL.inform, sender=VendedorAgent.uri,
                                 receiver=msgdic['sender'], msgcnt=mss_cnt)
        else:
            gr = build_message(Graph(), ACL['not-understood'],
                               sender=VendedorAgent.uri, msgcnt=mss_cnt)

    mss_cnt += 1
    return gr.serialize(format='xml')


def agentbehavior1(cola):
    register_message()
    logger.info(f'[{nombre_vendedor}] Registrado — anunciando catálogo...')
    anunciar_catalogo()
    tick = 0
    fin  = False
    while not fin:
        time.sleep(1)
        tick += 1
        if tick >= 60:
            anunciar_catalogo()
            tick = 0
        if not cola.empty() and cola.get() == 0:
            fin = True


if __name__ == '__main__':
    os.makedirs(os.path.join(os.path.dirname(__file__), 'data'), exist_ok=True)
    if not os.path.exists(CATA_PATH):
        guardar_catalogo(CATALOGO_INICIAL)
    ab1 = Process(target=agentbehavior1, args=(cola1,))
    ab1.start()
    app.run(host=flask_host, port=port)
    ab1.join()
    logger.info(f'[{nombre_vendedor}] Fin')
