import argparse
import json
import logging
import os
import socket
import sys
import time
import uuid
import threading
from datetime import datetime, timedelta
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
flask_host = '0.0.0.0' if args.open else hostname
dport = args.dport
dhostname = os.environ.get('ECSDI_DHOST') or args.dhost or socket.gethostname()

app = Flask(__name__)
if not args.verbose:
    logging.getLogger('werkzeug').setLevel(logging.ERROR)

agn = Namespace('http://www.agentes.org#')
mss_cnt = 0

DATA_DIR          = os.path.join(os.path.dirname(__file__), 'data')
VALORACIONES_PATH = os.path.join(DATA_DIR, 'listado_opiniones.json')
HISTORIAL_PATH    = os.path.join(DATA_DIR, 'historial_compras.json')
BUSQUEDAS_PATH    = os.path.join(DATA_DIR, 'historial_busquedas.json')

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


def _pedido_key(pedido_id):
    return (pedido_id or '').strip().upper()


def _norm_comprador(comprador):
    return (comprador or '').strip().lower()


def _ensure_producto_opiniones(valoraciones, producto_id, nombre=''):
    if producto_id not in valoraciones:
        valoraciones[producto_id] = {'nombre': nombre, 'valoraciones': [], 'pendientes': []}
    else:
        datos = valoraciones[producto_id]
        if 'valoraciones' not in datos:
            datos['valoraciones'] = []
        if 'pendientes' not in datos:
            datos['pendientes'] = []
        if nombre and not datos.get('nombre'):
            datos['nombre'] = nombre


def ya_valorado_en_pedido(comprador, producto_id, pedido_id, valoraciones=None):
    """Un comprador puede valorar el mismo producto en pedidos distintos, una vez por pedido."""
    comprador_norm = _norm_comprador(comprador)
    if not comprador_norm:
        return False
    if valoraciones is None:
        valoraciones = _load_json(VALORACIONES_PATH, {})
    pk = _pedido_key(pedido_id)
    for v in valoraciones.get(producto_id, {}).get('valoraciones', []):
        if _norm_comprador(v.get('comprador')) != comprador_norm:
            continue
        if _pedido_key(v.get('pedido_id')) == pk:
            return True
    return False


def _pendiente_activo(pendiente, comprador_norm, pedido_key):
    if _norm_comprador(pendiente.get('comprador')) != comprador_norm:
        return False
    if _pedido_key(pendiente.get('pedido_id')) != pedido_key:
        return False
    return pendiente.get('estado') in ('activado', 'solicitado')


def activar_proceso_feedback(comprador, pedido_id, sub_envios, productos):
    """
    Capacidad Activar: escribe en listado_opiniones que el usuario puede valorar
    (pendiente con fecha programada para la percepción Hora de valorar).
    """
    if not productos:
        return
    comprador = (comprador or 'Anonimo').strip()
    pedido_id = _pedido_key(pedido_id)
    fechas_envio = [
        e.get('fecha') or e.get('fecha_prevista', '')
        for e in (sub_envios or [])
        if e.get('fecha') or e.get('fecha_prevista')
    ]
    fecha_ref = max(fechas_envio) if fechas_envio else ''
    programado_iso, programado_ts = _fecha_programada_feedback(fecha_ref)
    valoraciones = _load_json(VALORACIONES_PATH, {})
    comprador_norm = _norm_comprador(comprador)
    activados = 0
    for p in productos:
        pid = p.get('id') or p.get('producto_id', '')
        if not pid:
            continue
        if ya_valorado_en_pedido(comprador, pid, pedido_id, valoraciones):
            continue
        nombre = p.get('nombre', pid)
        _ensure_producto_opiniones(valoraciones, pid, nombre)
        pendientes = valoraciones[pid]['pendientes']
        if any(_pendiente_activo(pe, comprador_norm, pedido_id) for pe in pendientes):
            for pe in pendientes:
                if _pendiente_activo(pe, comprador_norm, pedido_id):
                    pe['programado_para'] = programado_iso
                    pe['programado_ts'] = programado_ts
                    pe['estado'] = 'activado'
            activados += 1
            continue
        pendientes.append({
            'id':              'PEN-' + str(uuid.uuid4())[:8].upper(),
            'comprador':       comprador,
            'pedido_id':       pedido_id,
            'programado_para': programado_iso,
            'programado_ts':   programado_ts,
            'estado':          'activado',
            'activado_en':     datetime.now().isoformat(),
            'solicitado_en':   None,
        })
        activados += 1
    if activados:
        _save_json(VALORACIONES_PATH, valoraciones)
        logger.info(
            f'[Experiencia] Activar feedback: {activados} pendiente(s) en listado_opiniones — '
            f'{comprador} pedido {pedido_id} (valorar tras {int(programado_ts - time.time())}s)'
        )


