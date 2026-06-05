# -*- coding: utf-8 -*-
"""
filename: SimpleDirectoryAgent

Antes de ejecutar hay que añadir la raiz del proyecto a la variable PYTHONPATH

Agente que lleva un registro de otros agentes

Utiliza un registro simple que guarda en un grafo RDF

El registro no es persistente y se mantiene mientras el agente funciona

Las acciones que se pueden usar estan definidas en la ontología
directory-service-ontology.owl

@author: javier
"""

import argparse
import logging
import socket
import os
import sys
from multiprocessing import Process, Queue

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from flask import Flask, render_template, request
from rdflib import RDF, RDFS, Graph, Namespace
from rdflib.namespace import FOAF

from AgentUtil.ACL import ACL
from AgentUtil.ACLMessages import build_message, get_message_properties
from AgentUtil.Agent import Agent
from AgentUtil.DSO import DSO
from AgentUtil.FlaskServer import shutdown_server
from AgentUtil.Logging import config_logger
from AgentUtil.Util import gethostname
from ontologia import ECSNS

__author__ = "javier"

parser = argparse.ArgumentParser()
parser.add_argument('--open', action='store_true', default=False)
parser.add_argument('--verbose', action='store_true', default=False)
parser.add_argument('--port', type=int, default=9000)

logger = config_logger(level=1)
args = parser.parse_args()

port = args.port

if args.open:
    hostname = '0.0.0.0'
    hostaddr = gethostname()
else:
    hostaddr = hostname = socket.gethostname()

print('DS Hostname =', hostaddr)

dsgraph = Graph()
dsgraph.bind('acl', ACL)
dsgraph.bind('rdf', RDF)
dsgraph.bind('rdfs', RDFS)
dsgraph.bind('foaf', FOAF)
dsgraph.bind('dso', DSO)

agn = Namespace('http://www.agentes.org#')
DirectoryAgent = Agent(
    'DirectoryAgent',
    agn.Directory,
    'http://%s:%d/Register' % (hostaddr, port),
    'http://%s:%d/Stop' % (hostaddr, port),
)
app = Flask(__name__)

if not args.verbose:
    logging.getLogger('werkzeug').setLevel(logging.ERROR)

mss_cnt = 0
cola1 = Queue()


@app.route('/Register')
def register():
    def process_register():
        logger.info('Peticion de registro')
        agn_add    = gm.value(subject=content, predicate=DSO.Address)
        agn_name   = gm.value(subject=content, predicate=FOAF.name)
        agn_uri    = gm.value(subject=content, predicate=DSO.Uri)
        agn_type   = gm.value(subject=content, predicate=DSO.AgentType)
        agn_ciudad = gm.value(subject=content, predicate=ECSNS.ciudad)
        dsgraph.add((agn_uri, RDF.type,       FOAF.Agent))
        dsgraph.add((agn_uri, FOAF.name,      agn_name))
        dsgraph.add((agn_uri, DSO.Address,    agn_add))
        dsgraph.add((agn_uri, DSO.AgentType,  agn_type))
        if agn_ciudad:
            dsgraph.add((agn_uri, ECSNS.ciudad, agn_ciudad))
        return build_message(
            Graph(), ACL.confirm,
            sender=DirectoryAgent.uri, receiver=agn_uri, msgcnt=mss_cnt,
        )

    def process_search():
        logger.info('Peticion de busqueda')
        agn_type    = gm.value(subject=content, predicate=DSO.AgentType)
        encontrados = list(dsgraph.triples((None, DSO.AgentType, agn_type)))
        if encontrados:
            gr = Graph()
            gr.bind('dso', DSO)
            gr.bind('ecsns', ECSNS)
            for i, (agn_uri, _, _) in enumerate(encontrados):
                agn_add    = dsgraph.value(subject=agn_uri, predicate=DSO.Address)
                agn_ciudad = dsgraph.value(subject=agn_uri, predicate=ECSNS.ciudad)
                entry = agn[f'Directory-response-{i}']
                gr.add((entry, DSO.Address, agn_add))
                gr.add((entry, DSO.Uri,     agn_uri))
                if agn_ciudad:
                    gr.add((entry, ECSNS.ciudad, agn_ciudad))
            rsp_obj = agn['Directory-response']
            return build_message(gr, ACL.inform,
                                 sender=DirectoryAgent.uri,
                                 msgcnt=mss_cnt, content=rsp_obj)
        else:
            return build_message(Graph(), ACL.inform,
                                 sender=DirectoryAgent.uri, msgcnt=mss_cnt)

    global dsgraph, mss_cnt
    message = request.args['content']
    gm = Graph()
    gm.parse(data=message, format='xml')
    msgdic = get_message_properties(gm)

    if not msgdic:
        gr = build_message(Graph(), ACL['not-understood'],
                           sender=DirectoryAgent.uri, msgcnt=mss_cnt)
    else:
        if msgdic['performative'] != ACL.request:
            gr = build_message(Graph(), ACL['not-understood'],
                               sender=DirectoryAgent.uri, msgcnt=mss_cnt)
        else:
            content = msgdic['content']
            accion  = gm.value(subject=content, predicate=RDF.type)
            if accion == DSO.Register:
                gr = process_register()
            elif accion == DSO.Search:
                gr = process_search()
            else:
                gr = build_message(Graph(), ACL['not-understood'],
                                   sender=DirectoryAgent.uri, msgcnt=mss_cnt)
    mss_cnt += 1
    return gr.serialize(format='xml')


@app.route('/info')
def info():
    global dsgraph, mss_cnt
    return render_template('info.html', nmess=mss_cnt,
                           graph=dsgraph.serialize(format='turtle'))


@app.route('/stop')
def stop():
    tidyup()
    shutdown_server()
    return 'Parando Servidor'


def tidyup():
    global cola1
    cola1.put(0)


def agentbehavior1(cola):
    """
    Espera mensajes de la cola de forma BLOQUEANTE (sin busy-wait).
    Usa timeout=1 para no quedarse bloqueado indefinidamente y poder
    detectar la señal de parada aunque llegue con retraso.
    """
    fin = False
    while not fin:
        try:
            v = cola.get(timeout=1)  # bloquea hasta 1s, cede el nucleo al SO
            if v == 0:
                print(v)
                return 0
            else:
                print(v)
        except Exception:
            pass  # timeout: vuelve a esperar


if __name__ == '__main__':
    ab1 = Process(target=agentbehavior1, args=(cola1,))
    ab1.start()
    # debug=False evita que Flask lance un segundo proceso reloader
    app.run(host=hostname, port=port, debug=False)
    ab1.join()
    logger.info('The End')
