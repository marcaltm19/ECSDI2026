import argparse
import json
import logging
import os
import socket
import sys
import time
from datetime import datetime
from multiprocessing import Process, Queue

from flask import Flask, request, redirect, url_for, session, render_template, flash
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

TEMPLATES_DIR = os.path.join(os.path.dirname(__file__), 'templates')
app = Flask(__name__, template_folder=TEMPLATES_DIR)
app.secret_key = 'ecsdi2026-usuario-secret'
if not args.verbose:
    logging.getLogger('werkzeug').setLevel(logging.ERROR)

agn = Namespace('http://www.agentes.org#')
mss_cnt = 0

DATA_DIR        = os.path.join(os.path.dirname(__file__), 'data')
FACTURAS_PATH   = os.path.join(DATA_DIR, 'facturas.json')
PEDIDOS_PATH    = os.path.join(DATA_DIR, 'pedidos.json')

_envios_notificados = {}  # pedido_id -> lista sub_envios
_recomendaciones = []     # lista de productos recomendados

_addr_cache = {}

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
    global mss_cnt
    if agent_type_str in _addr_cache:
        return _addr_cache[agent_type_str]
    agent_type = ECSNS[agent_type_str]
    gmess = Graph()
    gmess.bind('dso', DSO)
    search_obj = agn['Search-' + str(mss_cnt)]
    gmess.add((search_obj, RDF.type,      DSO.Search))
    gmess.add((search_obj, DSO.AgentType, agent_type))
    msg = build_message(gmess, perf=ACL.request, sender=UsuarioAgent.uri,
                        receiver=DirectoryAgent.uri, content=search_obj, msgcnt=mss_cnt)
    try:
        response = http_requests.get(
            DirectoryAgent.address,
            params={'content': msg.serialize(format='xml')},
            timeout=5
        )
        mss_cnt += 1
        gr = Graph()
        gr.parse(data=response.text, format='xml')
        for s, p, o in gr:
            if p == DSO.Address:
                _addr_cache[agent_type_str] = str(o)
                return str(o)
    except Exception as e:
        logger.warning(f'[Usuario] Error buscando agente {agent_type_str}: {e}')
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


# ---------------------------------------------------------------------------
# Agent actions
# ---------------------------------------------------------------------------

def buscar_productos(nombre='', categoria='', precio_max='', val_min=''):
    global mss_cnt
    addr = get_agent_address('Ag.Comprador')
    if not addr:
        logger.warning('[Usuario] AgenteComprador no disponible')
        return [], 'AgenteComprador no disponible en el sistema'

    gmess = Graph()
    gmess.bind('ecsns', ECSNS)
    busq = ECSNS['busqueda-' + str(mss_cnt)]
    gmess.add((busq, RDF.type, ECSNS.Busqueda))
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
                'vendedor':  str(gr_resp.value(s, ECSNS.vendedor)     or 'tienda'),
            }
            if nombre.strip() and nombre.strip().lower() not in prod['nombre'].lower():
                continue
            productos.append(prod)
        return productos, None
    except Exception as e:
        logger.warning(f'[Usuario] Error buscando productos: {e}')
        mss_cnt += 1
        return [], f'Error de comunicacion: {e}'


def enviar_pedido(comprador, direccion, prioridad, metodo_pago, carrito):
    global mss_cnt
    addr = get_agent_address('Ag.GestorDePedidos')
    if not addr:
        return None, 'AgenteGestorPedidos no disponible'

    gmess = Graph()
    gmess.bind('ecsns', ECSNS)
    ped = ECSNS['pedido-ui-' + str(mss_cnt)]
    gmess.add((ped, RDF.type,         ECSNS.Pedido))
    gmess.add((ped, ECSNS.comprador,  Literal(comprador)))
    gmess.add((ped, ECSNS.direccion,  Literal(direccion)))
    gmess.add((ped, ECSNS.prioridad,  Literal(prioridad)))
    gmess.add((ped, ECSNS.metodoPago, Literal(metodo_pago)))
    for i, item in enumerate(carrito):
        pn = ECSNS[f'ui-prod-{mss_cnt}-{i}']
        gmess.add((ped, ECSNS.tieneProducto, pn))
        gmess.add((pn, ECSNS.idProducto, Literal(item['id'])))
        gmess.add((pn, ECSNS.nombre,     Literal(item.get('nombre', ''))))
        gmess.add((pn, ECSNS.precio,     Literal(float(item.get('precio', 0)))))
        gmess.add((pn, ECSNS.cantidad,   Literal(int(item.get('cantidad', 1)))))
        gmess.add((pn, ECSNS.peso,       Literal(float(item.get('peso', 0)))))
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
        return None, 'El sistema no devolvio una factura'
    except Exception as e:
        logger.warning(f'[Usuario] Error enviando pedido: {e}')
        mss_cnt += 1
        return None, f'Error: {e}'


