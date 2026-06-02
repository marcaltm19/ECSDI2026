import argparse
import json
import logging
import os
import socket
import sys
import time
import uuid
import threading
from datetime import datetime
from multiprocessing import Queue

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
parser.add_argument('--port', type=int, default=9005)
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

DATA_DIR         = os.path.join(os.path.dirname(__file__), 'data')
VALORACIONES_PATH = os.path.join(DATA_DIR, 'valoraciones.json')
HISTORIAL_PATH   = os.path.join(DATA_DIR, 'historial_compras.json')

ExperienciaAgent = Agent(
    'AgenteExperiencia',
    agn.AgenteExperiencia,
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


# ---------------------------------------------------------------------------
# Helpers de persistencia
# ---------------------------------------------------------------------------

def _load_json(path, default):
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return default


def _save_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Registro en el Directory Service
# ---------------------------------------------------------------------------

def register_message():
    global mss_cnt
    gmess = Graph()
    gmess.bind('foaf', FOAF)
    gmess.bind('dso', DSO)
    reg_obj = agn[ExperienciaAgent.name + '-Register']
    gmess.add((reg_obj, RDF.type,      DSO.Register))
    gmess.add((reg_obj, DSO.Uri,       ExperienciaAgent.uri))
    gmess.add((reg_obj, FOAF.name,     Literal(ExperienciaAgent.name)))
    gmess.add((reg_obj, DSO.Address,   Literal(ExperienciaAgent.address)))
    gmess.add((reg_obj, DSO.AgentType, ECSNS['Ag.Experiencia']))
    gr = send_message(
        build_message(gmess, perf=ACL.request, sender=ExperienciaAgent.uri,
                      receiver=DirectoryAgent.uri, content=reg_obj, msgcnt=mss_cnt),
        DirectoryAgent.address,
    )
    mss_cnt += 1
    return gr


# ---------------------------------------------------------------------------
# Valoraciones
# ---------------------------------------------------------------------------

def calcular_media(lista):
    if not lista:
        return 0.0
    return sum(v['puntuacion'] for v in lista) / len(lista)


def guardar_valoracion(comprador, producto_id, nombre_producto, puntuacion, comentario):
    if not (1 <= int(puntuacion) <= 5):
        raise ValueError(f'Puntuacion invalida: {puntuacion}. Debe ser 1-5.')
    valoraciones = _load_json(VALORACIONES_PATH, {})
    if producto_id not in valoraciones:
        valoraciones[producto_id] = {'nombre': nombre_producto, 'valoraciones': []}
    entrada = {
        'id':         'VAL-' + str(uuid.uuid4())[:8].upper(),
        'comprador':  comprador,
        'puntuacion': int(puntuacion),
        'comentario': comentario,
        'fecha':      datetime.now().isoformat(),
    }
    valoraciones[producto_id]['valoraciones'].append(entrada)
    _save_json(VALORACIONES_PATH, valoraciones)
    media = calcular_media(valoraciones[producto_id]['valoraciones'])
    logger.info(
        f'[Experiencia] Valoracion {entrada["id"]} guardada -- '
        f'Producto: {nombre_producto} | Puntuacion: {puntuacion}/5 | Media: {media:.2f}'
    )
    return entrada


def obtener_valoraciones_producto(producto_id):
    valoraciones = _load_json(VALORACIONES_PATH, {})
    if producto_id not in valoraciones:
        return {'producto_id': producto_id, 'media': 0.0, 'total': 0, 'valoraciones': []}
    datos = valoraciones[producto_id]
    media = calcular_media(datos['valoraciones'])
    return {
        'producto_id':  producto_id,
        'nombre':       datos.get('nombre', ''),
        'media':        round(media, 2),
        'total':        len(datos['valoraciones']),
        'valoraciones': datos['valoraciones'],
    }


# ---------------------------------------------------------------------------
# Historial de compras
# ---------------------------------------------------------------------------

feedback_tasks = []


def obtener_address_usuario():
    global mss_cnt
    gmess = Graph()
    gmess.bind('dso', DSO)
    search_obj = agn[f'SearchUser-{mss_cnt}']
    gmess.add((search_obj, RDF.type, DSO.Search))
    gmess.add((search_obj, DSO.AgentType, ECSNS['Ag.Usuario']))
    msg = build_message(gmess, perf=ACL.request, sender=ExperienciaAgent.uri,
                        receiver=DirectoryAgent.uri, content=search_obj, msgcnt=mss_cnt)
    mss_cnt += 1
    try:
        r = http_requests.get(DirectoryAgent.address,
                              params={'content': msg.serialize(format='xml')}, timeout=5)
        gr_ds = Graph()
        gr_ds.parse(data=r.text, format='xml')
        for entry in gr_ds.subjects(DSO.Uri):
            addr = gr_ds.value(entry, DSO.Address)
            if addr:
                return str(addr)
    except Exception as e:
        logger.warning(f'[Experiencia] Error buscando AgenteUsuario: {e}')
    return None


def registrar_compra(comprador, pedido_id, productos, total, fecha=None):
    historial = _load_json(HISTORIAL_PATH, {})
    if comprador not in historial:
        historial[comprador] = []
    entrada = {
        'pedido_id': pedido_id,
        'fecha':     fecha or datetime.now().isoformat(),
        'productos': productos,
        'total':     total,
    }
    historial[comprador].append(entrada)
    _save_json(HISTORIAL_PATH, historial)
    logger.info(
        f'[Experiencia] Historial actualizado -- '
        f'Comprador: {comprador} | Pedido: {pedido_id} | Total: {total}EUR'
    )
    # Enqueue a feedback task for 10 seconds from now
    feedback_tasks.append((time.time() + 10, comprador, productos))
    return entrada


def obtener_historial(comprador):
    historial = _load_json(HISTORIAL_PATH, {})
    return historial.get(comprador, [])


# ---------------------------------------------------------------------------
# Recomendaciones proactivas
# ---------------------------------------------------------------------------

def calcular_recomendaciones(comprador):
    """
    Estrategia 1 -- mejor valorados que el comprador NO ha comprado (media >= 3.5)
    Estrategia 2 -- mas populares entre otros compradores
    Devuelve hasta 5 productos con {id, nombre, media, razon}.
    """
    historial    = _load_json(HISTORIAL_PATH, {})
    valoraciones = _load_json(VALORACIONES_PATH, {})

    compras_propias = {
        p['id']
        for pedido in historial.get(comprador, [])
        for p in pedido.get('productos', [])
    }

    # Estrategia 1: mejor valorados
    por_valoracion = []
    for prod_id, datos in valoraciones.items():
        if prod_id in compras_propias:
            continue
        media = calcular_media(datos.get('valoraciones', []))
        if media >= 3.5:
            por_valoracion.append({
                'id':    prod_id,
                'nombre': datos.get('nombre', prod_id),
                'media':  round(media, 2),
                'razon':  f'Valoracion media de {round(media, 2)}/5',
            })
    por_valoracion.sort(key=lambda x: x['media'], reverse=True)

    # Estrategia 2: mas populares entre otros compradores
    contador = {}
    for otro, pedidos in historial.items():
        if otro == comprador:
            continue
        for pedido in pedidos:
            for p in pedido.get('productos', []):
                pid = p['id']
                if pid not in compras_propias:
                    if pid not in contador:
                        contador[pid] = {'nombre': p.get('nombre', pid), 'veces': 0}
                    contador[pid]['veces'] += 1

    por_popularidad = [
        {
            'id':    pid,
            'nombre': datos['nombre'],
            'media':  obtener_valoraciones_producto(pid)['media'],
            'razon':  f'Comprado {datos["veces"]} vez/veces por otros usuarios',
        }
        for pid, datos in sorted(contador.items(), key=lambda x: x[1]['veces'], reverse=True)
    ]

    # Combinar sin duplicados
    vistos, resultado = set(), []
    for item in por_valoracion + por_popularidad:
        if item['id'] not in vistos:
            vistos.add(item['id'])
            resultado.append(item)
        if len(resultado) >= 5:
            break

    logger.info(
        f'[Experiencia] Recomendaciones para {comprador}: '
        f'{[r["nombre"] for r in resultado]}'
    )
    return resultado


# ---------------------------------------------------------------------------
# Construccion de grafos RDF de respuesta
# ---------------------------------------------------------------------------

def build_respuesta_valoraciones(producto_id, datos):
    gr = Graph()
    gr.bind('ecsns', ECSNS)
    node = ECSNS['val-resultado-' + producto_id]
    gr.add((node, RDF.type,                  ECSNS.ResultadoValoraciones))
    gr.add((node, ECSNS.idProducto,          Literal(producto_id)))
    gr.add((node, ECSNS.nombre,              Literal(datos.get('nombre', ''))))
    gr.add((node, ECSNS.mediaValoracion,     Literal(datos['media'])))
    gr.add((node, ECSNS.totalValoraciones,   Literal(datos['total'])))
    for v in datos['valoraciones']:
        vn = ECSNS['val-' + v['id']]
        gr.add((node, ECSNS.tieneValoracion, vn))
        gr.add((vn,   ECSNS.idValoracion,    Literal(v['id'])))
        gr.add((vn,   ECSNS.comprador,       Literal(v['comprador'])))
        gr.add((vn,   ECSNS.puntuacion,      Literal(v['puntuacion'])))
        gr.add((vn,   ECSNS.comentario,      Literal(v['comentario'])))
        gr.add((vn,   ECSNS.fecha,           Literal(v['fecha'])))
    return gr, node


def build_respuesta_historial(comprador, pedidos):
    gr = Graph()
    gr.bind('ecsns', ECSNS)
    node = ECSNS['historial-' + comprador]
    gr.add((node, RDF.type,        ECSNS.HistorialCompras))
    gr.add((node, ECSNS.comprador, Literal(comprador)))
    for pedido in pedidos:
        pn = ECSNS['hist-ped-' + pedido['pedido_id']]
        gr.add((node, ECSNS.tienePedido, pn))
        gr.add((pn,   ECSNS.idPedido,   Literal(pedido['pedido_id'])))
        gr.add((pn,   ECSNS.fecha,      Literal(pedido['fecha'])))
        gr.add((pn,   ECSNS.total,      Literal(pedido['total'])))
        for p in pedido.get('productos', []):
            prn = ECSNS['hist-prod-' + p['id'] + '-' + pedido['pedido_id']]
            gr.add((pn,  ECSNS.tieneProducto, prn))
            gr.add((prn, ECSNS.idProducto,    Literal(p['id'])))
            gr.add((prn, ECSNS.nombre,        Literal(p.get('nombre', ''))))
            gr.add((prn, ECSNS.precio,        Literal(p.get('precio', 0))))
            gr.add((prn, ECSNS.cantidad,      Literal(p.get('cantidad', 1))))
    return gr, node


def build_respuesta_recomendaciones(comprador, recs):
    gr = Graph()
    gr.bind('ecsns', ECSNS)
    node = ECSNS['recs-' + comprador]
    gr.add((node, RDF.type,        ECSNS.Recomendaciones))
    gr.add((node, ECSNS.comprador, Literal(comprador)))
    for rec in recs:
        rn = ECSNS['rec-' + rec['id']]
        gr.add((node, ECSNS.tieneRecomendacion, rn))
        gr.add((rn,   ECSNS.idProducto,         Literal(rec['id'])))
        gr.add((rn,   ECSNS.nombre,             Literal(rec['nombre'])))
        gr.add((rn,   ECSNS.mediaValoracion,    Literal(rec['media'])))
        gr.add((rn,   ECSNS.razonRecomendacion, Literal(rec['razon'])))
    return gr, node


# ---------------------------------------------------------------------------
# Endpoint Flask
# ---------------------------------------------------------------------------

@app.route('/stop')
def stop():
    cola1.put(0)
    shutdown_server()
    return 'Parando AgenteExperiencia'


@app.route('/comm', methods=['GET', 'POST'])
def comunicacion():
    global mss_cnt
    logger.info('[Experiencia] Mensaje recibido')
    message = request.args.get('content') or request.form.get('content')
    gm = Graph()
    gm.parse(data=message, format='xml')
    msgdic = get_message_properties(gm)

    if msgdic is None:
        gr = build_message(Graph(), ACL['not-understood'],
                           sender=ExperienciaAgent.uri, msgcnt=mss_cnt)
        mss_cnt += 1
        return gr.serialize(format='xml')

    perf   = msgdic.get('performative')
    content = msgdic.get('content')
    accion = gm.value(subject=content, predicate=RDF.type) if content else None

    # --- Guardar valoracion ---
    if perf == ACL.request and accion == ECSNS.NuevaValoracion:
        comprador   = str(gm.value(content, ECSNS.comprador)  or 'Anonimo')
        producto_id = str(gm.value(content, ECSNS.idProducto) or '')
        nombre_prod = str(gm.value(content, ECSNS.nombre)     or producto_id)
        puntuacion  = int(gm.value(content, ECSNS.puntuacion) or 3)
        comentario  = str(gm.value(content, ECSNS.comentario) or '')
        try:
            entrada = guardar_valoracion(comprador, producto_id, nombre_prod,
                                         puntuacion, comentario)
            resp_gr = Graph()
            resp_gr.bind('ecsns', ECSNS)
            ok_node = ECSNS['val-ok-' + entrada['id']]
            resp_gr.add((ok_node, RDF.type,           ECSNS.ValoracionOK))
            resp_gr.add((ok_node, ECSNS.idValoracion, Literal(entrada['id'])))
            gr = build_message(resp_gr, ACL.inform,
                               sender=ExperienciaAgent.uri,
                               receiver=msgdic['sender'],
                               content=ok_node, msgcnt=mss_cnt)
        except ValueError:
            gr = build_message(Graph(), ACL['not-understood'],
                               sender=ExperienciaAgent.uri, msgcnt=mss_cnt)

    # --- Consultar valoraciones de un producto ---
    elif perf == ACL.request and accion == ECSNS.ConsultaValoraciones:
        producto_id = str(gm.value(content, ECSNS.idProducto) or '')
        datos = obtener_valoraciones_producto(producto_id)
        resp_gr, resp_node = build_respuesta_valoraciones(producto_id, datos)
        gr = build_message(resp_gr, ACL.inform,
                           sender=ExperienciaAgent.uri,
                           receiver=msgdic['sender'],
                           content=resp_node, msgcnt=mss_cnt)

    # --- Registrar compra en historial (enviado por GestorPedidos) ---
    elif perf == ACL.inform and accion == ECSNS.CompraFinalizada:
        comprador = str(gm.value(content, ECSNS.comprador) or 'Anonimo')
        pedido_id = str(gm.value(content, ECSNS.idPedido)  or '')
        total     = float(gm.value(content, ECSNS.total)   or 0)
        fecha     = str(gm.value(content, ECSNS.fecha)     or datetime.now().isoformat())
        productos = []
        for pn in gm.objects(content, ECSNS.tieneProducto):
            productos.append({
                'id':       str(gm.value(pn, ECSNS.idProducto) or ''),
                'nombre':   str(gm.value(pn, ECSNS.nombre)     or ''),
                'precio':   float(gm.value(pn, ECSNS.precio)   or 0),
                'cantidad': int(gm.value(pn, ECSNS.cantidad)   or 1),
            })
        registrar_compra(comprador, pedido_id, productos, total, fecha)
        gr = build_message(Graph(), ACL.inform,
                           sender=ExperienciaAgent.uri,
                           receiver=msgdic['sender'],
                           msgcnt=mss_cnt)

    # --- Consultar historial de un comprador ---
    elif perf == ACL.request and accion == ECSNS.ConsultaHistorial:
        comprador = str(gm.value(content, ECSNS.comprador) or '')
        pedidos   = obtener_historial(comprador)
        resp_gr, resp_node = build_respuesta_historial(comprador, pedidos)
        gr = build_message(resp_gr, ACL.inform,
                           sender=ExperienciaAgent.uri,
                           receiver=msgdic['sender'],
                           content=resp_node, msgcnt=mss_cnt)

    # --- Pedir recomendaciones proactivas ---
    elif perf == ACL.request and accion == ECSNS.PedirRecomendaciones:
        comprador = str(gm.value(content, ECSNS.comprador) or '')
        recs = calcular_recomendaciones(comprador)
        resp_gr, resp_node = build_respuesta_recomendaciones(comprador, recs)
        gr = build_message(resp_gr, ACL.inform,
                           sender=ExperienciaAgent.uri,
                           receiver=msgdic['sender'],
                           content=resp_node, msgcnt=mss_cnt)

    else:
        gr = build_message(Graph(), ACL['not-understood'],
                           sender=ExperienciaAgent.uri, msgcnt=mss_cnt)

    mss_cnt += 1
    return gr.serialize(format='xml')


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def agentbehavior1(cola):
    global mss_cnt, feedback_tasks
    register_message()
    logger.info('[Experiencia] Registrado y escuchando en puerto %d', port)
    
    last_rec_time = time.time()
    fin = False
    while not fin:
        time.sleep(1)
        if not cola.empty() and cola.get() == 0:
            fin = True
            break
            
        now = time.time()
        
        # 1. Feedback proactivo (10s después de la compra)
        vencidos = [t for t in feedback_tasks if t[0] <= now]
        feedback_tasks = [t for t in feedback_tasks if t[0] > now]
        
        if vencidos:
            user_addr = obtener_address_usuario()
            if user_addr:
                for _, comprador, productos in vencidos:
                    for p in productos:
                        pid = p['id']
                        pnombre = p.get('nombre', pid)
                        logger.info(f'[Experiencia] Enviando SolicitudFeedback proactiva a {user_addr} para {comprador} sobre {pnombre}')
                        
                        gmess = Graph()
                        gmess.bind('ecsns', ECSNS)
                        req = ECSNS[f'sol-feed-{uuid.uuid4()}']
                        gmess.add((req, RDF.type,          ECSNS.SolicitudFeedback))
                        gmess.add((req, ECSNS.comprador,   Literal(comprador)))
                        gmess.add((req, ECSNS.idProducto,  Literal(pid)))
                        gmess.add((req, ECSNS.nombre,      Literal(pnombre)))
                        
                        try:
                            send_message(
                                build_message(gmess, perf=ACL.request, sender=ExperienciaAgent.uri,
                                              receiver=agn.AgenteUsuario, content=req, msgcnt=mss_cnt),
                                user_addr
                            )
                            mss_cnt += 1
                        except Exception as e:
                            logger.warning(f'[Experiencia] Fallo al enviar feedback proactivo: {e}')
                            
        # 2. Recomendaciones proactivas periódicas (cada 30s)
        if now - last_rec_time >= 30:
            last_rec_time = now
            historial = _load_json(HISTORIAL_PATH, {})
            if historial:
                user_addr = obtener_address_usuario()
                if user_addr:
                    for comprador in list(historial.keys()):
                        recs = calcular_recomendaciones(comprador)
                        if recs:
                            logger.info(f'[Experiencia] Enviando {len(recs)} RecomendacionesProactivas proactivas a {user_addr} para {comprador}')
                            
                            gmess = Graph()
                            gmess.bind('ecsns', ECSNS)
                            rec_node = ECSNS[f'recs-pro-{uuid.uuid4()}']
                            gmess.add((rec_node, RDF.type, ECSNS.RecomendacionesProactivas))
                            gmess.add((rec_node, ECSNS.comprador, Literal(comprador)))
                            for r in recs:
                                rn = ECSNS[f'rec-pro-{r["id"]}']
                                gmess.add((rec_node, ECSNS.tieneRecomendacion, rn))
                                gmess.add((rn, ECSNS.idProducto, Literal(r['id'])))
                                gmess.add((rn, ECSNS.nombre, Literal(r['nombre'])))
                                gmess.add((rn, ECSNS.mediaValoracion, Literal(r['media'])))
                                gmess.add((rn, ECSNS.razonRecomendacion, Literal(r['razon'])))
                                
                            try:
                                send_message(
                                    build_message(gmess, perf=ACL.inform, sender=ExperienciaAgent.uri,
                                                  receiver=agn.AgenteUsuario, content=rec_node, msgcnt=mss_cnt),
                                    user_addr
                                )
                                mss_cnt += 1
                            except Exception as e:
                                logger.warning(f'[Experiencia] Fallo al enviar recomendaciones proactivas: {e}')


if __name__ == '__main__':
    os.makedirs(DATA_DIR, exist_ok=True)
    ab1 = threading.Thread(target=agentbehavior1, args=(cola1,))
    ab1.daemon = True
    ab1.start()
    app.run(host=hostname, port=port)
    logger.info('[Experiencia] Fin')
