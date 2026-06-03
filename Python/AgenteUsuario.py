import argparse
import json
import logging
import os
import socket
import sys
import time
from datetime import date, datetime
from multiprocessing import Process, Queue

from flask import Flask, request, redirect, url_for, session, render_template
from rdflib import Graph, Literal, Namespace, URIRef
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
parser.add_argument('--port', type=int, default=9020)
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

app = Flask(__name__, template_folder=os.path.join(os.path.dirname(__file__), 'templates'))
app.secret_key = 'ecsdi2026-usuario-secret'
if not args.verbose:
    logging.getLogger('werkzeug').setLevel(logging.ERROR)

agn = Namespace('http://www.agentes.org#')
mss_cnt = 0

DATA_DIR      = os.path.join(os.path.dirname(__file__), 'data')
FACTURAS_PATH      = os.path.join(DATA_DIR, 'listado_facturas.json')
PEDIDOS_PATH       = os.path.join(DATA_DIR, 'listado_pedidos.json')
VALORACIONES_PATH  = os.path.join(DATA_DIR, 'listado_opiniones.json')
PRODUCTOS_PATH     = os.path.join(DATA_DIR, 'listado_productos_detallados.json')
DEVOLUCIONES_PATH  = os.path.join(DATA_DIR, 'listado_devoluciones.json')

_envios_notificados     = {}
_recomendaciones        = []
_solicitudes_feedback   = []
_notificaciones_devolucion = []
_addr_cache         = {}

