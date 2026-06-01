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
FACTURAS_PATH     = os.path.join(os.path.dirname(__file__), 'data', 'facturas.json')
PLAZO_DIAS        = 15

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
    gmess.add((reg_obj, RDF.type,        DSO.Register))
    gmess.add((reg_obj, DSO.Uri,         DevolucionAgent.uri))
    gmess.add((reg_obj, FOAF.name,       Literal(DevolucionAgent.name)))
    gmess.add((reg_obj, DSO.Address,     Literal(DevolucionAgent.address)))
    gmess.add((reg_obj, DSO.AgentType,   ECSNS['Ag.Devolucion']))
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


def guardar_devoluciones(devs):
    with open(DEVOLUCIONES_PATH, 'w') as f:
        json.dump(devs, f, indent=2)


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
    siempre = ['defectuoso', 'defecto', 'equivocado', 'incorrecto', 'roto', 'danado', 'dañado']
    if any(r in razon_lower for r in siempre):
        return True, 'Devolución aceptada: producto defectuoso o equivocado', 'MensajeriaRapida S.L.'

    try:
        fecha_rec = datetime.fromisoformat(fecha_recepcion_str)
        dias = (datetime.now() - fecha_rec).days
        if dias <= PLAZO_DIAS:
            return True, f'Devolución aceptada: dentro del plazo ({dias} días)', 'MensajeriaEstandar S.A.'
        else:
            return False, f'Devolución rechazada: fuera del plazo de {PLAZO_DIAS} días ({dias} días transcurridos)', None
    except Exception:
        return False, 'Fecha de recepción inválida', None


def procesar_solicitud(gm, content):
    comprador       = str(gm.value(content, ECSNS.comprador)       or 'Anonimo')
    factura_id      = str(gm.value(content, ECSNS.idFactura)        or '')
    razon           = str(gm.value(content, ECSNS.razonDevolucion)  or 'insatisfaccion')
    fecha_recepcion = str(gm.value(content, ECSNS.fechaRecepcion)   or datetime.now().isoformat())

    aceptada, motivo, empresa = evaluar_devolucion(factura_id, razon, fecha_recepcion)

    dev_id = 'DEV-' + str(uuid.uuid4())[:8].upper()
    devs   = cargar_devoluciones()
    devs.append({
        'id': dev_id, 'comprador': comprador, 'factura_id': factura_id,
        'razon': razon, 'fecha_solicitud': datetime.now().isoformat(),
        'fecha_recepcion': fecha_recepcion, 'aceptada': aceptada,
        'motivo': motivo, 'empresa_mensajeria': empresa,
    })
    guardar_devoluciones(devs)
    logger.info(f'[Devolucion] {dev_id} — aceptada={aceptada} — {motivo}')

    gr = Graph()
    gr.bind('ecsns', ECSNS)
    node = ECSNS['devolucion-' + dev_id]
    gr.add((node, RDF.type,               ECSNS.Devolucion))
    gr.add((node, ECSNS.idDevolucion,     Literal(dev_id)))
    gr.add((node, ECSNS.aceptada,         Literal(aceptada)))
    gr.add((node, ECSNS.motivoDevolucion, Literal(motivo)))
    if empresa:
        gr.add((node, ECSNS.empresaMensajeria, Literal(empresa)))
    return gr


def procesar_consulta(gm, content):
    comprador = str(gm.value(content, ECSNS.comprador) or '')
    devs = cargar_devoluciones()
    if comprador:
        devs = [d for d in devs if d.get('comprador') == comprador]

    gr = Graph()
    gr.bind('ecsns', ECSNS)
    lista = ECSNS['listaDevoluciones']
    gr.add((lista, RDF.type, ECSNS.ListaDevoluciones))
    for d in devs:
        node = ECSNS['devolucion-' + d['id']]
        gr.add((lista, ECSNS.tieneDevolucion,  node))
        gr.add((node, ECSNS.idDevolucion,      Literal(d['id'])))
        gr.add((node, ECSNS.aceptada,          Literal(d['aceptada'])))
        gr.add((node, ECSNS.motivoDevolucion,  Literal(d['motivo'])))
        if d.get('empresa_mensajeria'):
            gr.add((node, ECSNS.empresaMensajeria, Literal(d['empresa_mensajeria'])))
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
        accion  = gm.value(subject=content, predicate=RDF.type)

        if accion == ECSNS.SolicitudDevolucion:
            resp = procesar_solicitud(gm, content)
            gr   = build_message(resp, ACL.inform, sender=DevolucionAgent.uri,
                                 receiver=msgdic['sender'], msgcnt=mss_cnt)
        elif accion == ECSNS.ConsultaDevoluciones:
            resp = procesar_consulta(gm, content)
            gr   = build_message(resp, ACL.inform, sender=DevolucionAgent.uri,
                                 receiver=msgdic['sender'], msgcnt=mss_cnt)
        else:
            gr = build_message(Graph(), ACL['not-understood'],
                               sender=DevolucionAgent.uri, msgcnt=mss_cnt)

    mss_cnt += 1
    return gr.serialize(format='xml')


def agentbehavior1(cola):
    register_message()
    logger.info(f'[Devolucion] Registrado en puerto {port}')
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
