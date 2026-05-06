import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import argparse
import random
import socket
import logging
from datetime import datetime, timedelta
from flask import Flask, request
from rdflib import Graph, Literal, Namespace
from rdflib.namespace import FOAF, RDF
import requests as http_requests

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
parser.add_argument('--port', type=int, default=9010)
parser.add_argument('--dhost', default=None)
parser.add_argument('--dport', type=int, default=9000)
parser.add_argument('--nombre', type=str, default='Transportista')
args = parser.parse_args()

logger = config_logger(level=1)
port = args.port
hostname = socket.gethostname()
hostaddr = hostname if not args.open else '0.0.0.0'
dport = args.dport
dhostname = args.dhost if args.dhost else socket.gethostname()
NOMBRE = args.nombre

app = Flask(__name__)
if not args.verbose:
    logging.getLogger('werkzeug').setLevel(logging.ERROR)

agn = Namespace('http://www.agentes.org#')
mss_cnt = 0

TransportistaAgent = Agent(
    f'AgenteTransportista.{NOMBRE}',
    agn[f'AgenteTransportista.{NOMBRE}'],
    f'http://{hostaddr}:{port}/comm',
    f'http://{hostaddr}:{port}/Stop',
)
DirectoryAgent = Agent(
    'DirectoryAgent',
    agn.Directory,
    f'http://{dhostname}:{dport}/Register',
    f'http://{dhostname}:{dport}/Stop',
)

def register_message():
    global mss_cnt
    gmess = Graph()
    gmess.bind('foaf', FOAF)
    gmess.bind('dso', DSO)
    reg_obj = agn[TransportistaAgent.name + '-Register']
    gmess.add((reg_obj, RDF.type, DSO.Register))
    gmess.add((reg_obj, DSO.Uri, TransportistaAgent.uri))
    gmess.add((reg_obj, FOAF.name, Literal(TransportistaAgent.name)))
    gmess.add((reg_obj, DSO.Address, Literal(TransportistaAgent.address)))
    gmess.add((reg_obj, DSO.AgentType, ECSNS['Ag.Transportista']))
    gr = send_message(
        build_message(gmess, perf=ACL.request, sender=TransportistaAgent.uri,
                      receiver=DirectoryAgent.uri, content=reg_obj, msgcnt=mss_cnt),
        DirectoryAgent.address,
    )
    mss_cnt += 1
    logger.info(f'[{NOMBRE}] Registrado en DS')
    return gr

@app.route('/comm', methods=['GET', 'POST'])
def comunicacion():
    global mss_cnt
    message = request.args.get('content') or request.form.get('content')
    gm = Graph()
    gm.parse(data=message, format='xml')
    msgdic = get_message_properties(gm)

    perf = msgdic.get('performative') if msgdic else None
    content = msgdic.get('content') if msgdic else None
    accion = gm.value(subject=content, predicate=RDF.type) if content else None

    if perf == ACL.request and accion == ECSNS.CFP:
        prioridad = str(gm.value(content, ECSNS.tienePrioridad) or 'normal')
        destino   = str(gm.value(content, ECSNS.tieneDestino) or '')

        if prioridad == 'urgente':
            precio = round(random.uniform(15, 30), 2)
            dias = random.randint(1, 2)
        elif prioridad == 'economica':
            precio = round(random.uniform(3, 8), 2)
            dias = random.randint(4, 6)
        else:
            precio = round(random.uniform(8, 15), 2)
            dias = random.randint(2, 4)

        fecha = (datetime.now() + timedelta(days=dias)).strftime('%Y-%m-%d')

        gr = Graph()
        gr.bind('ecsns', ECSNS)
        oferta_uri = agn[f'Oferta-{NOMBRE}-{mss_cnt}']
        gr.add((oferta_uri, RDF.type, ECSNS.Oferta))
        gr.add((oferta_uri, ECSNS.tieneTransportista, Literal(NOMBRE)))
        gr.add((oferta_uri, ECSNS.tienePrecio, Literal(precio)))
        gr.add((oferta_uri, ECSNS.tieneFechaEntrega, Literal(fecha)))

        logger.info(f'[{NOMBRE}] Oferta enviada: {precio}€ — {fecha}')
        resp = build_message(gr, ACL.propose, sender=TransportistaAgent.uri,
                             receiver=msgdic['sender'], content=oferta_uri, msgcnt=mss_cnt)

    elif perf == ACL['accept-proposal']:
        logger.info(f'[{NOMBRE}] ✅ Oferta ACEPTADA')
        resp = build_message(Graph(), ACL.inform, sender=TransportistaAgent.uri,
                             receiver=msgdic['sender'], msgcnt=mss_cnt)

    elif perf == ACL['reject-proposal']:
        logger.info(f'[{NOMBRE}] Oferta rechazada')
        resp = build_message(Graph(), ACL.inform, sender=TransportistaAgent.uri,
                             receiver=msgdic['sender'], msgcnt=mss_cnt)
    else:
        resp = build_message(Graph(), ACL['not-understood'],
                             sender=TransportistaAgent.uri, msgcnt=mss_cnt)

    mss_cnt += 1
    return resp.serialize(format='xml')

@app.route('/Stop')
def stop():
    shutdown_server()
    return f'Parando {NOMBRE}'

if __name__ == '__main__':
    register_message()
    logger.info(f'[{NOMBRE}] Escuchando en puerto {port}')
    app.run(host=hostname, port=port)