def pendientes_feedback_vencidos(now=None):
    """Pendientes en estado activado cuya Hora de valorar ya ha llegado."""
    now = now if now is not None else time.time()
    valoraciones = _load_json(VALORACIONES_PATH, {})
    vencidos = []
    for producto_id, datos in valoraciones.items():
        nombre = datos.get('nombre', producto_id)
        for pen in datos.get('pendientes', []):
            if pen.get('estado') != 'activado':
                continue
            if float(pen.get('programado_ts', float('inf'))) <= now:
                vencidos.append({
                    'producto_id':  producto_id,
                    'nombre':       nombre,
                    'pendiente_id': pen.get('id', ''),
                    'comprador':    pen.get('comprador', ''),
                    'pedido_id':    pen.get('pedido_id', ''),
                })
    return vencidos


def marcar_pendiente_solicitado(producto_id, pendiente_id):
    """Tras Obtener feedback (A): el pendiente pasa a solicitado."""
    valoraciones = _load_json(VALORACIONES_PATH, {})
    datos = valoraciones.get(producto_id, {})
    for pen in datos.get('pendientes', []):
        if pen.get('id') == pendiente_id:
            pen['estado'] = 'solicitado'
            pen['solicitado_en'] = datetime.now().isoformat()
            _save_json(VALORACIONES_PATH, valoraciones)
            return


def quitar_pendientes_feedback(comprador, pedido_id, producto_id=None):
    valoraciones = _load_json(VALORACIONES_PATH, {})
    comprador_norm = _norm_comprador(comprador)
    pk = _pedido_key(pedido_id)
    cambio = False
    for pid, datos in valoraciones.items():
        if producto_id and pid != producto_id:
            continue
        nuevos = [
            p for p in datos.get('pendientes', [])
            if not (
                _norm_comprador(p.get('comprador')) == comprador_norm
                and _pedido_key(p.get('pedido_id')) == pk
            )
        ]
        if len(nuevos) != len(datos.get('pendientes', [])):
            datos['pendientes'] = nuevos
            cambio = True
    if cambio:
        _save_json(VALORACIONES_PATH, valoraciones)


