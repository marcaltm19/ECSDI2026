import argparse
import json
import logging
import os
import socket
import sys
import time
import uuid
from datetime import datetime
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
parser.add_argument('--port', type=int, default=9002)
parser.add_argument('--dhost', default=None)
parser.add_argument('--dport', type=int, default=9000)
args = parser.parse_args()

logger = config_logger(level=1)
port = args.port
hostname = socket.gethostname()
hostaddr = hostname if not args.open else '0.0.0.0'
dport = args.dport
dhostname = args.dhost if args.dhost else socket.gethostname()

app = Flask(__name__)
if not args.verbose:
    logging.getLogger('werkzeug').setLevel(logging.ERROR)

agn = Namespace('http://www.agentes.org#')
mss_cnt = 0
FACTURAS_PATH = os.path.join(os.path.dirname(__file__), 'data', 'facturas.json')

GestorAgent = Agent(
    'AgenteGestorPedidos',
    agn.AgenteGestorPedidos,
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
logistico_address = None


def register_message():
    global mss_cnt
    gmess = Graph()
    gmess.bind('foaf', FOAF)
    gmess.bind('dso', DSO)
    reg_obj = agn[GestorAgent.name + '-Register']
    gmess.add((reg_obj, RDF.type, DSO.Register))
    gmess.add((reg_obj, DSO.Uri, GestorAgent.uri))
    gmess.add((reg_obj, FOAF.name, Literal(GestorAgent.name)))
    gmess.add((reg_obj, DSO.Address, Literal(GestorAgent.address)))
    gmess.add((reg_obj, DSO.AgentType, ECSNS['Ag.GestorDePedidos']))
    gr = send_message(
        build_message(gmess, perf=ACL.request, sender=GestorAgent.uri,
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
    gmess.add((search_obj, RDF.type, DSO.Search))
    gmess.add((search_obj, DSO.AgentType, agent_type))

    msg = build_message(gmess, perf=ACL.request, sender=GestorAgent.uri,
                        receiver=DirectoryAgent.uri, content=search_obj, msgcnt=mss_cnt)

    response = http_requests.get(
        DirectoryAgent.address,
        params={'content': msg.serialize(format='xml')}
    )
    mss_cnt += 1

    gr = Graph()
    gr.parse(data=response.text, format='xml')
    for s, p, o in gr:
        if p == DSO.Address:
            return str(o)
    return None


def generar_factura(productos, comprador, direccion, metodo_pago):
    factura_id = 'FAC-' + str(uuid.uuid4())[:8].upper()
    total = sum(p['precio'] * p.get('cantidad', 1) for p in productos)
    factura = {
        'id': factura_id,
        'comprador': comprador,
        'fecha': datetime.now().isoformat(),
        'productos': productos,
        'total': round(total, 2),
        'direccion': direccion,
        'metodo_pago': metodo_pago,
    }
    if os.path.exists(FACTURAS_PATH):
        with open(FACTURAS_PATH) as f:
            facturas = json.load(f)
    else:
        facturas = []
    facturas.append(factura)
    with open(FACTURAS_PATH, 'w') as f:
        json.dump(facturas, f, indent=2)
    logger.info(f'[GestorPedidos] Factura {factura_id} generada — Total: {total}€')
    return factura


def notificar_logistico(pedido):
    global mss_cnt, logistico_address
    if logistico_address is None:
        logistico_address = get_agent_address(ECSNS['Ag.Logistico'])
    if logistico_address is None:
        logger.warning('[GestorPedidos] AgenteLogistico no encontrado en DS')
        return

    gmess = Graph()
    gmess.bind('ecsns', ECSNS)
    ped_obj = ECSNS['pedido-' + pedido['id']]
    gmess.add((ped_obj, RDF.type,           ECSNS.Pedido))
    gmess.add((ped_obj, ECSNS.idPedido,     Literal(pedido['id'])))
    gmess.add((ped_obj, ECSNS.direccion,    Literal(pedido['direccion'])))
    gmess.add((ped_obj, ECSNS.prioridad,    Literal(pedido['prioridad'])))
    for i, p in enumerate(pedido['productos']):
        prod_node = ECSNS['ped-prod-' + str(i)]
        gmess.add((ped_obj,   ECSNS.tieneProducto, prod_node))
        gmess.add((prod_node, ECSNS.idProducto,    Literal(p['id'])))
        gmess.add((prod_node, ECSNS.cantidad,      Literal(p.get('cantidad', 1))))
        gmess.add((prod_node, ECSNS.peso,          Literal(p.get('peso', 0))))
        gmess.add((prod_node, ECSNS.precio,        Literal(p.get('precio', 0))))

    send_message(
        build_message(gmess, perf=ACL.request, sender=GestorAgent.uri,
                      receiver=agn.AgenteLogistico, content=ped_obj, msgcnt=mss_cnt),
        logistico_address,
    )
    mss_cnt += 1


def procesar_compra(gm, content):
    comprador   = str(gm.value(subject=content, predicate=ECSNS.comprador) or 'Anonimo')
    direccion   = str(gm.value(subject=content, predicate=ECSNS.direccion) or '')
    prioridad   = str(gm.value(subject=content, predicate=ECSNS.prioridad) or 'normal')
    metodo_pago = str(gm.value(subject=content, predicate=ECSNS.metodoPago) or '')

    productos = []
    for prod_node in gm.objects(content, ECSNS.tieneProducto):
        productos.append({
            'id':       str(gm.value(prod_node, ECSNS.idProducto)),
            'nombre':   str(gm.value(prod_node, ECSNS.nombre) or ''),
            'precio':   float(gm.value(prod_node, ECSNS.precio) or 0),
            'cantidad': int(gm.value(prod_node, ECSNS.cantidad) or 1),
            'peso':     float(gm.value(prod_node, ECSNS.peso) or 0),
        })

    factura = generar_factura(productos, comprador, direccion, metodo_pago)

    pedido_id = 'PED-' + str(uuid.uuid4())[:8].upper()
    pedido = {'id': pedido_id, 'productos': productos,
              'direccion': direccion, 'prioridad': prioridad}
    notificar_logistico(pedido)

    gr = Graph()
    gr.bind('ecsns', ECSNS)
    fac_node = ECSNS['factura-' + factura['id']]
    gr.add((fac_node, RDF.type,         ECSNS.Factura))
    gr.add((fac_node, ECSNS.idFactura,  Literal(factura['id'])))
    gr.add((fac_node, ECSNS.total,      Literal(factura['total'])))
    gr.add((fac_node, ECSNS.fecha,      Literal(factura['fecha'])))
    return gr


@app.route('/stop')
def stop():
    cola1.put(0)
    shutdown_server()
    return 'Parando AgenteGestorPedidos'


@app.route('/comm', methods=['GET', 'POST'])
def comunicacion():
    global mss_cnt
    logger.info('[GestorPedidos] Mensaje recibido')
    message = request.args.get('content') or request.form.get('content')
    gm = Graph()
    gm.parse(data=message, format='xml')
    msgdic = get_message_properties(gm)
    
    if msgdic is None or msgdic.get('performative') != ACL.request:
        gr = build_message(Graph(), ACL['not-understood'],
                           sender=GestorAgent.uri, msgcnt=mss_cnt)
    else:
        content = msgdic.get('content')
        accion  = gm.value(subject=content, predicate=RDF.type)

        if accion == ECSNS.Pedido:
            resp_graph = procesar_compra(gm, content)
            gr = build_message(resp_graph, ACL.inform,
                               sender=GestorAgent.uri,
                               receiver=msgdic['sender'],
                               msgcnt=mss_cnt)
        else:
            gr = build_message(Graph(), ACL['not-understood'],
                               sender=GestorAgent.uri, msgcnt=mss_cnt)

    mss_cnt += 1
    return gr.serialize(format='xml')


def agentbehavior1(cola):
    register_message()
    logger.info('[GestorPedidos] Registrado y escuchando')
    fin = False
    while not fin:
        time.sleep(1)
        if not cola.empty() and cola.get() == 0:
            fin = True


if __name__ == '__main__':
    os.makedirs(os.path.join(os.path.dirname(__file__), 'data'), exist_ok=True)
    ab1 = Process(target=agentbehavior1, args=(cola1,))
    ab1.start()
    app.run(host=hostname, port=port)
    ab1.join()
    logger.info('[GestorPedidos] Fin')