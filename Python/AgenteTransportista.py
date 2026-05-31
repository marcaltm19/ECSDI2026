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
# Modificador de precio: permite dar un perfil diferente a cada instancia del agente.
# Un valor > 1.0 hace al transportista mas caro, < 1.0 mas barato.
parser.add_argument('--precio-factor', type=float, default=1.0,
                    help='Factor multiplicador sobre el precio base (default: 1.0)')
args = parser.parse_args()

logger = config_logger(level=1)
port = args.port
hostname = socket.gethostname()
hostaddr = hostname if not args.open else '0.0.0.0'
dport = args.dport
dhostname = args.dhost if args.dhost else socket.gethostname()
NOMBRE = args.nombre
PRECIO_FACTOR = args.precio_factor

app = Flask(__name__)
if not args.verbose:
    logging.getLogger('werkzeug').setLevel(logging.ERROR)

agn = Namespace('http://www.agentes.org#')
mss_cnt = 0

# Estado por sesion de negociacion: guardamos el precio de la oferta inicial
# para poder validar la contra-oferta en la ronda 2.
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


def _calcular_oferta_inicial(prioridad):
    """Calcula precio y fecha de entrega segun prioridad y precio_factor."""
    if prioridad == 'urgente':
        precio_base = random.uniform(15, 30)
        dias = random.randint(1, 2)
    elif prioridad == 'economica':
        precio_base = random.uniform(3, 8)
        dias = random.randint(4, 6)
    else:  # normal
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

    # ---------------------------------------------------------------
    # RONDA 1: CFP — el logistico pide una oferta inicial
    # ---------------------------------------------------------------
    if perf == ACL.request and accion == ECSNS.CFP:
        prioridad = str(gm.value(content, ECSNS.tienePrioridad) or 'normal')
        precio, fecha = _calcular_oferta_inicial(prioridad)

        # Guardamos el precio para la ronda 2
        _ultima_oferta_precio = precio

        gr = Graph()
        gr.bind('ecsns', ECSNS)
        oferta_uri = agn[f'Oferta-{NOMBRE}-{mss_cnt}']
        gr.add((oferta_uri, RDF.type, ECSNS.Oferta))
        gr.add((oferta_uri, ECSNS.tieneTransportista, Literal(NOMBRE)))
        gr.add((oferta_uri, ECSNS.tienePrecio, Literal(precio)))
        gr.add((oferta_uri, ECSNS.tieneFechaEntrega, Literal(fecha)))

        logger.info(f'[{NOMBRE}] R1 — Oferta enviada: {precio}€ — {fecha}')
        resp = build_message(gr, ACL.propose, sender=TransportistaAgent.uri,
                             receiver=msgdic['sender'], content=oferta_uri, msgcnt=mss_cnt)

    # ---------------------------------------------------------------
    # RONDA 2: counter-proposal — el logistico envia una contra-oferta
    # ---------------------------------------------------------------
    elif perf == ACL['counter-proposal']:
        contra_precio = float(gm.value(content, ECSNS.tienePrecio) or 0)
        oferta_inicial = _ultima_oferta_precio or 999

        # Estrategia aleatoria con tres posibles respuestas
        dado = random.random()

        if dado < 0.33:
            # --- ACEPTA la contra-oferta ---
            logger.info(f'[{NOMBRE}] R2 — ACEPTA contra-oferta: {contra_precio}€')
            resp = build_message(Graph(), ACL.inform,
                                 sender=TransportistaAgent.uri,
                                 receiver=msgdic['sender'],
                                 msgcnt=mss_cnt)

        elif dado < 0.66:
            # --- PROPONE un precio intermedio ---
            # Debe ser estrictamente mayor que contra_precio y menor que la oferta inicial
            if contra_precio < oferta_inicial:
                nuevo_precio = round(random.uniform(contra_precio, oferta_inicial), 2)
                # Aseguramos que sea estrictamente mayor que contra_precio
                if nuevo_precio <= contra_precio:
                    nuevo_precio = round(contra_precio + 0.01, 2)
            else:
                # Caso degenrado: aceptamos directamente
                logger.info(f'[{NOMBRE}] R2 — rango invalido, ACEPTA: {contra_precio}€')
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
            logger.info(f'[{NOMBRE}] R2 — PROPONE nuevo precio: {nuevo_precio}€')
            resp = build_message(gr, ACL.propose,
                                 sender=TransportistaAgent.uri,
                                 receiver=msgdic['sender'],
                                 content=nueva_oferta_uri,
                                 msgcnt=mss_cnt)

        else:
            # --- RECHAZA la contra-oferta ---
            logger.info(f'[{NOMBRE}] R2 — RECHAZA la contra-oferta')
            resp = build_message(Graph(), ACL['reject-proposal'],
                                 sender=TransportistaAgent.uri,
                                 receiver=msgdic['sender'],
                                 msgcnt=mss_cnt)

    # ---------------------------------------------------------------
    # Decision final del logistico
    # ---------------------------------------------------------------
    elif perf == ACL['accept-proposal']:
        logger.info(f'[{NOMBRE}] DECISION FINAL — Oferta ACEPTADA ✅')
        resp = build_message(Graph(), ACL.inform, sender=TransportistaAgent.uri,
                             receiver=msgdic['sender'], msgcnt=mss_cnt)

    elif perf == ACL['reject-proposal']:
        logger.info(f'[{NOMBRE}] DECISION FINAL — Oferta rechazada')
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
    logger.info(f'[{NOMBRE}] Escuchando en puerto {port} (factor precio: {PRECIO_FACTOR}x)')
    app.run(host=hostname, port=port)