def guardar_valoracion(comprador, producto_id, nombre_producto, puntuacion, comentario, pedido_id=''):
    """Capacidad Generar (percepción Feedback del usuario): escribe la valoración."""
    if not (1 <= int(puntuacion) <= 5):
        raise ValueError(f'Puntuacion invalida: {puntuacion}. Debe ser 1-5.')
    valoraciones = _load_json(VALORACIONES_PATH, {})
    if ya_valorado_en_pedido(comprador, producto_id, pedido_id, valoraciones):
        raise ValueError('ya_valorado_pedido')
    _ensure_producto_opiniones(valoraciones, producto_id, nombre_producto)
    entrada = {
        'id':         'VAL-' + str(uuid.uuid4())[:8].upper(),
        'comprador':  comprador,
        'pedido_id':  _pedido_key(pedido_id),
        'puntuacion': int(puntuacion),
        'comentario': comentario,
        'fecha':      datetime.now().isoformat(),
    }
    valoraciones[producto_id]['valoraciones'].append(entrada)
    pk = _pedido_key(pedido_id)
    cn = _norm_comprador(comprador)
    valoraciones[producto_id]['pendientes'] = [
        p for p in valoraciones[producto_id].get('pendientes', [])
        if not (
            _norm_comprador(p.get('comprador')) == cn
            and _pedido_key(p.get('pedido_id')) == pk
        )
    ]
    _save_json(VALORACIONES_PATH, valoraciones)
    media = calcular_media(valoraciones[producto_id]['valoraciones'])
    logger.info(
        f'[Experiencia] Valoracion {entrada["id"]} guardada -- '
        f'Pedido: {entrada["pedido_id"]} | Producto: {nombre_producto} | '
        f'Puntuacion: {puntuacion}/5 | Media: {media:.2f}'
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

_comprador_address = None


def _get_comprador_address():
    global mss_cnt, _comprador_address
    if _comprador_address is not None:
        return _comprador_address
    gmess = Graph()
    gmess.bind('dso', DSO)
    search_obj = agn[f'SearchComp-{mss_cnt}']
    gmess.add((search_obj, RDF.type,      DSO.Search))
    gmess.add((search_obj, DSO.AgentType, ECSNS['Ag.Comprador']))
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
                _comprador_address = str(addr)
                return _comprador_address
    except Exception as e:
        logger.warning(f'[Experiencia] Error buscando AgenteComprador: {e}')
    return None


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


def eliminar_compra(comprador, pedido_id):
    historial = _load_json(HISTORIAL_PATH, {})
    entradas = historial.get(comprador, [])
    nuevas = [e for e in entradas if e.get('pedido_id') != pedido_id]
    if len(nuevas) == len(entradas):
        logger.warning(f'[Experiencia] Compra {pedido_id} no encontrada en historial de {comprador}')
        return
    historial[comprador] = nuevas
    _save_json(HISTORIAL_PATH, historial)
    quitar_pendientes_feedback(comprador, pedido_id)
    logger.info(f'[Experiencia] Compra {pedido_id} eliminada del historial de {comprador}')


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
    return entrada


def _fecha_programada_feedback(fecha_entrega_str):
    """Fecha para la percepción Hora de valorar (ISO + unix). Demo: máx. 20s tras activar."""
    try:
        fecha_ent = datetime.strptime(str(fecha_entrega_str)[:10], '%Y-%m-%d')
        trigger_dt = fecha_ent + timedelta(days=1)
        ts = trigger_dt.timestamp()
        if ts <= time.time():
            ts = time.time() + 5
        max_demo = time.time() + 20
        ts = min(ts, max_demo)
        return datetime.fromtimestamp(ts).isoformat(), ts
    except (ValueError, TypeError):
        ts = time.time() + 10
        return datetime.fromtimestamp(ts).isoformat(), ts


def registrar_busqueda(comprador, categoria='', precio_max=None, valoracion_min=None):
    historial = _load_json(BUSQUEDAS_PATH, {})
    comprador = (comprador or 'Anonimo').strip()
    if comprador not in historial:
        historial[comprador] = []
    entrada = {
        'fecha': datetime.now().isoformat(),
        'categoria': (categoria or '').strip(),
        'precio_max': precio_max,
        'valoracion_min': valoracion_min,
    }
    historial[comprador].append(entrada)
    _save_json(BUSQUEDAS_PATH, historial)


def obtener_historial(comprador):
    historial = _load_json(HISTORIAL_PATH, {})
    return historial.get(comprador, [])


# ---------------------------------------------------------------------------
# Recomendaciones proactivas
# ---------------------------------------------------------------------------

def _cargar_catalogo():
    global mss_cnt
    addr = _get_comprador_address()
    if addr is None:
        return []
    gmess = Graph()
    gmess.bind('ecsns', ECSNS)
    req = ECSNS['listcat-' + str(mss_cnt)]
    gmess.add((req, RDF.type, ECSNS.ListarProductos))
    try:
        resp = send_message(
            build_message(gmess, perf=ACL.request, sender=ExperienciaAgent.uri,
                          receiver=agn.AgenteComprador, content=req, msgcnt=mss_cnt),
            addr,
        )
        mss_cnt += 1
        catalogo = []
        for s in resp.subjects(RDF.type, ECSNS.Producto):
            catalogo.append({
                'id':        str(resp.value(s, ECSNS.idProducto)  or ''),
                'nombre':    str(resp.value(s, ECSNS.nombre)       or ''),
                'categoria': str(resp.value(s, ECSNS.categoria)    or ''),
                'precio':    float(resp.value(s, ECSNS.precio)     or 0),
                'valoracion': float(resp.value(s, ECSNS.valoracion) or 0),
            })
        return catalogo
    except Exception as e:
        mss_cnt += 1
        logger.warning(f'[Experiencia] Error obteniendo catálogo de AgenteComprador: {e}')
        return []


def _compras_del_comprador(historial, comprador):
    comprador_norm = (comprador or '').strip().lower()
    ids = set()
    for nombre, pedidos in historial.items():
        if nombre.strip().lower() != comprador_norm:
            continue
        for pedido in pedidos:
            for p in pedido.get('productos', []):
                if p.get('id'):
                    ids.add(p['id'])
    return ids


def calcular_recomendaciones(comprador):
    """
    Estrategia 1 -- valoraciones de usuarios (media >= 3.5), no comprados
    Estrategia 2 -- populares entre otros compradores del historial
    Estrategia 3 -- bien valorados en el catalogo (campo valoracion), no comprados
    Estrategia 4 -- misma categoria que productos ya comprados, no comprados
    Estrategia 5 -- categorias buscadas recientemente por el comprador
    Devuelve hasta 5 productos con {id, nombre, media, razon}.
    """
    historial    = _load_json(HISTORIAL_PATH, {})
    busquedas_h  = _load_json(BUSQUEDAS_PATH, {})
    valoraciones = _load_json(VALORACIONES_PATH, {})
    catalogo     = _cargar_catalogo()
    por_id       = {p['id']: p for p in catalogo if p.get('id')}

    compras_propias = _compras_del_comprador(historial, comprador)
    comprador_norm = (comprador or '').strip().lower()
    tiene_busquedas = any(
        nombre.strip().lower() == comprador_norm for nombre in busquedas_h
    )
    if not compras_propias and not tiene_busquedas:
        logger.info(f'[Experiencia] Sin historial para {comprador!r}')
        return []

    # Estrategia 1: valoraciones de la comunidad
    por_valoracion = []
    for prod_id, datos in valoraciones.items():
        if prod_id in compras_propias:
            continue
        media = calcular_media(datos.get('valoraciones', []))
        # Solo valoraciones positivas influyen en recomendaciones a otros usuarios
        if media >= 3.5:
            por_valoracion.append({
                'id':     prod_id,
                'nombre': datos.get('nombre', prod_id),
                'media':  round(media, 2),
                'razon':  f'Bien valorado por la comunidad ({round(media, 2)}/5)',
            })
    por_valoracion.sort(key=lambda x: x['media'], reverse=True)

    # Estrategia 2: populares entre otros compradores
    contador = {}
    comprador_norm = (comprador or '').strip().lower()
    for otro, pedidos in historial.items():
        if otro.strip().lower() == comprador_norm:
            continue
        for pedido in pedidos:
            for p in pedido.get('productos', []):
                pid = p['id']
                if pid and pid not in compras_propias:
                    if pid not in contador:
                        contador[pid] = {'nombre': p.get('nombre', pid), 'veces': 0}
                    contador[pid]['veces'] += 1

    por_popularidad = [
        {
            'id':     pid,
            'nombre': datos['nombre'],
            'media':  obtener_valoraciones_producto(pid)['media']
                      or float(por_id.get(pid, {}).get('valoracion', 0)),
            'razon':  f'Comprado {datos["veces"]} vez/veces por otros usuarios',
        }
        for pid, datos in sorted(contador.items(), key=lambda x: x[1]['veces'], reverse=True)
    ]

    # Estrategia 3: catalogo (util cuando solo hay un comprador o pocas valoraciones)
    por_catalogo = []
    for p in catalogo:
        pid = p.get('id')
        if not pid or pid in compras_propias:
            continue
        nota = float(p.get('valoracion', 0))
        if nota >= 3.5:
            por_catalogo.append({
                'id':     pid,
                'nombre': p.get('nombre', pid),
                'media':  round(nota, 2),
                'razon':  f'Valoracion en catalogo: {nota}/5',
            })
    por_catalogo.sort(key=lambda x: x['media'], reverse=True)

    # Estrategia 4: afinidad por categorias compradas
    categorias_compradas = set()
    for pid in compras_propias:
        cat = (por_id.get(pid, {}).get('categoria') or '').strip()
        if cat:
            categorias_compradas.add(cat.lower())

    por_categoria = []
    for p in catalogo:
        pid = p.get('id')
        cat = (p.get('categoria') or '').strip()
        if not pid or pid in compras_propias or not cat:
            continue
        if cat.lower() not in categorias_compradas:
            continue
        nota = float(p.get('valoracion', 0))
        por_categoria.append({
            'id':     pid,
            'nombre': p.get('nombre', pid),
            'media':  round(nota, 2),
            'razon':  f'Tambien te puede interesar en {cat}',
        })
    por_categoria.sort(key=lambda x: x['media'], reverse=True)

    # Estrategia 5: categorias de busquedas del comprador
    cats_buscadas = set()
    for nombre, lista in busquedas_h.items():
        if nombre.strip().lower() != comprador_norm:
            continue
        for b in lista:
            cat = (b.get('categoria') or '').strip()
            if cat:
                cats_buscadas.add(cat.lower())

    por_busqueda = []
    for p in catalogo:
        pid = p.get('id')
        cat = (p.get('categoria') or '').strip()
        if not pid or pid in compras_propias or not cat:
            continue
        if cat.lower() not in cats_buscadas:
            continue
        nota = float(p.get('valoracion', 0))
        por_busqueda.append({
            'id':     pid,
            'nombre': p.get('nombre', pid),
            'media':  round(nota, 2),
            'razon':  f'Basado en tus busquedas en {cat}',
        })
    por_busqueda.sort(key=lambda x: x['media'], reverse=True)

    # Combinar sin duplicados
    vistos, resultado = set(), []
    for item in por_valoracion + por_popularidad + por_catalogo + por_categoria + por_busqueda:
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
        if v.get('pedido_id'):
            gr.add((vn, ECSNS.idPedido, Literal(v['pedido_id'])))
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
    if not message:
        return ('<html><head><title>AgenteExperiencia</title></head>'
                '<body style="font-family:sans-serif;padding:32px"><h2>AgenteExperiencia</h2>'
                '<p><strong>Estado:</strong> activo &nbsp;|&nbsp; <strong>Puerto:</strong> ' + str(port) + '</p>'
                '<p style="color:#666">Endpoint ACL/RDF entre agentes.</p>'
                '</body></html>'), 200, {'Content-Type': 'text/html; charset=utf-8'}
    try:
        gm = Graph()
        gm.parse(data=message, format='xml')
    except Exception as e:
        logger.warning(f'[Experiencia] /comm parse error: {e}')
        gr = build_message(Graph(), ACL['not-understood'], sender=ExperienciaAgent.uri, msgcnt=mss_cnt)
        mss_cnt += 1
        return gr.serialize(format='xml')
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
        pedido_id   = str(gm.value(content, ECSNS.idPedido)   or '')
        nombre_prod = str(gm.value(content, ECSNS.nombre)     or producto_id)
        puntuacion  = int(gm.value(content, ECSNS.puntuacion) or 3)
        comentario  = str(gm.value(content, ECSNS.comentario) or '')
        try:
            entrada = guardar_valoracion(
                comprador, producto_id, nombre_prod, puntuacion, comentario, pedido_id
            )
            resp_gr = Graph()
            resp_gr.bind('ecsns', ECSNS)
            ok_node = ECSNS['val-ok-' + entrada['id']]
            resp_gr.add((ok_node, RDF.type,           ECSNS.ValoracionOK))
            resp_gr.add((ok_node, ECSNS.idValoracion, Literal(entrada['id'])))
            gr = build_message(resp_gr, ACL.inform,
                               sender=ExperienciaAgent.uri,
                               receiver=msgdic['sender'],
                               content=ok_node, msgcnt=mss_cnt)
        except ValueError as e:
            if str(e) == 'ya_valorado_pedido':
                logger.info(
                    f'[Experiencia] Valoracion duplicada rechazada -- '
                    f'{comprador} ya valoro {producto_id} en pedido {pedido_id}'
                )
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

    elif perf == ACL.inform and accion == ECSNS.EnviosAsignados:
        comprador = str(gm.value(content, ECSNS.comprador) or 'Anonimo')
        pedido_id = str(gm.value(content, ECSNS.idPedido) or '')
        sub_envios = []
        for en in gm.objects(content, ECSNS.tieneSubEnvio):
            sub_envios.append({
                'fecha': str(gm.value(en, ECSNS.tieneFechaEntrega) or ''),
            })
        productos = []
        for pn in gm.objects(content, ECSNS.tieneProducto):
            productos.append({
                'id':     str(gm.value(pn, ECSNS.idProducto) or ''),
                'nombre': str(gm.value(pn, ECSNS.nombre)     or ''),
            })
        activar_proceso_feedback(comprador, pedido_id, sub_envios, productos)
        gr = build_message(Graph(), ACL.inform,
                           sender=ExperienciaAgent.uri,
                           receiver=msgdic['sender'],
                           msgcnt=mss_cnt)

    elif perf == ACL.inform and accion == ECSNS.RegistroBusqueda:
        comprador = str(gm.value(content, ECSNS.comprador) or 'Anonimo')
        categoria = str(gm.value(content, ECSNS.categoria) or '')
        pm = gm.value(content, ECSNS.precioMaximo)
        vm = gm.value(content, ECSNS.valoracionMinima)
        precio_max = float(pm) if pm is not None else None
        val_min = float(vm) if vm is not None else None
        registrar_busqueda(comprador, categoria, precio_max, val_min)
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

    elif perf == ACL.inform and accion == ECSNS.DevolucionAceptada:
        comprador = str(gm.value(content, ECSNS.comprador) or '')
        pedido_id = str(gm.value(content, ECSNS.idFactura) or '')
        eliminar_compra(comprador, pedido_id)
        gr = build_message(Graph(), ACL.inform,
                           sender=ExperienciaAgent.uri,
                           receiver=msgdic['sender'],
                           msgcnt=mss_cnt)

    else:
        gr = build_message(Graph(), ACL['not-understood'],
                           sender=ExperienciaAgent.uri, msgcnt=mss_cnt)

    mss_cnt += 1
    return gr.serialize(format='xml')


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def _obtener_feedback_usuario(vencidos, user_addr):
    """Acción Obtener feedback (A): percepción Hora de valorar → SolicitudFeedback."""
    global mss_cnt
    for item in vencidos:
        comprador = item['comprador']
        pedido_id = item['pedido_id']
        pid = item['producto_id']
        pnombre = item['nombre']
        pendiente_id = item['pendiente_id']
        logger.info(
            f'[Experiencia] Obtener feedback (A) → SolicitudFeedback a {user_addr} -- '
            f'{comprador} / {pedido_id} / {pnombre}'
        )
        gmess = Graph()
        gmess.bind('ecsns', ECSNS)
        req = ECSNS[f'sol-feed-{uuid.uuid4()}']
        gmess.add((req, RDF.type,          ECSNS.SolicitudFeedback))
        gmess.add((req, ECSNS.comprador,   Literal(comprador)))
        gmess.add((req, ECSNS.idPedido,    Literal(pedido_id)))
        gmess.add((req, ECSNS.idProducto,  Literal(pid)))
        gmess.add((req, ECSNS.nombre,      Literal(pnombre)))
        try:
            send_message(
                build_message(gmess, perf=ACL.request, sender=ExperienciaAgent.uri,
                              receiver=agn.AgenteUsuario, content=req, msgcnt=mss_cnt),
                user_addr,
            )
            mss_cnt += 1
            marcar_pendiente_solicitado(pid, pendiente_id)
        except Exception as e:
            logger.warning(f'[Experiencia] Fallo al enviar feedback proactivo: {e}')


def agentbehavior1(cola):
    global mss_cnt
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
        
        # Generar feedback: percepción Hora de valorar → leer listado_opiniones → Obtener feedback (A)
        vencidos = pendientes_feedback_vencidos(now)
        if vencidos:
            user_addr = obtener_address_usuario()
            if user_addr:
                for _, comprador, pedido_id, pid, pnombre in vencidos:
                    logger.info(
                        f'[Experiencia] SolicitudFeedback a {user_addr} -- '
                        f'{comprador} / {pedido_id} / {pnombre}'
                    )
                    gmess = Graph()
                    gmess.bind('ecsns', ECSNS)
                    sol_id = uuid.uuid4()
                    req = ECSNS[f'sol-feed-{sol_id}']
                    fb  = ECSNS[f'feedback-{sol_id}']
                    gmess.add((req, RDF.type,             ECSNS.SolicitudFeedback))
                    gmess.add((req, ECSNS.tieneFeedback,  fb))
                    gmess.add((fb,  RDF.type,             ECSNS.Feedback))
                    gmess.add((fb,  ECSNS.comprador,      Literal(comprador)))
                    gmess.add((fb,  ECSNS.idPedido,       Literal(pedido_id)))
                    gmess.add((fb,  ECSNS.idProducto,     Literal(pid)))
                    gmess.add((fb,  ECSNS.nombre,         Literal(pnombre)))
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
    app.run(host=flask_host, port=port)
    logger.info('[Experiencia] Fin')
