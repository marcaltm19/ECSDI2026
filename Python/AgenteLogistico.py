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
PEDIDOS_PATH  = os.path.join(os.path.dirname(__file__), 'data', 'pedidos.json')
ENVIOS_PATH   = os.path.join(os.path.dirname(__file__), 'data', 'envios.json')
CENTROS_PATH  = os.path.join(os.path.dirname(__file__), 'data', 'centros_logisticos.json')
PRODUCTOS_PATH = os.path.join(os.path.dirname(__file__), 'data', 'productos.json')

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


# ---------------------------------------------------------------------------
# Registro en el DirectoryService
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Utilidades de datos
# ---------------------------------------------------------------------------

def cargar_pedidos():
    if os.path.exists(PEDIDOS_PATH):
        with open(PEDIDOS_PATH) as f:
            return json.load(f)
    return []


def guardar_pedidos(pedidos):
    with open(PEDIDOS_PATH, 'w') as f:
        json.dump(pedidos, f, indent=2)


def cargar_centros():
    with open(CENTROS_PATH) as f:
        return json.load(f)


def cargar_productos_catalogo():
    """Devuelve un dict {id_producto: datos_producto} con centro_logistico_id incluido."""
    if not os.path.exists(PRODUCTOS_PATH):
        return {}
    with open(PRODUCTOS_PATH) as f:
        productos = json.load(f)
    return {p['id']: p for p in productos}


def haversine(lat1, lon1, lat2, lon2):
    R = 6371
    d_lat = math.radians(lat2 - lat1)
    d_lon = math.radians(lon2 - lon1)
    a = (math.sin(d_lat / 2) ** 2 +
         math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(d_lon / 2) ** 2)
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def elegir_centro_por_coords(lat, lon):
    """Devuelve el centro logistico mas cercano a las coordenadas dadas."""
    centros = cargar_centros()
    return min(centros, key=lambda c: haversine(lat, lon, c['lat'], c['lon']))


def obtener_centro_de_producto(producto_id):
    """
    Devuelve el dict del centro logistico donde esta almacenado el producto.
    Si el producto no tiene centro asignado en el catalogo, elige el CL-001 por defecto.
    """
    catalogo = cargar_productos_catalogo()
    centros   = {c['id']: c for c in cargar_centros()}
    prod = catalogo.get(producto_id)
    if prod and 'centro_logistico_id' in prod:
        centro_id = prod['centro_logistico_id']
        if centro_id in centros:
            return centros[centro_id]
    # fallback: centro mas cercano al origen (Madrid)
    logger.warning(f'[Logistico] Producto {producto_id} sin centro asignado, usando CL-001')
    return centros.get('CL-001', list(centros.values())[0])


def agrupar_productos_por_centro(productos):
    """
    Agrupa una lista de productos del pedido por su centro logistico.
    Devuelve: {centro_id: {'centro': {...}, 'productos': [...]}}
    """
    grupos = {}
    for prod in productos:
        centro = obtener_centro_de_producto(prod['id'])
        cid = centro['id']
        if cid not in grupos:
            grupos[cid] = {'centro': centro, 'productos': []}
        grupos[cid]['productos'].append(prod)
    return grupos


# ---------------------------------------------------------------------------
# Negociacion compleja con transportistas (ronda 1 CFP + ronda 2 contra-oferta)
# ---------------------------------------------------------------------------