def solicitar_devolucion(comprador, factura_id, razon, fecha_recepcion):
    global mss_cnt
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
                'id':       str(gr_resp.value(s, ECSNS.idDevolucion)     or ''),
                'aceptada': str(gr_resp.value(s, ECSNS.aceptada)         or 'False') == 'True',
                'motivo':   str(gr_resp.value(s, ECSNS.motivoDevolucion) or ''),
                'empresa':  str(gr_resp.value(s, ECSNS.empresaMensajeria)or ''),
            }, None
        return None, 'Respuesta inesperada del agente de devoluciones'
    except Exception as e:
        mss_cnt += 1
        return None, f'Error: {e}'


def enviar_valoracion(comprador, producto_id, puntuacion, comentario=''):
    global mss_cnt
    addr = get_agent_address('Ag.Experiencia')
    if not addr:
        return False, 'AgenteExperiencia no disponible'
    gmess = Graph()
    gmess.bind('ecsns', ECSNS)
    val = ECSNS['val-' + str(mss_cnt)]
    gmess.add((val, RDF.type,         ECSNS.Valoracion))
    gmess.add((val, ECSNS.comprador,  Literal(comprador)))
    gmess.add((val, ECSNS.idProducto, Literal(producto_id)))
    gmess.add((val, ECSNS.puntuacion, Literal(int(puntuacion))))
    gmess.add((val, ECSNS.comentario, Literal(comentario)))
    gmess.add((val, ECSNS.fecha,      Literal(datetime.now().isoformat())))
    try:
        send_message(
            build_message(gmess, perf=ACL.inform, sender=UsuarioAgent.uri,
                          receiver=agn.AgenteExperiencia, content=val, msgcnt=mss_cnt),
            addr
        )
        mss_cnt += 1
        return True, None
    except Exception as e:
        mss_cnt += 1
        return False, f'Error: {e}'


# ---------------------------------------------------------------------------
# Web routes
# ---------------------------------------------------------------------------

@app.route('/')
def index():
    recs = _recomendaciones[-6:]
    facturas = load_json(FACTURAS_PATH)
    return render_template('usuario/index.html',
                           recomendaciones=recs,
                           num_facturas=len(facturas),
                           num_carrito=len(session.get('carrito', [])))


@app.route('/buscar', methods=['GET', 'POST'])
def buscar():
    productos, error = [], None
    filtros = {'nombre': '', 'categoria': '', 'precio_max': '', 'val_min': ''}
    if request.method == 'POST':
        filtros = {
            'nombre':     request.form.get('nombre', ''),
            'categoria':  request.form.get('categoria', ''),
            'precio_max': request.form.get('precio_max', ''),
            'val_min':    request.form.get('val_min', ''),
        }
        productos, error = buscar_productos(**filtros)
    return render_template('usuario/buscar.html',
                           productos=productos, filtros=filtros, error=error,
                           num_carrito=len(session.get('carrito', [])))


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
            flash(f'"{prod["nombre"]}" actualizado en el carrito', 'success')
            return redirect(url_for('buscar'))
    carrito.append(prod)
    session['carrito'] = carrito
    flash(f'"{prod["nombre"]}" añadido al carrito', 'success')
    return redirect(url_for('buscar'))


@app.route('/carrito')
def carrito_ver():
    carrito = session.get('carrito', [])
    total = round(sum(i['precio'] * i['cantidad'] for i in carrito), 2)
    return render_template('usuario/carrito.html', carrito=carrito, total=total,
                           num_carrito=len(carrito))


@app.route('/carrito/eliminar/<prod_id>')
def carrito_eliminar(prod_id):
    carrito = [i for i in session.get('carrito', []) if i['id'] != prod_id]
    session['carrito'] = carrito
    return redirect(url_for('carrito_ver'))


@app.route('/carrito/vaciar')
def carrito_vaciar():
    session['carrito'] = []
    return redirect(url_for('carrito_ver'))


@app.route('/pedido', methods=['GET', 'POST'])
def pedido():
    carrito = session.get('carrito', [])
    if not carrito:
        flash('El carrito está vacío', 'warning')
        return redirect(url_for('buscar'))
    error = None
    if request.method == 'POST':
        comprador   = request.form.get('comprador', '').strip()
        direccion   = request.form.get('direccion', '').strip()
        prioridad   = request.form.get('prioridad', 'normal')
        metodo_pago = request.form.get('metodo_pago', 'tarjeta')
        if not comprador or not direccion:
            error = 'Rellena todos los campos obligatorios'
        else:
            factura, err = enviar_pedido(comprador, direccion, prioridad, metodo_pago, carrito)
            if factura:
                session['carrito'] = []
                session['ultimo_pedido'] = {'factura': factura, 'comprador': comprador, 'items': carrito}
                return redirect(url_for('pedido_confirmado'))
            else:
                error = err or 'Error al procesar el pedido'
    total = round(sum(i['precio'] * i['cantidad'] for i in carrito), 2)
    return render_template('usuario/pedido.html', carrito=carrito, total=total, error=error,
                           num_carrito=len(carrito))


