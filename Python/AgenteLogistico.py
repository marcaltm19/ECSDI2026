import argparse
import json
import logging
import math
import os
import random
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
parser.add_argument('--port', type=int, default=9003)
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
PEDIDOS_PATH = os.path.join(os.path.dirname(__file__), 'data', 'pedidos.json')
ENVIOS_PATH  = os.path.join(os.path.dirname(__file__), 'data', 'envios.json')
CENTROS_PATH = os.path.join(os.path.dirname(__file__), 'data', 'centros_logisticos.json')

LogisticoAgent = Agent(
    'AgenteLogistico',
    agn.AgenteLogistico,
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
PRIORIDADES = {'urgente': 0, 'normal': 1, 'economica': 2}


def register_message():
    global mss_cnt
    gmess = Graph()
    gmess.bind('foaf', FOAF)
    gmess.bind('dso', DSO)
    reg_obj = agn[LogisticoAgent.name + '-Register']
    gmess.add((reg_obj, RDF.type, DSO.Register))
    gmess.add((reg_obj, DSO.Uri, LogisticoAgent.uri))
    gmess.add((reg_obj, FOAF.name, Literal(LogisticoAgent.name)))
    gmess.add((reg_obj, DSO.Address, Literal(LogisticoAgent.address)))
    gmess.add((reg_obj, DSO.AgentType, ECSNS['Ag.Logistico']))
    gr = send_message(
        build_message(gmess, perf=ACL.request, sender=LogisticoAgent.uri,
                      receiver=DirectoryAgent.uri, content=reg_obj, msgcnt=mss_cnt),
        DirectoryAgent.address,
    )
    mss_cnt += 1
    return gr


def cargar_pedidos():
    if os.path.exists(PEDIDOS_PATH):
        with open(PEDIDOS_PATH) as f:
            return json.load(f)
    return []


def guardar_pedidos(pedidos):
    with open(PEDIDOS_PATH, 'w') as f:
        json.dump(pedidos, f, indent=2)


def haversine(lat1, lon1, lat2, lon2):
    R = 6371
    d_lat = math.radians(lat2 - lat1)
    d_lon = math.radians(lon2 - lon1)
    a = (math.sin(d_lat / 2) ** 2 +
         math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(d_lon / 2) ** 2)
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def elegir_centro(lat, lon):
    with open(CENTROS_PATH) as f:
        centros = json.load(f)
    return min(centros, key=lambda c: haversine(lat, lon, c['lat'], c['lon']))


def _buscar_transportistas():
    """Consulta el DirectoryService y devuelve lista de direcciones de transportistas."""
    global mss_cnt
    gmess = Graph()
    gmess.bind('dso', DSO)
    search_obj = agn[f'Search-{mss_cnt}']
    gmess.add((search_obj, RDF.type, DSO.Search))
    gmess.add((search_obj, DSO.AgentType, ECSNS['Ag.Transportista']))
    msg = build_message(gmess, perf=ACL.request, sender=LogisticoAgent.uri,
                        receiver=DirectoryAgent.uri, content=search_obj, msgcnt=mss_cnt)
    r = http_requests.get(DirectoryAgent.address,
                          params={'content': msg.serialize(format='xml')})
    mss_cnt += 1
    gr_ds = Graph()
    gr_ds.parse(data=r.text, format='xml')
    return [str(o) for s, p, o in gr_ds if p == DSO.Address]


def _enviar_cfp(t_addr, prioridad, direccion):
    """Envia un CFP a un transportista y devuelve (precio, nombre, fecha) o None."""
    global mss_cnt
    cfp_graph = Graph()
    cfp_graph.bind('ecsns', ECSNS)
    cfp_uri = agn[f'CFP-{mss_cnt}']
    cfp_graph.add((cfp_uri, RDF.type, ECSNS.CFP))
    cfp_graph.add((cfp_uri, ECSNS.tieneDestino, Literal(direccion)))
    cfp_graph.add((cfp_uri, ECSNS.tienePrioridad, Literal(prioridad)))
    cfp_msg = build_message(cfp_graph, perf=ACL.request, sender=LogisticoAgent.uri,
                            content=cfp_uri, msgcnt=mss_cnt)
    mss_cnt += 1
    try:
        resp = http_requests.get(t_addr, params={'content': cfp_msg.serialize(format='xml')},
                                 timeout=5)
        gr_resp = Graph()
        gr_resp.parse(data=resp.text, format='xml')
        msgdic_t = get_message_properties(gr_resp)
        if msgdic_t and msgdic_t.get('performative') == ACL.propose:
            oferta = msgdic_t.get('content')
            precio = float(gr_resp.value(oferta, ECSNS.tienePrecio) or 999)
            nombre = str(gr_resp.value(oferta, ECSNS.tieneTransportista) or t_addr)
            fecha  = str(gr_resp.value(oferta, ECSNS.tieneFechaEntrega) or '')
            return precio, nombre, fecha
    except Exception as e:
        logger.warning(f'[Logistico] Error CFP a {t_addr}: {e}')
    return None


def _enviar_contraoferta(t_addr, contra_precio):
    """
    Envia una contra-oferta a un transportista.
    Retorna:
      ('acepta', contra_precio)  si el transportista acepta (ACL.inform)
      ('propone', nuevo_precio)  si el transportista propone un precio intermedio (ACL.propose)
      ('rechaza', None)          si el transportista rechaza (ACL.reject-proposal)
      ('error', None)            si hay un problema de comunicacion
    """
    global mss_cnt
    gr_counter = Graph()
    gr_counter.bind('ecsns', ECSNS)
    counter_uri = agn[f'Counter-{mss_cnt}']
    gr_counter.add((counter_uri, RDF.type, ECSNS.ContraOferta))
    gr_counter.add((counter_uri, ECSNS.tienePrecio, Literal(contra_precio)))
    msg_counter = build_message(
        gr_counter, perf=ACL['counter-proposal'],
        sender=LogisticoAgent.uri, content=counter_uri, msgcnt=mss_cnt
    )
    mss_cnt += 1
    try:
        resp = http_requests.get(t_addr,
                                 params={'content': msg_counter.serialize(format='xml')},
                                 timeout=5)
        gr_resp = Graph()
        gr_resp.parse(data=resp.text, format='xml')
        msgdic_r = get_message_properties(gr_resp)
        if not msgdic_r:
            return 'error', None
        perf = msgdic_r.get('performative')
        if perf == ACL.inform:
            return 'acepta', contra_precio
        elif perf == ACL.propose:
            c = msgdic_r.get('content')
            nuevo_precio = float(gr_resp.value(c, ECSNS.tienePrecio) or 999)
            return 'propone', nuevo_precio
        elif perf == ACL['reject-proposal']:
            return 'rechaza', None
    except Exception as e:
        logger.warning(f'[Logistico] Error contra-oferta a {t_addr}: {e}')
    return 'error', None


def negociar_transporte(prioridad, direccion):
    """
    Negociacion compleja con los transportistas registrados en el DS:
      Ronda 1 - CFP: recoge todas las propuestas iniciales.
      Ronda 2 - Contra-oferta: envia precio_min * 0.9 a todos.
        - Acepta  -> entra al pool final con el precio de contra-oferta.
        - Propone -> entra al pool final si contra_precio < nuevo < precio_inicial.
        - Rechaza -> queda fuera del pool final.
      Si nadie acepta ni propone, el pool final es el de la ronda 1.
      Ganador: precio mas bajo del pool final (empate -> azar).
    """
    global mss_cnt

    transportistas_addr = _buscar_transportistas()

    if not transportistas_addr:
        logger.warning('[Logistico] No hay transportistas en el DS, usando fallback')
        return 'Desconocido', (datetime.now() + timedelta(days=3)).strftime('%Y-%m-%d')

    # --- RONDA 1: CFP inicial ---
    ofertas_r1 = {}  # addr -> {'precio': float, 'nombre': str, 'fecha': str}
    for t_addr in transportistas_addr:
        resultado = _enviar_cfp(t_addr, prioridad, direccion)
        if resultado:
            precio, nombre, fecha = resultado
            ofertas_r1[t_addr] = {'precio': precio, 'nombre': nombre, 'fecha': fecha}
            logger.info(f'[Logistico] Oferta R1 de {nombre}: {precio}€ — {fecha}')

    if not ofertas_r1:
        logger.warning('[Logistico] Ningun transportista respondio al CFP')
        return 'Desconocido', (datetime.now() + timedelta(days=3)).strftime('%Y-%m-%d')

    precio_min_r1 = min(o['precio'] for o in ofertas_r1.values())
    contra_precio = round(precio_min_r1 * 0.9, 2)
    logger.info(f'[Logistico] Precio minimo R1: {precio_min_r1}€ — Contra-oferta: {contra_precio}€')

    # --- RONDA 2: contra-oferta ---
    pool_final = {}  # addr -> {'precio': float, 'nombre': str, 'fecha': str}

    for t_addr, oferta in ofertas_r1.items():
        estado, nuevo_precio = _enviar_contraoferta(t_addr, contra_precio)

        if estado == 'acepta':
            logger.info(f'[Logistico] {oferta["nombre"]} ACEPTA contra-oferta: {contra_precio}€')
            pool_final[t_addr] = {
                'precio': contra_precio,
                'nombre': oferta['nombre'],
                'fecha':  oferta['fecha'],
            }
        elif estado == 'propone':
            # Solo valida si esta estrictamente entre contra_precio y precio_inicial
            if contra_precio < nuevo_precio < oferta['precio']:
                logger.info(f'[Logistico] {oferta["nombre"]} PROPONE: {nuevo_precio}€')
                pool_final[t_addr] = {
                    'precio': nuevo_precio,
                    'nombre': oferta['nombre'],
                    'fecha':  oferta['fecha'],
                }
            else:
                logger.info(f'[Logistico] {oferta["nombre"]} propuso {nuevo_precio}€ fuera de rango, ignorado')
        elif estado == 'rechaza':
            logger.info(f'[Logistico] {oferta["nombre"]} RECHAZA la contra-oferta')
        # 'error' -> no entra en el pool

    # Si nadie entro en el pool final, usamos las ofertas de la ronda 1
    if not pool_final:
        logger.info('[Logistico] Nadie acepto la contra-oferta, usando mejores ofertas R1')
        pool_final = ofertas_r1

    # Seleccionar ganador: precio mas bajo (empate -> azar)
    precio_minimo = min(o['precio'] for o in pool_final.values())
    candidatos = [addr for addr, o in pool_final.items() if o['precio'] == precio_minimo]
    ganador_addr = random.choice(candidatos)
    ganador = pool_final[ganador_addr]
    logger.info(f'[Logistico] GANADOR: {ganador["nombre"]} — {ganador["precio"]}€ — {ganador["fecha"]}')

    # Notificar a todos: aceptar al ganador, rechazar al resto
    for t_addr in ofertas_r1:
        perf = ACL['accept-proposal'] if t_addr == ganador_addr else ACL['reject-proposal']
        gr_dec = Graph()
        msg_dec = build_message(gr_dec, perf=perf,
                                sender=LogisticoAgent.uri, msgcnt=mss_cnt)
        mss_cnt += 1
        try:
            http_requests.get(t_addr, params={'content': msg_dec.serialize(format='xml')},
                              timeout=5)
        except Exception as e:
            logger.warning(f'[Logistico] Error notificando decision a {t_addr}: {e}')

    return ganador['nombre'], ganador['fecha']


def juntar_productos():
    pedidos = cargar_pedidos()
    for pedido in pedidos:
        agrupados = {}
        for prod in pedido.get('productos', []):
            pid = prod['id']
            if pid in agrupados:
                agrupados[pid]['cantidad'] += prod.get('cantidad', 1)
            else:
                agrupados[pid] = dict(prod)
        pedido['productos'] = list(agrupados.values())
    guardar_pedidos(pedidos)
    logger.info('[Logistico] Productos agrupados')


def realizar_envios():
    pedidos = cargar_pedidos()
    if not pedidos:
        return
    for pedido in pedidos:
        nombre_t, fecha = negociar_transporte(
            pedido.get('prioridad', 'normal'),
            pedido.get('direccion', '')
        )

        envio = {
            'id': 'ENV-' + str(uuid.uuid4())[:8].upper(),
            'pedido_id': pedido['id'],
            'transportista': nombre_t,
            'fecha_prevista': fecha,
            'estado': 'enviado',
        }
        if os.path.exists(ENVIOS_PATH):
            with open(ENVIOS_PATH) as f:
                envios = json.load(f)
        else:
            envios = []
        envios.append(envio)
        with open(ENVIOS_PATH, 'w') as f:
            json.dump(envios, f, indent=2)
        logger.info(f"[Logistico] Envio {envio['id']} — {nombre_t} — {fecha}")
    guardar_pedidos([])


def procesar_pedido(gm, content):
    pedido_id = str(gm.value(subject=content, predicate=ECSNS.idPedido)
                    or 'PED-' + str(uuid.uuid4())[:8].upper())
    direccion = str(gm.value(subject=content, predicate=ECSNS.direccion) or '')
    prioridad = str(gm.value(subject=content, predicate=ECSNS.prioridad) or 'normal')

    productos = []
    for prod_node in gm.objects(content, ECSNS.tieneProducto):
        productos.append({
            'id':       str(gm.value(prod_node, ECSNS.idProducto)),
            'cantidad': int(gm.value(prod_node, ECSNS.cantidad) or 1),
            'peso':     float(gm.value(prod_node, ECSNS.peso) or 0),
        })

    pedidos = cargar_pedidos()
    pedidos.append({'id': pedido_id, 'productos': productos,
                    'direccion': direccion, 'prioridad': prioridad})
    pedidos.sort(key=lambda p: PRIORIDADES.get(p.get('prioridad', 'normal'), 1))
    guardar_pedidos(pedidos)
    logger.info(f'[Logistico] Pedido {pedido_id} recibido y ordenado')


@app.route('/stop')
def stop():
    cola1.put(0)
    shutdown_server()
    return 'Parando AgenteLogistico'


@app.route('/comm', methods=['GET', 'POST'])
def comunicacion():
    global mss_cnt
    logger.info('[Logistico] Mensaje recibido')
    message = request.args.get('content') or request.form.get('content')
    gm = Graph()
    gm.parse(data=message, format='xml')
    msgdic = get_message_properties(gm)

    if msgdic is None or msgdic.get('performative') != ACL.request:
        gr = build_message(Graph(), ACL['not-understood'],
                           sender=LogisticoAgent.uri, msgcnt=mss_cnt)
    else:
        content = msgdic.get('content')
        accion  = gm.value(subject=content, predicate=RDF.type)

        if accion == ECSNS.Pedido:
            procesar_pedido(gm, content)
            gr = build_message(Graph(), ACL.inform,
                               sender=LogisticoAgent.uri,
                               receiver=msgdic['sender'],
                               msgcnt=mss_cnt)
        else:
            gr = build_message(Graph(), ACL['not-understood'],
                               sender=LogisticoAgent.uri, msgcnt=mss_cnt)

    mss_cnt += 1
    return gr.serialize(format='xml')


def agentbehavior1(cola):
    register_message()
    logger.info('[Logistico] Registrado y escuchando')
    tick = 0
    fin = False
    while not fin:
        time.sleep(1)
        tick += 1
        if tick % 20 == 0:
            juntar_productos()
            realizar_envios()
        if not cola.empty() and cola.get() == 0:
            fin = True


if __name__ == '__main__':
    os.makedirs(os.path.join(os.path.dirname(__file__), 'data'), exist_ok=True)
    ab1 = Process(target=agentbehavior1, args=(cola1,))
    ab1.start()
    app.run(host=hostname, port=port)
    ab1.join()
    logger.info('[Logistico] Fin')