UsuarioAgent = Agent(
    'AgenteUsuario',
    agn.AgenteUsuario,
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


@app.context_processor
def inject_globals():
    return {
        'solicitudes_feedback': list(_solicitudes_feedback),
        'usuario_actual': session.get('usuario', ''),
    }


def _comprador_sesion():
    return (
        session.get('usuario', '').strip()
        or session.get('comprador', '').strip()
        or (session.get('ultimo_pedido') or {}).get('comprador', '').strip()
    )



def _añadir_solicitud_feedback(gm, content):
    comprador = str(gm.value(content, ECSNS.comprador) or '').strip()
    pedido_id = str(gm.value(content, ECSNS.idPedido) or '').strip()
    producto_id = str(gm.value(content, ECSNS.idProducto) or '').strip()
    nombre = str(gm.value(content, ECSNS.nombre) or producto_id)
    if not comprador or not producto_id:
        return
    clave = (comprador.lower(), pedido_id, producto_id)
    for s in _solicitudes_feedback:
        if (s['comprador'].lower(), s.get('pedido_id', ''), s['producto_id']) == clave:
            return
    _solicitudes_feedback.append({
        'comprador': comprador,
        'pedido_id': pedido_id,
        'producto_id': producto_id,
        'nombre': nombre,
        'linea': f'{pedido_id}|{producto_id}' if pedido_id else f'|{producto_id}',
    })
    logger.info(f'[Usuario] Feedback pendiente: {comprador} — {nombre} ({pedido_id})')


def _quitar_solicitud_feedback(comprador, pedido_id, producto_id):
    comprador_norm = (comprador or '').strip().lower()
    pk = (pedido_id or '').strip()
    pid = (producto_id or '').strip()
    global _solicitudes_feedback
    _solicitudes_feedback[:] = [
        s for s in _solicitudes_feedback
        if not (
            s['comprador'].strip().lower() == comprador_norm
            and s.get('pedido_id', '').strip() == pk
            and s['producto_id'].strip() == pid
        )
    ]


# ---------------------------------------------------------------------------
# DS helpers
# ---------------------------------------------------------------------------

def register_message():
    global mss_cnt
    gmess = Graph()
    gmess.bind('foaf', FOAF)
    gmess.bind('dso', DSO)
    reg_obj = agn[UsuarioAgent.name + '-Register']
    gmess.add((reg_obj, RDF.type,      DSO.Register))
    gmess.add((reg_obj, DSO.Uri,       UsuarioAgent.uri))
    gmess.add((reg_obj, FOAF.name,     Literal(UsuarioAgent.name)))
    gmess.add((reg_obj, DSO.Address,   Literal(UsuarioAgent.address)))
    gmess.add((reg_obj, DSO.AgentType, ECSNS['Ag.Usuario']))
    gr = send_message(
        build_message(gmess, perf=ACL.request, sender=UsuarioAgent.uri,
                      receiver=DirectoryAgent.uri, content=reg_obj, msgcnt=mss_cnt),
        DirectoryAgent.address,
    )
    mss_cnt += 1
    logger.info('[Usuario] Registrado en DS')
    return gr


def get_agent_address(agent_type_str):
    """
    Busca la dirección de un agente en el DS por tipo.
    Usa send_message (igual que el resto de agentes) y parsea
    correctamente la respuesta del DS que devuelve nodos
    agn['Directory-response-N'] con DSO.Address como Literal.
    """
    global mss_cnt
    if agent_type_str in _addr_cache:
        return _addr_cache[agent_type_str]

    agent_type = ECSNS[agent_type_str]
    gmess = Graph()
    gmess.bind('dso', DSO)
    search_obj = agn['Search-' + str(mss_cnt)]
    gmess.add((search_obj, RDF.type,      DSO.Search))
    gmess.add((search_obj, DSO.AgentType, agent_type))

    try:
        gr = send_message(
            build_message(gmess, perf=ACL.request, sender=UsuarioAgent.uri,
                          receiver=DirectoryAgent.uri, content=search_obj, msgcnt=mss_cnt),
            DirectoryAgent.address,
        )
        mss_cnt += 1
        # El DS devuelve tripletas (agn.Directory-response-N, DSO.Address, Literal("http://..."))
        # Filtramos SOLO los Literal (las URIs de los agentes son Literal, no URIRef)
        for s, p, o in gr:
            if p == DSO.Address and isinstance(o, Literal):
                addr = str(o)
                _addr_cache[agent_type_str] = addr
                logger.info(f'[Usuario] Dirección de {agent_type_str}: {addr}')
                return addr
        logger.warning(f'[Usuario] Agente {agent_type_str} no encontrado en DS')
    except Exception as e:
        logger.warning(f'[Usuario] Error buscando agente {agent_type_str} en DS: {e}')
        mss_cnt += 1
    return None


# ---------------------------------------------------------------------------
# Data helpers
# ---------------------------------------------------------------------------

def load_json(path, default=None):
    if default is None:
        default = []
    if os.path.exists(path):
        try:
            with open(path) as f:
                return json.load(f)
        except Exception:
            pass
    return default


def _catalogo_productos():
    return load_json(PRODUCTOS_PATH, [])


def listar_categorias():
    cats = set()
    for p in _catalogo_productos():
        c = (p.get('categoria') or '').strip()
        if c:
            cats.add(c)
    return sorted(cats, key=str.lower)


def enriquecer_recomendacion(rec):
    for p in _catalogo_productos():
        if p.get('id') == rec.get('id'):
            rec.setdefault('nombre', p.get('nombre', ''))
            rec.setdefault('precio', float(p.get('precio', 0)))
            rec.setdefault('categoria', p.get('categoria', ''))
            break
    return rec


def _registrar_recomendaciones(nuevas):
    global _recomendaciones
    for rec in nuevas:
        rec = enriquecer_recomendacion(dict(rec))
        if not rec.get('id'):
            continue
        if not any(r['id'] == rec['id'] for r in _recomendaciones):
            _recomendaciones.append(rec)


def _parsear_recomendaciones_grafo(gm, content):
    recs = []
    for pn in gm.objects(content, ECSNS.tieneRecomendacion):
        rec = {
            'id':     str(gm.value(pn, ECSNS.idProducto) or ''),
            'nombre': str(gm.value(pn, ECSNS.nombre) or ''),
            'media':  float(gm.value(pn, ECSNS.mediaValoracion) or 0),
            'razon':  str(gm.value(pn, ECSNS.razonRecomendacion) or ''),
        }
        if rec['id']:
            recs.append(rec)
    return recs


def solicitar_recomendaciones(comprador):
    global mss_cnt
    addr = get_agent_address('Ag.Experiencia')
    if not addr:
        return [], 'AgenteExperiencia no disponible'
    gmess = Graph()
    gmess.bind('ecsns', ECSNS)
    req = ECSNS['recs-req-' + str(mss_cnt)]
    gmess.add((req, RDF.type, ECSNS.PedirRecomendaciones))
    gmess.add((req, ECSNS.comprador, Literal(comprador)))
    try:
        gr_resp = send_message(
            build_message(gmess, perf=ACL.request, sender=UsuarioAgent.uri,
                          receiver=agn.AgenteExperiencia, content=req, msgcnt=mss_cnt),
            addr,
        )
        mss_cnt += 1
        msgdic = get_message_properties(gr_resp)
        content = msgdic.get('content') if msgdic else None
        if content is None:
            return [], 'Sin respuesta del agente de experiencia'
        recs = _parsear_recomendaciones_grafo(gr_resp, content)
        _registrar_recomendaciones(recs)
        return recs, None
    except Exception as e:
        mss_cnt += 1
        return [], f'Error: {e}'


def _num_carrito():
    return len(session.get('carrito', []))


def _norm_comprador(nombre):
    return (nombre or '').strip().casefold()


def ids_facturas_devueltas():
    """Facturas con devolución aceptada (flag en factura o registro en devoluciones.json)."""
    ids = set()
    for f in load_json(FACTURAS_PATH):
        if f.get('devuelta'):
            ids.add(f.get('id'))
    for d in load_json(DEVOLUCIONES_PATH):
        if d.get('aceptada') and d.get('factura_id'):
            ids.add(d['factura_id'])
    return ids


def buscar_factura_local(factura_id):
    for f in load_json(FACTURAS_PATH):
        if f.get('id') == factura_id:
            return f
    return None


def facturas_para_vista(solo_comprador=None, excluir_devueltas=True):
    """Facturas con envíos logísticos fusionados (memoria, pedidos.json o facturas.json)."""
    devueltas = ids_facturas_devueltas() if excluir_devueltas else set()
    comprador_norm = _norm_comprador(solo_comprador) if solo_comprador else None
    facturas = load_json(FACTURAS_PATH)
    pedidos_map = {p['id']: p for p in load_json(PEDIDOS_PATH)}
    resultado = []
    for f in facturas:
        fid = f.get('id', '')
        if excluir_devueltas and fid in devueltas:
            continue
        if comprador_norm and _norm_comprador(f.get('comprador')) != comprador_norm:
            continue
        if not f.get('envios_logistico'):
            envios = _envios_notificados.get(fid)
            if not envios:
                envios = pedidos_map.get(fid, {}).get('envios', [])
            f['envios_logistico'] = envios or []
        resultado.append(f)
    return list(reversed(resultado))


def devoluciones_para_vista(comprador=None):
    devs = load_json(DEVOLUCIONES_PATH)
    if comprador:
        cn = _norm_comprador(comprador)
        devs = [d for d in devs if _norm_comprador(d.get('comprador')) == cn]
    return list(reversed(devs))


def facturas_elegibles_devolucion(comprador):
    """Facturas del comprador que aún no tienen devolución aceptada."""
    return facturas_para_vista(solo_comprador=comprador, excluir_devueltas=True)


def _pedido_key(pedido_id):
    return (pedido_id or '').strip().upper()


def valoracion_en_pedido(comprador, producto_id, pedido_id, valoraciones=None):
    comprador_norm = (comprador or '').strip().lower()
    if valoraciones is None:
        valoraciones = load_json(VALORACIONES_PATH, {})
    pk = _pedido_key(pedido_id)
    for v in valoraciones.get(producto_id, {}).get('valoraciones', []):
        if (v.get('comprador') or '').strip().lower() != comprador_norm:
            continue
        if _pedido_key(v.get('pedido_id')) == pk:
            return v
    return None


def ya_valorado_en_pedido(comprador, producto_id, pedido_id):
    return valoracion_en_pedido(comprador, producto_id, pedido_id) is not None


def lineas_compra_para_valorar(comprador):
    """Líneas (pedido + producto) del comprador, separadas en pendientes y ya valoradas."""
    comprador_norm = (comprador or '').strip().lower()
    valoraciones = load_json(VALORACIONES_PATH, {})
    pendientes, valorados = [], []
    for f in load_json(FACTURAS_PATH):
        if (f.get('comprador') or '').strip().lower() != comprador_norm:
            continue
        fid = f.get('id', '')
        for p in f.get('productos', []):
            pid = p.get('id', '')
            linea = {
                'factura_id':  fid,
                'producto_id': pid,
                'nombre':      p.get('nombre', pid),
                'precio':      p.get('precio', 0),
                'fecha':       (f.get('fecha') or '')[:10],
            }
            prev = valoracion_en_pedido(comprador, pid, fid, valoraciones)
            if prev:
                linea['puntuacion_enviada'] = prev['puntuacion']
                valorados.append(linea)
            else:
                pendientes.append(linea)
    return pendientes, valorados


def estado_envio_factura(f):
    if f.get('envios_logistico') or f.get('envios_vendedor'):
        return 'enviado'
    if any(p.get('vendedor', 'tienda') == 'tienda' or p.get('gestion_envio') == 'tienda'
           for p in f.get('productos', [])):
        return 'procesando'
    return 'confirmado'


# ---------------------------------------------------------------------------
# Agent actions
# ---------------------------------------------------------------------------

def buscar_productos(nombre='', categoria='', precio_max='', val_min=''):
    global mss_cnt
    addr = get_agent_address('Ag.Comprador')
    if not addr:
        return [], 'AgenteComprador no disponible en el sistema. ¿Está arrancado?'

    comprador = _comprador_sesion() or 'Anonimo'
    gmess = Graph()
    gmess.bind('ecsns', ECSNS)
    busq = ECSNS['busqueda-' + str(mss_cnt)]
    gmess.add((busq, RDF.type,           ECSNS.Busqueda))
    gmess.add((busq, ECSNS.comprador,    Literal(comprador)))
    if categoria.strip():
        gmess.add((busq, ECSNS.categoria, Literal(categoria.strip())))
    if precio_max.strip():
        try:
            gmess.add((busq, ECSNS.precioMaximo, Literal(float(precio_max.strip()))))
        except ValueError:
            pass
    if val_min.strip():
        try:
            gmess.add((busq, ECSNS.valoracionMinima, Literal(float(val_min.strip()))))
        except ValueError:
            pass
    try:
        gr_resp = send_message(
            build_message(gmess, perf=ACL.request, sender=UsuarioAgent.uri,
                          receiver=agn.AgenteComprador, content=busq, msgcnt=mss_cnt),
            addr
        )
        mss_cnt += 1
        productos = []
        for s in gr_resp.subjects(RDF.type, ECSNS.Producto):
            prod = {
                'id':        str(gr_resp.value(s, ECSNS.idProducto)  or ''),
                'nombre':    str(gr_resp.value(s, ECSNS.nombre)       or ''),
                'categoria': str(gr_resp.value(s, ECSNS.categoria)    or ''),
                'precio':    float(gr_resp.value(s, ECSNS.precio)     or 0),
                'peso':      float(gr_resp.value(s, ECSNS.peso)       or 0),
                'valoracion':float(gr_resp.value(s, ECSNS.valoracion) or 0),
                'vendedor':     str(gr_resp.value(s, ECSNS.vendedor)     or 'tienda'),
                'gestion_envio': str(gr_resp.value(s, ECSNS.gestionEnvio) or 'tienda'),
            }
            if nombre.strip() and nombre.strip().lower() not in prod['nombre'].lower():
                continue
            productos.append(prod)
        return productos, None
    except Exception as e:
        logger.warning(f'[Usuario] Error buscando productos: {e}')
        mss_cnt += 1
        return [], f'Error de comunicación con AgenteComprador: {e}'


def enviar_pedido(comprador, direccion, prioridad, metodo_pago, carrito):
    global mss_cnt
    addr = get_agent_address('Ag.GestorDePedidos')
    if not addr:
        return None, 'AgenteGestorPedidos no disponible'

    gmess = Graph()
    gmess.bind('ecsns', ECSNS)
    ped = ECSNS['pedido-ui-' + str(mss_cnt)]
    gmess.add((ped, RDF.type,         ECSNS.SolicitudPedido))
    gmess.add((ped, ECSNS.comprador,  Literal(comprador)))
    gmess.add((ped, ECSNS.direccion,  Literal(direccion)))
    gmess.add((ped, ECSNS.prioridad,  Literal(prioridad)))
    gmess.add((ped, ECSNS.metodoPago, Literal(metodo_pago)))
    for i, item in enumerate(carrito):
        pn = ECSNS[f'ui-prod-{mss_cnt}-{i}']
        gmess.add((ped, ECSNS.tieneProducto, pn))
        gmess.add((pn, ECSNS.idProducto,  Literal(item['id'])))
        gmess.add((pn, ECSNS.nombre,      Literal(item.get('nombre', ''))))
        gmess.add((pn, ECSNS.precio,      Literal(float(item.get('precio', 0)))))
        gmess.add((pn, ECSNS.cantidad,    Literal(int(item.get('cantidad', 1)))))
        gmess.add((pn, ECSNS.peso,        Literal(float(item.get('peso', 0)))))
        gmess.add((pn, ECSNS.vendedor,    Literal(item.get('vendedor', 'tienda'))))
        gmess.add((pn, ECSNS.gestionEnvio, Literal(item.get('gestion_envio', 'tienda'))))
    try:
        gr_resp = send_message(
            build_message(gmess, perf=ACL.request, sender=UsuarioAgent.uri,
                          receiver=agn.AgenteGestorPedidos, content=ped, msgcnt=mss_cnt),
            addr
        )
        mss_cnt += 1
        for s in gr_resp.subjects(RDF.type, ECSNS.Factura):
            return {
                'id':    str(gr_resp.value(s, ECSNS.idFactura) or ''),
                'total': float(gr_resp.value(s, ECSNS.total)   or 0),
                'fecha': str(gr_resp.value(s, ECSNS.fecha)     or ''),
            }, None
        return None, 'El sistema no devolvió una factura'
    except Exception as e:
        logger.warning(f'[Usuario] Error enviando pedido: {e}')
        mss_cnt += 1
        return None, f'Error: {e}'


def _literal_bool(val):
    if val is None:
        return False
    if isinstance(val, bool):
        return val
    return str(val).strip().lower() in ('true', '1', 'yes')


def solicitar_devolucion(comprador, factura_id, razon, fecha_recepcion):
    global mss_cnt
    factura = buscar_factura_local(factura_id)
    if not factura:
        return None, 'Factura no encontrada.'
    if _norm_comprador(comprador) != _norm_comprador(factura.get('comprador', '')):
        return None, 'El nombre no coincide con el comprador de esta factura.'
    if factura_id in ids_facturas_devueltas():
        return None, 'Esta factura ya tiene una devolución aceptada.'
    addr = get_agent_address('Ag.Devolucion')
    if not addr:
        return None, 'AgenteDevolucion no disponible'
    gmess = Graph()
    gmess.bind('ecsns', ECSNS)
    sol = ECSNS['sol-dev-' + str(mss_cnt)]
    gmess.add((sol, RDF.type,               ECSNS.SolicitudDevolucion))
    gmess.add((sol, ECSNS.comprador,        Literal(comprador)))
    gmess.add((sol, ECSNS.idFactura,        Literal(factura_id)))
    gmess.add((sol, ECSNS.razonDevolucion,  Literal(razon)))
    gmess.add((sol, ECSNS.fechaRecepcion,   Literal(fecha_recepcion)))
    try:
        gr_resp = send_message(
            build_message(gmess, perf=ACL.request, sender=UsuarioAgent.uri,
                          receiver=agn.AgenteDevolucion, content=sol, msgcnt=mss_cnt),
            addr
        )
        mss_cnt += 1
        for s in gr_resp.subjects(RDF.type, ECSNS.Devolucion):
            return {
                'id':       str(gr_resp.value(s, ECSNS.idDevolucion)      or ''),
                'aceptada': _literal_bool(gr_resp.value(s, ECSNS.aceptada)),
                'motivo':   str(gr_resp.value(s, ECSNS.motivoDevolucion)  or ''),
                'empresa':  str(gr_resp.value(s, ECSNS.empresaMensajeria) or ''),
            }, None
        return None, 'Respuesta inesperada del agente de devoluciones'
    except Exception as e:
        mss_cnt += 1
        return None, f'Error: {e}'


def enviar_valoracion(comprador, producto_id, pedido_id, nombre_producto, puntuacion, comentario=''):
    global mss_cnt
    if ya_valorado_en_pedido(comprador, producto_id, pedido_id):
        return False, 'Ya has valorado este producto en ese pedido.'
    addr = get_agent_address('Ag.Experiencia')
    if not addr:
        return False, 'AgenteExperiencia no disponible'
    gmess = Graph()
    gmess.bind('ecsns', ECSNS)
    val = ECSNS['val-' + str(mss_cnt)]
    gmess.add((val, RDF.type,         ECSNS.NuevaValoracion))
    gmess.add((val, ECSNS.comprador,  Literal(comprador)))
    gmess.add((val, ECSNS.idProducto, Literal(producto_id)))
    gmess.add((val, ECSNS.idPedido,   Literal(_pedido_key(pedido_id))))
    gmess.add((val, ECSNS.nombre,     Literal(nombre_producto or producto_id)))
    gmess.add((val, ECSNS.puntuacion, Literal(int(puntuacion))))
    gmess.add((val, ECSNS.comentario, Literal(comentario)))
    try:
        gr_resp = send_message(
            build_message(gmess, perf=ACL.request, sender=UsuarioAgent.uri,
                          receiver=agn.AgenteExperiencia, content=val, msgcnt=mss_cnt),
            addr
        )
        mss_cnt += 1
        if list(gr_resp.subjects(RDF.type, ECSNS.ValoracionOK)):
            return True, None
        return False, 'No se pudo registrar la valoración.'
    except Exception as e:
        mss_cnt += 1
        return False, f'Error: {e}'


# ---------------------------------------------------------------------------
# Web routes
# ---------------------------------------------------------------------------

@app.route('/login', methods=['GET', 'POST'])
def login():
    error = None
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        if username:
            session['usuario'] = username
            return redirect(request.args.get('next') or url_for('index'))
        error = 'Introduce un nombre de usuario.'
    return render_template('usuario/login.html', num_carrito=_num_carrito(), error=error)


@app.route('/logout')
def logout():
    session.pop('usuario', None)
    session.pop('carrito', None)
    session.pop('ultimo_pedido', None)
    return redirect(url_for('login'))


@app.route('/')
def index():
    facturas = load_json(FACTURAS_PATH)
    return render_template(
        'usuario/index.html',
        num_carrito=_num_carrito(),
        num_facturas=len(facturas),
        recomendaciones=_recomendaciones[-6:],
    )


@app.route('/buscar', methods=['GET', 'POST'])
def buscar():
    productos, error = [], None
    filtros = {'nombre': '', 'categoria': '', 'precio_max': '', 'val_min': ''}
    if request.method == 'POST':
        filtros = {k: request.form.get(k, '') for k in filtros}
        productos, error = buscar_productos(**filtros)
    elif request.args.get('categoria'):
        filtros['categoria'] = request.args.get('categoria', '').strip()
        productos, error = buscar_productos(**filtros)
    return render_template(
        'usuario/buscar.html',
        num_carrito=_num_carrito(),
        productos=productos,
        error=error,
        filtros=filtros,
        categorias=listar_categorias(),
    )


@app.route('/recomendaciones', methods=['GET', 'POST'])
def recomendaciones():
    error = None
    comprador = (request.form.get('comprador', '').strip()
                 or request.args.get('comprador', '').strip()
                 or _comprador_sesion())
    recs_nuevas = []
    if request.method == 'POST' and comprador:
        recs_nuevas, error = solicitar_recomendaciones(comprador)
    visibles = _recomendaciones[-12:]
    if comprador:
        visibles = [enriquecer_recomendacion(dict(r)) for r in visibles]
    return render_template(
        'usuario/recomendaciones.html',
        num_carrito=_num_carrito(),
        recomendaciones=visibles,
        comprador=comprador,
        recs_nuevas=len(recs_nuevas),
        error=error,
    )


@app.route('/carrito/anadir', methods=['POST'])
def carrito_anadir():
    carrito = session.get('carrito', [])
    prod = {
        'id':       request.form.get('id', ''),
        'nombre':   request.form.get('nombre', ''),
        'precio':   float(request.form.get('precio', 0)),
        'peso':     float(request.form.get('peso', 0)),
        'cantidad': 1,
    }
    for item in carrito:
        if item['id'] == prod['id']:
            item['cantidad'] += 1
            session['carrito'] = carrito
            return redirect(url_for('buscar'))
    carrito.append(prod)
    session['carrito'] = carrito
    return redirect(url_for('buscar'))


@app.route('/carrito')
def carrito_ver():
    carrito = session.get('carrito', [])
    total = round(sum(i['precio'] * i['cantidad'] for i in carrito), 2)
    return render_template(
        'usuario/carrito.html',
        num_carrito=len(carrito),
        carrito=carrito,
        total=total,
    )


@app.route('/carrito/eliminar/<prod_id>')
def carrito_eliminar(prod_id):
    session['carrito'] = [i for i in session.get('carrito', []) if i['id'] != prod_id]
    return redirect(url_for('carrito_ver'))


@app.route('/carrito/vaciar')
def carrito_vaciar():
    session['carrito'] = []
    return redirect(url_for('carrito_ver'))


@app.route('/pedido', methods=['GET', 'POST'])
def pedido():
    carrito = session.get('carrito', [])
    if not carrito:
        return redirect(url_for('buscar'))
    error = None
    comprador_default = _comprador_sesion()
    if request.method == 'POST':
        comprador   = request.form.get('comprador', '').strip() or comprador_default
        direccion   = request.form.get('direccion', '').strip()
        prioridad   = request.form.get('prioridad', 'normal')
        metodo_pago = request.form.get('metodo_pago', 'tarjeta')
        if not comprador or not direccion:
            error = 'Rellena todos los campos obligatorios'
        else:
            factura, err = enviar_pedido(comprador, direccion, prioridad, metodo_pago, carrito)
            if factura:
                session['carrito'] = []
                session['comprador'] = comprador
                session['ultimo_pedido'] = {'factura': factura, 'comprador': comprador, 'items': carrito}
                return redirect(url_for('pedido_confirmado'))
            else:
                error = err or 'Error al procesar el pedido'
    total = round(sum(i['precio'] * i['cantidad'] for i in carrito), 2)
    return render_template(
        'usuario/pedido.html',
        num_carrito=len(carrito),
        carrito=carrito,
        total=total,
        error=error,
        comprador_default=comprador_default,
    )


@app.route('/pedido/confirmado')
def pedido_confirmado():
    datos = session.get('ultimo_pedido')
    if not datos:
        return redirect(url_for('index'))
    factura = datos['factura']
    fid = factura.get('id', '')
    envios = _envios_notificados.get(fid, [])
    if not envios:
        for f in load_json(FACTURAS_PATH):
            if f.get('id') == fid:
                envios = f.get('envios_logistico', [])
                break
    return render_template(
        'usuario/pedido_confirmado.html',
        num_carrito=0,
        factura=factura,
        comprador=datos['comprador'],
        items=datos['items'],
        envios=envios,
    )


@app.route('/historial')
def historial():
    comprador = _comprador_sesion()
    facturas = facturas_para_vista(solo_comprador=comprador if comprador else None)
    for f in facturas:
        f['estado_envio'] = estado_envio_factura(f)
    return render_template(
        'usuario/historial.html',
        num_carrito=_num_carrito(),
        facturas=facturas,
        comprador=comprador,
    )


@app.route('/devolucion', methods=['GET', 'POST'])
def devolucion():
    resultado = error = None
    comprador_form = (
        request.form.get('comprador', '').strip()
        or request.args.get('comprador', '').strip()
        or _comprador_sesion()
    )
    if request.method == 'POST':
        comprador = comprador_form
        factura_id = request.form.get('factura_id', '').strip()
        razon = request.form.get('razon', '').strip()
        fecha_recepcion = request.form.get('fecha_recepcion', '').strip()
        if factura_id and razon and fecha_recepcion:
            if not comprador:
                error = 'Indica tu nombre de comprador.'
            else:
                resultado, error = solicitar_devolucion(
                    comprador, factura_id, razon, fecha_recepcion
                )
        elif comprador:
            pass
        else:
            error = 'Indica tu nombre de comprador.'
    facturas_elegibles = (
        facturas_elegibles_devolucion(comprador_form) if comprador_form else []
    )
    mis_devoluciones = devoluciones_para_vista(comprador_form) if comprador_form else []
    return render_template(
        'usuario/devolucion.html',
        num_carrito=_num_carrito(),
        facturas=facturas_elegibles,
        mis_devoluciones=mis_devoluciones,
        comprador=comprador_form,
        resultado=resultado,
        error=error,
        hoy=date.today().isoformat(),
    )


@app.route('/valorar', methods=['GET', 'POST'])
def valorar():
    facturas = load_json(FACTURAS_PATH)
    resultado = error = None
    comprador_form = (
        request.form.get('comprador', '').strip()
        or request.args.get('comprador', '').strip()
        or _comprador_sesion()
    )
    linea_preseleccionada = request.args.get('linea', '').strip()
    if request.method == 'POST':
        comprador = comprador_form
        linea = request.form.get('linea_valoracion', '').strip()
        if linea:
            puntuacion = request.form.get('puntuacion', 3)
            comentario = request.form.get('comentario', '').strip()
            if '|' not in linea:
                error = 'Selección de pedido y producto no válida.'
            else:
                factura_id, prod_id = linea.split('|', 1)
                pendientes, _ = lineas_compra_para_valorar(comprador)
                linea_datos = next(
                    (l for l in pendientes
                     if l['factura_id'] == factura_id and l['producto_id'] == prod_id),
                    None,
                )
                if not comprador:
                    error = 'Indica tu nombre de comprador.'
                elif not linea_datos:
                    error = 'Ya has valorado este producto en ese pedido o no pertenece a tus compras.'
                else:
                    ok, err = enviar_valoracion(
                        comprador,
                        prod_id,
                        factura_id,
                        linea_datos.get('nombre', ''),
                        puntuacion,
                        comentario,
                    )
                    if ok:
                        _quitar_solicitud_feedback(comprador, factura_id, prod_id)
                    resultado = 'Valoración registrada correctamente.' if ok else None
                    error = err if not ok else None
    productos_pendientes, productos_valorados = [], []
    if comprador_form:
        productos_pendientes, productos_valorados = lineas_compra_para_valorar(comprador_form)
    return render_template(
        'usuario/valorar.html',
        num_carrito=_num_carrito(),
        productos=productos_pendientes,
        productos_valorados=productos_valorados,
        comprador=comprador_form,
        tiene_compras=bool(facturas),
        resultado=resultado,
        error=error,
        linea_preseleccionada=linea_preseleccionada,
        solicitudes_feedback=_solicitudes_feedback,
    )


# ---------------------------------------------------------------------------
# /comm — mensajes de agentes
# ---------------------------------------------------------------------------

@app.route('/comm', methods=['GET', 'POST'])
def comunicacion():
    global mss_cnt
    message = request.args.get('content') or request.form.get('content')
    if not message:
        return ('<html><head><title>AgenteUsuario</title></head>'
                '<body style="font-family:sans-serif;padding:32px">'
                '<h2>AgenteUsuario</h2>'
                '<p><strong>Estado:</strong> activo &nbsp;|&nbsp; <strong>Puerto:</strong> ' + str(port) + '</p>'
                '<p style="color:#666">Endpoint ACL/RDF entre agentes. '
                'La interfaz web está en '
                '<a href="http://localhost:9020/">localhost:9020</a>.</p>'
                '</body></html>'), 200, {'Content-Type': 'text/html; charset=utf-8'}
    try:
        gm = Graph()
        gm.parse(data=message, format='xml')
    except Exception as e:
        logger.warning(f'[Usuario] /comm parse error: {e}')
        gr = build_message(Graph(), ACL['not-understood'], sender=UsuarioAgent.uri, msgcnt=mss_cnt)
        mss_cnt += 1
        return gr.serialize(format='xml')
    msgdic = get_message_properties(gm)

    if msgdic is None:
        gr = build_message(Graph(), ACL['not-understood'], sender=UsuarioAgent.uri, msgcnt=mss_cnt)
        mss_cnt += 1
        return gr.serialize(format='xml')

    perf    = msgdic.get('performative')
    content = msgdic.get('content')
    accion  = gm.value(subject=content, predicate=RDF.type) if content else None

    if perf == ACL.inform and accion == ECSNS.NotificacionEnvios:
        pedido_id  = str(gm.value(content, ECSNS.idPedido) or '')
        sub_envios = []
        for en in gm.objects(content, ECSNS.tieneSubEnvio):
            sub_envios.append({
                'id':            str(gm.value(en, ECSNS.idEnvio)            or ''),
                'centro':        str(gm.value(en, ECSNS.tieneCentro)        or ''),
                'transportista': str(gm.value(en, ECSNS.tieneTransportista) or ''),
                'fecha':         str(gm.value(en, ECSNS.tieneFechaEntrega)  or ''),
                'productos':     [str(o) for o in gm.objects(en, ECSNS.tieneProductoId)],
            })
        _envios_notificados[pedido_id] = sub_envios
        logger.info(f'[Usuario] NotificacionEnvios: pedido {pedido_id}, {len(sub_envios)} envío(s)')
        gr = build_message(Graph(), ACL.confirm, sender=UsuarioAgent.uri,
                           receiver=msgdic['sender'], msgcnt=mss_cnt)

    elif perf == ACL.inform and accion == ECSNS.RecomendacionesProactivas:
        nuevas = _parsear_recomendaciones_grafo(gm, content)
        _registrar_recomendaciones(nuevas)
        logger.info(f'[Usuario] Recomendaciones proactivas: +{len(nuevas)}, total {len(_recomendaciones)}')
        gr = build_message(Graph(), ACL.confirm, sender=UsuarioAgent.uri,
                           receiver=msgdic['sender'], msgcnt=mss_cnt)

    elif perf == ACL.request and accion == ECSNS.SolicitudFeedback:
        _añadir_solicitud_feedback(gm, content)
        gr = build_message(Graph(), ACL.confirm, sender=UsuarioAgent.uri,
                           receiver=msgdic['sender'], msgcnt=mss_cnt)

    elif perf == ACL.inform and accion == ECSNS.InformarDesicion:
        comprador  = str(gm.value(content, ECSNS.comprador)        or '')
        dev_id     = str(gm.value(content, ECSNS.idDevolucion)      or '')
        motivo     = str(gm.value(content, ECSNS.motivoDevolucion)  or '')
        empresa    = str(gm.value(content, ECSNS.empresaMensajeria) or '')
        _notificaciones_devolucion.append({
            'comprador': comprador,
            'id':        dev_id,
            'motivo':    motivo,
            'empresa':   empresa,
        })
        logger.info(f'[Usuario] Devolución aceptada recibida: {dev_id} — {motivo}')
        gr = build_message(Graph(), ACL.confirm, sender=UsuarioAgent.uri,
                           receiver=msgdic['sender'], msgcnt=mss_cnt)

    else:
        gr = build_message(Graph(), ACL['not-understood'], sender=UsuarioAgent.uri, msgcnt=mss_cnt)

    mss_cnt += 1
    return gr.serialize(format='xml')


@app.route('/Stop')
def stop():
    cola1.put(0)
    shutdown_server()
    return 'Parando AgenteUsuario'


# ---------------------------------------------------------------------------
# Agent behaviour
# ---------------------------------------------------------------------------

def agentbehavior1(cola):
    register_message()
    logger.info('[Usuario] UI disponible en http://%s:%d/' % (hostaddr, port))
    fin = False
    while not fin:
        time.sleep(1)
        if not cola.empty() and cola.get() == 0:
            fin = True


if __name__ == '__main__':
    os.makedirs(DATA_DIR, exist_ok=True)
    ab1 = Process(target=agentbehavior1, args=(cola1,))
    ab1.start()
    app.run(host=flask_host, port=port)
    ab1.join()
    logger.info('[Usuario] Fin')
