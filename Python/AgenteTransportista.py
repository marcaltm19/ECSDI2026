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
parser.add_argument('--precio-factor', type=float, default=1.0,
                    help='Factor multiplicador sobre el precio base (default: 1.0)')
parser.add_argument('--ciudad', type=str, default='',
                    help='Ciudad de cobertura geográfica (default: "")')
args = parser.parse_args()

logger = config_logger(level=1)
port = args.port
hostname = socket.gethostname()
# flask_host: donde escucha Flask (0.0.0.0 si --open para aceptar conexiones externas)
flask_host = '0.0.0.0' if args.open else hostname
# hostaddr: direccion publica que se registra en el DS.
hostaddr = os.environ.get('ECSDI_PUBLIC_HOST') or hostname
dport = args.dport
dhostname = os.environ.get('ECSDI_DHOST') or args.dhost or socket.gethostname()
NOMBRE = args.nombre
PRECIO_FACTOR = args.precio_factor
CIUDAD = args.ciudad

app = Flask(__name__)
if not args.verbose:
    logging.getLogger('werkzeug').setLevel(logging.ERROR)

agn = Namespace('http://www.agentes.org#')
mss_cnt = 0

_ultima_oferta_precio = None

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
    gmess.bind('ecsns', ECSNS)
    reg_obj = agn[TransportistaAgent.name + '-Register']
    gmess.add((reg_obj, RDF.type, DSO.Register))
    gmess.add((reg_obj, DSO.Uri, TransportistaAgent.uri))
    gmess.add((reg_obj, FOAF.name, Literal(TransportistaAgent.name)))
    gmess.add((reg_obj, DSO.Address, Literal(TransportistaAgent.address)))
    gmess.add((reg_obj, DSO.AgentType, ECSNS['Ag.Transportista']))
    if CIUDAD:
        gmess.add((reg_obj, ECSNS.ciudad, Literal(CIUDAD)))
    gr = send_message(
        build_message(gmess, perf=ACL.request, sender=TransportistaAgent.uri,
                      receiver=DirectoryAgent.uri, content=reg_obj, msgcnt=mss_cnt),
        DirectoryAgent.address,
    )
    mss_cnt += 1
    logger.info(f'[{NOMBRE}] Registrado en DS como {TransportistaAgent.address} - Ciudad: {CIUDAD}')
    return gr


def _calcular_oferta_inicial(prioridad):
    if prioridad == 'urgente':
        precio_base = random.uniform(15, 30)
        dias = random.randint(1, 2)
    elif prioridad == 'economica':
        precio_base = random.uniform(3, 8)
        dias = random.randint(4, 6)
    else:
        precio_base = random.uniform(8, 15)
        dias = random.randint(2, 4)

    precio = round(precio_base * PRECIO_FACTOR, 2)
    fecha = (datetime.now() + timedelta(days=dias)).strftime('%Y-%m-%d')
    return precio, fecha


@app.route('/comm', methods=['GET', 'POST'])
def comunicacion():
    global mss_cnt, _ultima_oferta_precio
    message = request.args.get('content') or request.form.get('content')
    gm = Graph()
    gm.parse(data=message, format='xml')
    msgdic = get_message_properties(gm)

    perf    = msgdic.get('performative') if msgdic else None
    content = msgdic.get('content') if msgdic else None
    accion  = gm.value(subject=content, predicate=RDF.type) if content else None

    if perf == ACL.request and accion == ECSNS.CFP:
        prioridad = str(gm.value(content, ECSNS.tienePrioridad) or 'normal')
        precio, fecha = _calcular_oferta_inicial(prioridad)
        _ultima_oferta_precio = precio

        gr = Graph()
        gr.bind('ecsns', ECSNS)
        oferta_uri = agn[f'Oferta-{NOMBRE}-{mss_cnt}']
        gr.add((oferta_uri, RDF.type, ECSNS.Oferta))
        gr.add((oferta_uri, ECSNS.tieneTransportista, Literal(NOMBRE)))
        gr.add((oferta_uri, ECSNS.tienePrecio, Literal(precio)))
        gr.add((oferta_uri, ECSNS.tieneFechaEntrega, Literal(fecha)))

        logger.info(f'[{NOMBRE}] R1 -- Oferta enviada: {precio}EUR -- {fecha}')
        resp = build_message(gr, ACL.propose, sender=TransportistaAgent.uri,
                             receiver=msgdic['sender'], content=oferta_uri, msgcnt=mss_cnt)

    elif perf == ACL.propose and accion == ECSNS.ContraOferta:
        contra_precio = float(gm.value(content, ECSNS.tienePrecio) or 0)
        oferta_inicial = _ultima_oferta_precio or 999
        dado = random.random()

        if dado < 0.33:
            logger.info(f'[{NOMBRE}] R2 -- ACEPTA contra-oferta: {contra_precio}EUR')
            resp = build_message(Graph(), ACL.inform,
                                 sender=TransportistaAgent.uri,
                                 receiver=msgdic['sender'],
                                 msgcnt=mss_cnt)
        elif dado < 0.66:
            if contra_precio < oferta_inicial:
                nuevo_precio = round(random.uniform(contra_precio, oferta_inicial), 2)
                if nuevo_precio <= contra_precio:
                    nuevo_precio = round(contra_precio + 0.01, 2)
            else:
                logger.info(f'[{NOMBRE}] R2 -- rango invalido, ACEPTA: {contra_precio}EUR')
                resp = build_message(Graph(), ACL.inform,
                                     sender=TransportistaAgent.uri,
                                     receiver=msgdic['sender'],
                                     msgcnt=mss_cnt)
                mss_cnt += 1
                return resp.serialize(format='xml')

            gr = Graph()
            gr.bind('ecsns', ECSNS)
            nueva_oferta_uri = agn[f'NuevaOferta-{NOMBRE}-{mss_cnt}']
            gr.add((nueva_oferta_uri, RDF.type, ECSNS.Oferta))
            gr.add((nueva_oferta_uri, ECSNS.tieneTransportista, Literal(NOMBRE)))
            gr.add((nueva_oferta_uri, ECSNS.tienePrecio, Literal(nuevo_precio)))
            logger.info(f'[{NOMBRE}] R2 -- PROPONE nuevo precio: {nuevo_precio}EUR')
            resp = build_message(gr, ACL.propose,
                                 sender=TransportistaAgent.uri,
                                 receiver=msgdic['sender'],
                                 content=nueva_oferta_uri,
                                 msgcnt=mss_cnt)
        else:
            logger.info(f'[{NOMBRE}] R2 -- RECHAZA la contra-oferta')
            resp = build_message(Graph(), ACL['reject-proposal'],
                                 sender=TransportistaAgent.uri,
                                 receiver=msgdic['sender'],
                                 msgcnt=mss_cnt)

    elif perf == ACL['accept-proposal']:
        logger.info(f'[{NOMBRE}] DECISION FINAL -- Oferta ACEPTADA')
        resp = build_message(Graph(), ACL.inform, sender=TransportistaAgent.uri,
                             receiver=msgdic['sender'], msgcnt=mss_cnt)

    elif perf == ACL['reject-proposal']:
        logger.info(f'[{NOMBRE}] DECISION FINAL -- Oferta rechazada')
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
    logger.info(f'[{NOMBRE}] Escuchando en {flask_host}:{port} (factor precio: {PRECIO_FACTOR}x)')
    app.run(host=flask_host, port=port)