def _buscar_transportistas():
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
    Ronda 1 (CFP) + Ronda 2 (contra-oferta al 90% del minimo).
    Retorna (nombre_transportista, fecha_entrega).
    """
    global mss_cnt
    transportistas_addr = _buscar_transportistas()
    if not transportistas_addr:
        logger.warning('[Logistico] No hay transportistas en el DS, usando fallback')
        return 'Desconocido', (datetime.now() + timedelta(days=3)).strftime('%Y-%m-%d')

    # Ronda 1
    ofertas_r1 = {}
    for t_addr in transportistas_addr:
        resultado = _enviar_cfp(t_addr, prioridad, direccion)
        if resultado:
            precio, nombre, fecha = resultado
            ofertas_r1[t_addr] = {'precio': precio, 'nombre': nombre, 'fecha': fecha}
            logger.info(f'[Logistico] Oferta R1 de {nombre}: {precio}EUR -- {fecha}')

    if not ofertas_r1:
        logger.warning('[Logistico] Ningun transportista respondio al CFP')
        return 'Desconocido', (datetime.now() + timedelta(days=3)).strftime('%Y-%m-%d')

    precio_min_r1 = min(o['precio'] for o in ofertas_r1.values())
    contra_precio = round(precio_min_r1 * 0.9, 2)
    logger.info(f'[Logistico] Precio min R1: {precio_min_r1}EUR -- Contra-oferta: {contra_precio}EUR')

    # Ronda 2
    pool_final = {}
    for t_addr, oferta in ofertas_r1.items():
        estado, nuevo_precio = _enviar_contraoferta(t_addr, contra_precio)
        if estado == 'acepta':
            logger.info(f'[Logistico] {oferta["nombre"]} ACEPTA: {contra_precio}EUR')
            pool_final[t_addr] = {'precio': contra_precio, 'nombre': oferta['nombre'], 'fecha': oferta['fecha']}
        elif estado == 'propone':
            if contra_precio < nuevo_precio < oferta['precio']:
                logger.info(f'[Logistico] {oferta["nombre"]} PROPONE: {nuevo_precio}EUR')
                pool_final[t_addr] = {'precio': nuevo_precio, 'nombre': oferta['nombre'], 'fecha': oferta['fecha']}
            else:
                logger.info(f'[Logistico] {oferta["nombre"]} propuso {nuevo_precio}EUR fuera de rango, ignorado')
        elif estado == 'rechaza':
            logger.info(f'[Logistico] {oferta["nombre"]} RECHAZA')

    if not pool_final:
        logger.info('[Logistico] Nadie acepto contra-oferta, usando ofertas R1')
        pool_final = ofertas_r1

    precio_minimo = min(o['precio'] for o in pool_final.values())
    candidatos   = [addr for addr, o in pool_final.items() if o['precio'] == precio_minimo]
    ganador_addr = random.choice(candidatos)
    ganador      = pool_final[ganador_addr]
    logger.info(f'[Logistico] GANADOR: {ganador["nombre"]} -- {ganador["precio"]}EUR -- {ganador["fecha"]}')

    for t_addr in ofertas_r1:
        perf = ACL['accept-proposal'] if t_addr == ganador_addr else ACL['reject-proposal']
        gr_dec = Graph()
        msg_dec = build_message(gr_dec, perf=perf, sender=LogisticoAgent.uri, msgcnt=mss_cnt)
        mss_cnt += 1
        try:
            http_requests.get(t_addr, params={'content': msg_dec.serialize(format='xml')}, timeout=5)
        except Exception as e:
            logger.warning(f'[Logistico] Error notificando decision a {t_addr}: {e}')

    return ganador['nombre'], ganador['fecha']


# ---------------------------------------------------------------------------
# Logica de pedidos y envios
# ---------------------------------------------------------------------------

def juntar_productos():
    """Agrupa productos duplicados dentro de cada pedido sumando cantidades."""
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
    """
    Para cada pedido pendiente:
      1. Agrupa sus productos por centro logistico.
      2. Lanza una negociacion independiente por cada grupo.
      3. Guarda un envio separado por centro (con su transportista y fecha).
      4. Notifica al GestorPedidos con los resultados de todos los sub-envios.
    """
    pedidos = cargar_pedidos()
    if not pedidos:
        return

    if os.path.exists(ENVIOS_PATH):
        with open(ENVIOS_PATH) as f:
            envios = json.load(f)
    else:
        envios = []

    for pedido in pedidos:
        logger.info(f'[Logistico] Procesando pedido {pedido["id"]}')
        grupos = agrupar_productos_por_centro(pedido.get('productos', []))
        n_grupos = len(grupos)

        if n_grupos == 0:
            logger.warning(f'[Logistico] Pedido {pedido["id"]} sin productos, saltando')
            continue

        sub_envios = []
        for centro_id, grupo in grupos.items():
            centro = grupo['centro']
            productos_grupo = grupo['productos']
            nombres_productos = [p.get('nombre', p['id']) for p in productos_grupo]

            logger.info(
                f'[Logistico] Sub-envio desde {centro["nombre"]} '
                f'({len(productos_grupo)} producto/s: {nombres_productos})'
            )

            nombre_t, fecha = negociar_transporte(
                pedido.get('prioridad', 'normal'),
                pedido.get('direccion', '')
            )

            envio_id = 'ENV-' + str(uuid.uuid4())[:8].upper()
            envio = {
                'id':               envio_id,
                'pedido_id':        pedido['id'],
                'centro_logistico': centro['nombre'],
                'transportista':    nombre_t,
                'fecha_prevista':   fecha,
                'productos':        [p['id'] for p in productos_grupo],
                'estado':           'enviado',
            }
            envios.append(envio)
            sub_envios.append(envio)

            logger.info(
                f'[Logistico] Envio {envio_id} | Centro: {centro["nombre"]} | '
                f'Transportista: {nombre_t} | Entrega: {fecha}'
            )

        # Notificar al GestorPedidos con TODOS los sub-envios del pedido
        notificar_gestor_multiples_envios(pedido, sub_envios)

    with open(ENVIOS_PATH, 'w') as f:
        json.dump(envios, f, indent=2)

    guardar_pedidos([])


def notificar_gestor_multiples_envios(pedido, sub_envios):
    """
    Envia un mensaje ACL.inform al AgenteGestorPedidos con la lista de sub-envios
    del pedido (uno por cada centro logistico involucrado).
    El mensaje incluye: pedido_id, y por cada sub-envio: centro, transportista, fecha, productos.
    """
    global mss_cnt

    # Buscar al GestorPedidos en el DS
    gmess = Graph()
    gmess.bind('dso', DSO)
    search_obj = agn[f'SearchGestor-{mss_cnt}']
    gmess.add((search_obj, RDF.type, DSO.Search))
    gmess.add((search_obj, DSO.AgentType, ECSNS['Ag.GestorDePedidos']))
    msg = build_message(gmess, perf=ACL.request, sender=LogisticoAgent.uri,
                        receiver=DirectoryAgent.uri, content=search_obj, msgcnt=mss_cnt)
    mss_cnt += 1
    try:
        r = http_requests.get(DirectoryAgent.address,
                              params={'content': msg.serialize(format='xml')}, timeout=5)
        gr_ds = Graph()
        gr_ds.parse(data=r.text, format='xml')
        gestor_addrs = [str(o) for s, p, o in gr_ds if p == DSO.Address]
    except Exception as e:
        logger.warning(f'[Logistico] No se pudo encontrar GestorPedidos: {e}')
        return

    if not gestor_addrs:
        logger.warning('[Logistico] GestorPedidos no registrado en DS, no se notifica')
        return

    gestor_addr = gestor_addrs[0]

    gr = Graph()
    gr.bind('ecsns', ECSNS)
    resultado_uri = ECSNS[f'ResultadoEnvio-{pedido["id"]}']
    gr.add((resultado_uri, RDF.type,        ECSNS.ResultadoEnvio))
    gr.add((resultado_uri, ECSNS.idPedido,  Literal(pedido['id'])))
    gr.add((resultado_uri, ECSNS.numEnvios, Literal(len(sub_envios))))

    for i, envio in enumerate(sub_envios):
        envio_node = ECSNS[f'SubEnvio-{pedido["id"]}-{i}']
        gr.add((resultado_uri,  ECSNS.tieneSubEnvio,      envio_node))
        gr.add((envio_node,     ECSNS.idEnvio,            Literal(envio['id'])))
        gr.add((envio_node,     ECSNS.tieneCentro,        Literal(envio['centro_logistico'])))
        gr.add((envio_node,     ECSNS.tieneTransportista, Literal(envio['transportista'])))
        gr.add((envio_node,     ECSNS.tieneFechaEntrega,  Literal(envio['fecha_prevista'])))
        for pid in envio.get('productos', []):
            gr.add((envio_node, ECSNS.tieneProductoId,    Literal(pid)))

    try:
        send_message(
            build_message(gr, perf=ACL.inform, sender=LogisticoAgent.uri,
                          receiver=agn.AgenteGestorPedidos, content=resultado_uri,
                          msgcnt=mss_cnt),
            gestor_addr,
        )
        mss_cnt += 1
        logger.info(f'[Logistico] Notificados {len(sub_envios)} sub-envio/s al GestorPedidos')
    except Exception as e:
        logger.warning(f'[Logistico] Error notificando GestorPedidos: {e}')


def procesar_pedido(gm, content):
    pedido_id = str(gm.value(subject=content, predicate=ECSNS.idPedido)
                    or 'PED-' + str(uuid.uuid4())[:8].upper())
    direccion = str(gm.value(subject=content, predicate=ECSNS.direccion) or '')
    prioridad = str(gm.value(subject=content, predicate=ECSNS.prioridad) or 'normal')

    productos = []
    for prod_node in gm.objects(content, ECSNS.tieneProducto):
        productos.append({
            'id':       str(gm.value(prod_node, ECSNS.idProducto)),
            'nombre':   str(gm.value(prod_node, ECSNS.nombre) or ''),
            'cantidad': int(gm.value(prod_node, ECSNS.cantidad) or 1),
            'peso':     float(gm.value(prod_node, ECSNS.peso) or 0),
        })

    pedidos = cargar_pedidos()
    pedidos.append({'id': pedido_id, 'productos': productos,
                    'direccion': direccion, 'prioridad': prioridad})
    pedidos.sort(key=lambda p: PRIORIDADES.get(p.get('prioridad', 'normal'), 1))
    guardar_pedidos(pedidos)
    logger.info(f'[Logistico] Pedido {pedido_id} recibido ({len(productos)} productos)')

    # Log de distribucion por centro para visibilidad inmediata
    grupos = agrupar_productos_por_centro(productos)
    for cid, grupo in grupos.items():
        names = [p.get('nombre', p['id']) for p in grupo['productos']]
        logger.info(f'  -> {grupo["centro"]["nombre"]}: {names}')


# ---------------------------------------------------------------------------
# Flask endpoints
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Comportamiento periodico del agente
# ---------------------------------------------------------------------------

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