@app.route('/pedido/confirmado')
def pedido_confirmado():
    datos = session.get('ultimo_pedido')
    if not datos:
        return redirect(url_for('index'))
    factura   = datos['factura']
    envios = _envios_notificados.get(factura.get('id', ''), [])
    return render_template('usuario/pedido_confirmado.html',
                           factura=factura, comprador=datos['comprador'],
                           items=datos.get('items', []), envios=envios,
                           num_carrito=0)


@app.route('/historial')
def historial():
    facturas = load_json(FACTURAS_PATH)
    pedidos_map = {p['id']: p for p in load_json(PEDIDOS_PATH)}
    for f in facturas:
        fid = f.get('id', '')
        f['envios_logistico'] = _envios_notificados.get(fid, pedidos_map.get(fid, {}).get('envios', []))
    return render_template('usuario/historial.html',
                           facturas=list(reversed(facturas)),
                           num_carrito=len(session.get('carrito', [])))


@app.route('/devolucion', methods=['GET', 'POST'])
def devolucion():
    facturas = load_json(FACTURAS_PATH)
    resultado = None
    error = None
    if request.method == 'POST':
        comprador        = request.form.get('comprador', '').strip()
        factura_id       = request.form.get('factura_id', '').strip()
        razon            = request.form.get('razon', '').strip()
        fecha_recepcion  = request.form.get('fecha_recepcion', '').strip()
        if not all([comprador, factura_id, razon, fecha_recepcion]):
            error = 'Rellena todos los campos'
        else:
            resultado, error = solicitar_devolucion(comprador, factura_id, razon, fecha_recepcion)
    return render_template('usuario/devolucion.html',
                           facturas=facturas, resultado=resultado, error=error,
                           num_carrito=len(session.get('carrito', [])))


@app.route('/valorar', methods=['GET', 'POST'])
def valorar():
    facturas = load_json(FACTURAS_PATH)
    productos_comprados = {}
    for f in facturas:
        for p in f.get('productos', []):
            productos_comprados[p['id']] = p
    resultado = None
    error = None
    if request.method == 'POST':
        comprador  = request.form.get('comprador', '').strip()
        prod_id    = request.form.get('producto_id', '').strip()
        puntuacion = request.form.get('puntuacion', 3)
        comentario = request.form.get('comentario', '').strip()
        ok, err = enviar_valoracion(comprador, prod_id, puntuacion, comentario)
        if ok:
            resultado = 'Valoración enviada correctamente'
        else:
            error = err or 'Error enviando valoración'
    return render_template('usuario/valorar.html',
                           productos=list(productos_comprados.values()),
                           resultado=resultado, error=error,
                           num_carrito=len(session.get('carrito', [])))


# ---------------------------------------------------------------------------
# /comm — mensajes de agentes
# ---------------------------------------------------------------------------

@app.route('/comm', methods=['GET', 'POST'])
def comunicacion():
    global mss_cnt
    message = request.args.get('content') or request.form.get('content')
    gm = Graph()
    gm.parse(data=message, format='xml')
    msgdic = get_message_properties(gm)

    if msgdic is None:
        gr = build_message(Graph(), ACL['not-understood'], sender=UsuarioAgent.uri, msgcnt=mss_cnt)
        mss_cnt += 1
        return gr.serialize(format='xml')

    perf    = msgdic.get('performative')
    content = msgdic.get('content')
    accion  = gm.value(subject=content, predicate=RDF.type) if content else None

    if perf == ACL.inform and accion == ECSNS.NotificacionEnvios:
        pedido_id = str(gm.value(content, ECSNS.idPedido) or '')
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
        logger.info(f'[Usuario] NotificacionEnvios: pedido {pedido_id} con {len(sub_envios)} envios')
        gr = build_message(Graph(), ACL.confirm, sender=UsuarioAgent.uri,
                           receiver=msgdic['sender'], msgcnt=mss_cnt)

    elif perf == ACL.inform and accion == ECSNS.RecomendacionesProactivas:
        for pn in gm.objects(content, ECSNS.tieneProducto):
            rec = {
                'id':       str(gm.value(pn, ECSNS.idProducto)  or ''),
                'nombre':   str(gm.value(pn, ECSNS.nombre)       or ''),
                'precio':   float(gm.value(pn, ECSNS.precio)     or 0),
                'categoria':str(gm.value(pn, ECSNS.categoria)    or ''),
            }
            if rec['id'] and rec not in _recomendaciones:
                _recomendaciones.append(rec)
        logger.info(f'[Usuario] Recomendaciones: {len(_recomendaciones)} total')
        gr = build_message(Graph(), ACL.confirm, sender=UsuarioAgent.uri,
                           receiver=msgdic['sender'], msgcnt=mss_cnt)

    elif perf == ACL.request and accion == ECSNS.SolicitudFeedback:
        logger.info('[Usuario] SolicitudFeedback recibida del AgenteExperiencia')
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
