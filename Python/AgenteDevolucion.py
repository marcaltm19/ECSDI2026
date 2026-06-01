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
parser.add_argument('--port', type=int, default=9006)
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

DEVOLUCIONES_PATH = os.path.join(os.path.dirname(__file__), 'data', 'devoluciones.json')
FACTURAS_PATH = os.path.join(os.path.dirname(__file__), 'data', 'facturas.json')
PLAZO_INSATISFACCION = 15

DevolucionAgent = Agent(
    'AgenteDevolucion',
    agn.AgenteDevolucion,
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


def register_message():
    global mss_cnt
    gmess = Graph()
    gmess.bind('foaf', FOAF)
    gmess.bind('dso', DSO)
    reg_obj = agn[DevolucionAgent.name + '-Register']
    gmess.add((reg_obj, RDF.type, DSO.Register))
    gmess.add((reg_obj, DSO.Uri, DevolucionAgent.uri))
    gmess.add((reg_obj, FOAF.name, Literal(DevolucionAgent.name)))
    gmess.add((reg_obj, DSO.Address, Literal(DevolucionAgent.address)))
    gmess.add((reg_obj, DSO.AgentType, ECSNS['Ag.Devolucion']))
    gr = send_message(
        build_message(gmess, perf=ACL.request, sender=DevolucionAgent.uri,
                      receiver=DirectoryAgent.uri, content=reg_obj, msgcnt=mss_cnt),
        DirectoryAgent.address,
    )
    mss_cnt += 1
    return gr


def cargar_devoluciones():
    if os.path.exists(DEVOLUCIONES_PATH):
        with open(DEVOLUCIONES_PATH) as f:
            return json.load(f)
    return []


def guardar_devoluciones(devoluciones):
    with open(DEVOLUCIONES_PATH, 'w') as f:
        json.dump(devoluciones, f, indent=2)


def buscar_factura(factura_id):
    if not os.path.exists(FACTURAS_PATH):
        return None
    with open(FACTURAS_PATH) as f:
        facturas = json.load(f)
    for fac in facturas:
        if fac['id'] == factura_id:
            return fac
    return None


def evaluar_devolucion(factura_id, razon, fecha_recepcion_str):
    factura = buscar_factura(factura_id)
    if factura is None:
        return False, 'Factura no encontrada', None

    razon_lower = razon.lower()
    razones_siempre = ['defectuoso', 'defecto', 'equivocado', 'incorrecto', 'roto', 'danado', 'daniado']
    if any(r in razon_lower for r in razones_siempre):
        return True, 'Devolucion aceptada: producto defectuoso o equivocado', 'MensajeriaRapida S.L.'

    try:
        fecha_recepcion = datetime.fromisoformat(fecha_recepcion_str)
        dias_transcurridos = (datetime.now() - fecha_recepcion).days
        if dias_transcurridos <= PLAZO_INSATISFACCION:
            return True, f'Devolucion aceptada: dentro del plazo ({dias_transcurridos} dias)', 'MensajeriaEstandar S.A.'
        else:
            return False, f'Devolucion rechazada: fuera del plazo de {PLAZO_INSATISFACCION} dias ({dias_transcurridos} dias transcurridos)', None
    except Exception:
        return False, 'Fecha de recepcion invalida', None


def procesar_solicitud_devolucion(gm, content):
    comprador = str(gm.value(content, ECSNS.comprador) or 'Anonimo')
    factura_id = str(gm.value(content, ECSNS.idFactura) or '')
    razon = str(gm.value(content, ECSNS.razonDevolucion) or 'insatisfaccion')
    fecha_recepcion = str(gm.value(content, ECSNS.fechaRecepcion) or datetime.now().isoformat())

    aceptada, motivo, empresa_mensajeria = evaluar_devolucion(factura_id, razon, fecha_recepcion)

    dev_id = 'DEV-' + str(uuid.uuid4())[:8].upper()
    registro = {
        'id': dev_id,
        'comprador': comprador,
        'factura_id': factura_id,
        'razon': razon,
        'fecha_solicitud': datetime.now().isoformat(),
        'fecha_recepcion': fecha_recepcion,
        'aceptada': aceptada,
        'motivo': motivo,
        'empresa_mensajeria': empresa_mensajeria,
    }
    devoluciones = cargar_devoluciones()
    devoluciones.append(registro)
    guardar_devoluciones(devoluciones)
    logger.info(f'[Devolucion] {dev_id} — Aceptada: {aceptada} — {motivo}')

    gr = Graph()
    gr.bind('ecsns', ECSNS)
    dev_node = ECSNS['devolucion-' + dev_id]
    gr.add((dev_node, RDF.type, ECSNS.Devolucion))
    gr.add((dev_node, ECSNS.idDevolucion, Literal(dev_id)))
    gr.add((dev_node, ECSNS.aceptada, Literal(aceptada)))
    gr.add((dev_node, ECSNS.motivoDevolucion, Literal(motivo)))
    if empresa_mensajeria:
        gr.add((dev_node, ECSNS.empresaMensajeria, Literal(empresa_mensajeria)))
    return gr


def procesar_consulta_devoluciones(gm, content):
    comprador = str(gm.value(content, ECSNS.comprador) or '')
    devoluciones = cargar_devoluciones()
    if comprador:
        devoluciones = [d for d in devoluciones if d.get('comprador') == comprador]

    gr = Graph()
    gr.bind('ecsns', ECSNS)
    lista_node = ECSNS['listaDevoluciones']
    gr.add((lista_node, RDF.type, ECSNS.ListaDevoluciones))
    for d in devoluciones:
        dev_node = ECSNS['devolucion-' + d['id']]
        gr.add((lista_node, ECSNS.tieneDevolucion, dev_node))
        gr.add((dev_node, ECSNS.idDevolucion, Literal(d['id'])))
        gr.add((dev_node, ECSNS.aceptada, Literal(d['aceptada'])))
        gr.add((dev_node, ECSNS.motivoDevolucion, Literal(d['motivo'])))
        if d.get('empresa_mensajeria'):
            gr.add((dev_node, ECSNS.empresaMensajeria, Literal(d['empresa_mensajeria'])))
    return gr


@app.route('/stop')
def stop():
    cola1.put(0)
    shutdown_server()
    return 'Parando AgenteDevolucion'


@app.route('/comm', methods=['GET', 'POST'])
def comunicacion():
    global mss_cnt
    logger.info('[Devolucion] Mensaje recibido')
    message = request.args.get('content') or request.form.get('content')
    gm = Graph()
    gm.parse(data=message, format='xml')
    msgdic = get_message_properties(gm)

    if msgdic is None or msgdic.get('performative') != ACL.request:
        gr = build_message(Graph(), ACL['not-understood'],
                           sender=DevolucionAgent.uri, msgcnt=mss_cnt)
    else:
        content = msgdic.get('content')
        accion = gm.value(subject=content, predicate=RDF.type)

        if accion == ECSNS.SolicitudDevolucion:
            resp_graph = procesar_solicitud_devolucion(gm, content)
            gr = build_message(resp_graph, ACL.inform,
                               sender=DevolucionAgent.uri,
                               receiver=msgdic['sender'],
                               msgcnt=mss_cnt)
        elif accion == ECSNS.ConsultaDevoluciones:
            resp_graph = procesar_consulta_devoluciones(gm, content)
            gr = build_message(resp_graph, ACL.inform,
                               sender=DevolucionAgent.uri,
                               receiver=msgdic['sender'],
                               msgcnt=mss_cnt)
        else:
            gr = build_message(Graph(), ACL['not-understood'],
                               sender=DevolucionAgent.uri, msgcnt=mss_cnt)

    mss_cnt += 1
    return gr.serialize(format='xml')


def agentbehavior1(cola):
    register_message()
    logger.info('[Devolucion] Registrado y escuchando en puerto %d' % port)
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
    logger.info('[Devolucion] Fin')
