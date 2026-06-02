import argparse
import json
import logging
import socket
import sys
import os
import time
from multiprocessing import Process, Queue

from flask import Flask, request
from rdflib import Graph, Literal, Namespace
from rdflib.namespace import FOAF, RDF

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
parser.add_argument('--port', type=int, default=9001)
parser.add_argument('--dhost', default=None)
parser.add_argument('--dport', type=int, default=9000)
args = parser.parse_args()

logger = config_logger(level=1)
port = args.port
hostname = socket.gethostname()
hostaddr = os.environ.get('ECSDI_PUBLIC_HOST') or hostname
dport = args.dport
dhostname = os.environ.get('ECSDI_DHOST') or args.dhost or socket.gethostname()

app = Flask(__name__)
if not args.verbose:
    logging.getLogger('werkzeug').setLevel(logging.ERROR)

agn = Namespace('http://www.agentes.org#')
mss_cnt = 0
DATA_PATH = os.path.join(os.path.dirname(__file__), 'data', 'productos.json')
EXT_PATH  = os.path.join(os.path.dirname(__file__), 'data', 'productos_externos.json')

CompradorAgent = Agent(
    'AgenteComprador',
    agn.AgenteComprador,
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
    reg_obj = agn[CompradorAgent.name + '-Register']
    gmess.add((reg_obj, RDF.type,      DSO.Register))
    gmess.add((reg_obj, DSO.Uri,       CompradorAgent.uri))
    gmess.add((reg_obj, FOAF.name,     Literal(CompradorAgent.name)))
    gmess.add((reg_obj, DSO.Address,   Literal(CompradorAgent.address)))
    gmess.add((reg_obj, DSO.AgentType, ECSNS['Ag.Comprador']))
    gr = send_message(
        build_message(gmess, perf=ACL.request, sender=CompradorAgent.uri,
                      receiver=DirectoryAgent.uri, content=reg_obj, msgcnt=mss_cnt),
        DirectoryAgent.address,
    )
    mss_cnt += 1
    return gr


def cargar_productos_externos():
    if os.path.exists(EXT_PATH):
        with open(EXT_PATH) as f:
            return json.load(f)
    return []


def guardar_productos_externos(prods):
    os.makedirs(os.path.dirname(EXT_PATH), exist_ok=True)
    with open(EXT_PATH, 'w') as f:
        json.dump(prods, f, indent=2)


def procesar_catalogo_externo(gm, content):
    """Recibe un CatalogoExterno de un VendedorExterno y actualiza productos_externos.json."""
    nombre_vendedor = str(gm.value(content, ECSNS.nombreVendedor) or 'Desconocido')
    externos = cargar_productos_externos()
    # Eliminar productos anteriores de este vendedor
    externos = [p for p in externos if p.get('vendedor') != nombre_vendedor]

    nuevos = 0
    for p_node in gm.objects(content, ECSNS.tieneProducto):
        prod = {
            'id':           str(gm.value(p_node, ECSNS.idProducto)  or ''),
            'nombre':       str(gm.value(p_node, ECSNS.nombre)       or ''),
            'categoria':    str(gm.value(p_node, ECSNS.categoria)    or ''),
            'precio':       float(gm.value(p_node, ECSNS.precio)     or 0),
            'peso':         float(gm.value(p_node, ECSNS.peso)       or 0),
            'valoracion':   float(gm.value(p_node, ECSNS.valoracion) or 0),
            'vendedor':     nombre_vendedor,
            'gestion_envio': str(gm.value(p_node, ECSNS.gestionEnvio) or 'tienda'),
        }
        externos.append(prod)
        nuevos += 1

    guardar_productos_externos(externos)
    logger.info(f'[Comprador] Catálogo de {nombre_vendedor} actualizado ({nuevos} productos)')


def buscar_productos(gm, content):
    precio_max = gm.value(subject=content, predicate=ECSNS.precioMaximo)
    categoria  = gm.value(subject=content, predicate=ECSNS.categoria)
    val_min    = gm.value(subject=content, predicate=ECSNS.valoracionMinima)
    incluir_externos = gm.value(subject=content, predicate=ECSNS.incluirExternos)

    # Productos propios
    with open(DATA_PATH) as f:
        productos = json.load(f)

    # Añadir productos externos si se solicita (o siempre)
    if incluir_externos is None or str(incluir_externos).lower() != 'false':
        productos = productos + cargar_productos_externos()

    resultados = []
    for p in productos:
        if precio_max and p['precio'] > float(precio_max):
            continue
        if categoria and p['categoria'].lower() != str(categoria).lower():
            continue
        if val_min and p['valoracion'] < float(val_min):
            continue
        resultados.append(p)

    gr = Graph()
    gr.bind('ecsns', ECSNS)
    for p in resultados:
        prod = ECSNS['prod-' + p['id']]
        gr.add((prod, RDF.type,          ECSNS.Producto))
        gr.add((prod, ECSNS.nombre,      Literal(p['nombre'])))
        gr.add((prod, ECSNS.precio,      Literal(p['precio'])))
        gr.add((prod, ECSNS.categoria,   Literal(p['categoria'])))
        gr.add((prod, ECSNS.valoracion,  Literal(p['valoracion'])))
        gr.add((prod, ECSNS.peso,        Literal(p['peso'])))
        gr.add((prod, ECSNS.idProducto,  Literal(p['id'])))
        gr.add((prod, ECSNS.vendedor,    Literal(p.get('vendedor', 'tienda'))))
    return gr


@app.route('/stop')
def stop():
    cola1.put(0)
    shutdown_server()
    return 'Parando AgenteComprador'


@app.route('/comm', methods=['GET', 'POST'])
def comunicacion():
    global mss_cnt
    logger.info('[Comprador] Mensaje recibido')
    message = request.args.get('content') or request.form.get('content')
    gm = Graph()
    gm.parse(data=message, format='xml')
    msgdic = get_message_properties(gm)

    if msgdic is None:
        gr = build_message(Graph(), ACL['not-understood'],
                           sender=CompradorAgent.uri, msgcnt=mss_cnt)
    else:
        content = msgdic.get('content')
        accion  = gm.value(subject=content, predicate=RDF.type)
        perf    = msgdic.get('performative')

        if perf == ACL.request and accion == ECSNS.Busqueda:
            resp = buscar_productos(gm, content)
            gr   = build_message(resp, ACL.inform, sender=CompradorAgent.uri,
                                 receiver=msgdic['sender'], msgcnt=mss_cnt)
        elif perf == ACL.inform and accion == ECSNS.CatalogoExterno:
            # Vendedor externo anuncia su catálogo
            procesar_catalogo_externo(gm, content)
            gr = build_message(Graph(), ACL.confirm, sender=CompradorAgent.uri,
                               receiver=msgdic['sender'], msgcnt=mss_cnt)
        else:
            gr = build_message(Graph(), ACL['not-understood'],
                               sender=CompradorAgent.uri, msgcnt=mss_cnt)

    mss_cnt += 1
    return gr.serialize(format='xml')


def agentbehavior1(cola):
    register_message()
    logger.info('[Comprador] Registrado y escuchando')
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
    logger.info('[Comprador] Fin')
