import argparse
import json
import logging
import os
import socket
import sys
import time
from datetime import datetime
from multiprocessing import Process, Queue

from flask import Flask, request, redirect, url_for, session, make_response
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

app = Flask(__name__)
app.secret_key = 'ecsdi2026-usuario-secret'
if not args.verbose:
    logging.getLogger('werkzeug').setLevel(logging.ERROR)

agn = Namespace('http://www.agentes.org#')
mss_cnt = 0

DATA_DIR      = os.path.join(os.path.dirname(__file__), 'data')
FACTURAS_PATH = os.path.join(DATA_DIR, 'facturas.json')
PEDIDOS_PATH  = os.path.join(DATA_DIR, 'pedidos.json')

_envios_notificados = {}
_recomendaciones    = []
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


# ---------------------------------------------------------------------------
# Agent actions
# ---------------------------------------------------------------------------

def buscar_productos(nombre='', categoria='', precio_max='', val_min=''):
    global mss_cnt
    addr = get_agent_address('Ag.Comprador')
    if not addr:
        return [], 'AgenteComprador no disponible en el sistema. ¿Está arrancado?'

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
        return [], f'Error de comunicación con AgenteComprador: {e}'


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
        return None, 'El sistema no devolvió una factura'
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
                'id':       str(gr_resp.value(s, ECSNS.idDevolucion)      or ''),
                'aceptada': str(gr_resp.value(s, ECSNS.aceptada)          or 'False') == 'True',
                'motivo':   str(gr_resp.value(s, ECSNS.motivoDevolucion)  or ''),
                'empresa':  str(gr_resp.value(s, ECSNS.empresaMensajeria) or ''),
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
# HTML inline renderer (sin dependencia de templates/)
# ---------------------------------------------------------------------------

NAV = '''<nav style="background:#1a3c5e;padding:12px 24px;display:flex;gap:16px;align-items:center">
  <span style="color:#fff;font-weight:700;font-size:17px;margin-right:auto">🛒 ECSDI Shop 2026</span>
  <a href="/" style="color:#cde;text-decoration:none;padding:4px 10px;border-radius:4px">Inicio</a>
  <a href="/buscar" style="color:#cde;text-decoration:none;padding:4px 10px;border-radius:4px">Buscar</a>
  <a href="/carrito" style="color:#cde;text-decoration:none;padding:4px 10px;border-radius:4px">🛒 Carrito{badge}</a>
  <a href="/historial" style="color:#cde;text-decoration:none;padding:4px 10px;border-radius:4px">Historial</a>
  <a href="/valorar" style="color:#cde;text-decoration:none;padding:4px 10px;border-radius:4px">Valorar</a>
  <a href="/devolucion" style="color:#cde;text-decoration:none;padding:4px 10px;border-radius:4px">Devolución</a>
</nav>'''

CSS = '''<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:'Segoe UI',sans-serif;background:#f4f6f8;color:#333;font-size:15px}
.container{max-width:1000px;margin:28px auto;padding:0 16px}
h1{font-size:22px;margin-bottom:16px;color:#1a3c5e}
h2{font-size:16px;margin-bottom:10px;color:#1a3c5e}
.card{background:#fff;border-radius:8px;padding:20px;margin-bottom:16px;box-shadow:0 1px 4px rgba(0,0,0,.08)}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:12px}
.pc{background:#fff;border-radius:8px;padding:14px;box-shadow:0 1px 4px rgba(0,0,0,.08);display:flex;flex-direction:column;gap:5px}
.pc .nm{font-weight:600}.pc .pr{color:#1a7a3c;font-weight:700;font-size:16px}
.pc .ct{font-size:12px;color:#888;background:#f0f0f0;padding:2px 8px;border-radius:10px;display:inline-block}
.pc .vl{font-size:12px;color:#e6a817}
btn,button,input[type=submit]{background:#1a3c5e;color:#fff;border:none;padding:8px 16px;border-radius:5px;cursor:pointer;font-size:14px;display:inline-block;text-decoration:none}
button:hover,input[type=submit]:hover{background:#2a5480}
.bg{background:#1a7a3c}.bg:hover{background:#155f2f}
.br{background:#c0392b}.br:hover{background:#a93226}
input[type=text],input[type=number],input[type=date],select,textarea{padding:7px 10px;border:1px solid #ccc;border-radius:5px;font-size:14px;width:100%}
label{font-size:13px;color:#555;margin-bottom:3px;display:block}
.fg{margin-bottom:12px}
table{width:100%;border-collapse:collapse}th,td{padding:9px 12px;text-align:left;border-bottom:1px solid #eee}
th{background:#f0f4f8;font-size:13px;color:#555}
.badge{background:#e8f4fd;color:#1a3c5e;padding:2px 8px;border-radius:10px;font-size:12px;font-weight:600}
.ok{background:#d4edda;color:#155724;border:1px solid #c3e6cb;padding:10px 14px;border-radius:6px;margin-bottom:12px;font-size:14px}
.err{background:#f8d7da;color:#721c24;border:1px solid #f5c6cb;padding:10px 14px;border-radius:6px;margin-bottom:12px;font-size:14px}
.envtag{background:#e8f8ef;color:#1a7a3c;padding:3px 10px;border-radius:6px;font-size:13px;display:inline-block;margin:2px}
.empty{text-align:center;color:#aaa;padding:36px 0}
form.il{display:inline}
.nb{background:#e9ecef;color:#555;border:none;padding:8px 16px;border-radius:5px;cursor:pointer;font-size:14px}
.nb:hover{background:#dee2e6}
</style>'''

def html_page(body, num_carrito=0):
    badge = f' <span style="background:#e74c3c;color:#fff;border-radius:10px;padding:1px 6px;font-size:11px">{num_carrito}</span>' if num_carrito else ''
    nav = NAV.replace('{badge}', badge)
    return f'<!DOCTYPE html><html lang="es"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>ECSDI Shop 2026</title>{CSS}</head><body>{nav}<div class="container">{body}</div></body></html>'

def resp(html):
    r = make_response(html)
    r.headers['Content-Type'] = 'text/html; charset=utf-8'
    return r


# ---------------------------------------------------------------------------
# Web routes
# ---------------------------------------------------------------------------

@app.route('/')
def index():
    recs = _recomendaciones[-6:]
    nc = len(session.get('carrito', []))
    recs_html = ''
    if recs:
        recs_html = '<h2 style="margin-top:20px">⭐ Recomendaciones para ti</h2><div class="grid">'
        for r in recs:
            recs_html += f'<div class="pc"><span class="nm">{r["nombre"]}</span><span class="ct">{r["categoria"]}</span><span class="pr">{r["precio"]:.2f} €</span><a href="/buscar" class="bg" style="margin-top:6px;text-align:center;font-size:13px">Ver productos</a></div>'
        recs_html += '</div>'
    body = f'''<h1>Bienvenido a ECSDI Shop 2026</h1>
<div class="card">
  <p style="margin-bottom:14px">Sistema multiagente de comercio electrónico ECSDI 2026.</p>
  <a href="/buscar" class="bg" style="margin-right:8px">🔍 Buscar productos</a>
  <a href="/carrito" style="background:#1a3c5e;color:#fff;padding:8px 16px;border-radius:5px;text-decoration:none">🛒 Ver carrito</a>
</div>{recs_html}'''
    return resp(html_page(body, nc))


@app.route('/buscar', methods=['GET', 'POST'])
def buscar():
    productos, error = [], None
    filtros = {'nombre': '', 'categoria': '', 'precio_max': '', 'val_min': ''}
    nc = len(session.get('carrito', []))
    if request.method == 'POST':
        filtros = {k: request.form.get(k, '') for k in filtros}
        productos, error = buscar_productos(**filtros)

    err_html = f'<div class="err">⚠️ {error}</div>' if error else ''
    prods_html = ''
    if productos:
        prods_html = f'<p style="color:#555;font-size:13px;margin-bottom:10px">{len(productos)} resultado(s)</p><div class="grid">'
        for p in productos:
            stars = '★' * int(p['valoracion']) + '☆' * (5 - int(p['valoracion']))
            prods_html += f'''<div class="pc">
  <span class="nm">{p["nombre"]}</span>
  <span class="ct">{p["categoria"]}</span>
  <span class="pr">{p["precio"]:.2f} €</span>
  <span class="vl">{stars} ({p["valoracion"]:.1f})</span>
  <span style="font-size:11px;color:#aaa">Vendedor: {p["vendedor"]}</span>
  <form class="il" method="post" action="/carrito/anadir">
    <input type="hidden" name="id" value="{p["id"]}">
    <input type="hidden" name="nombre" value="{p["nombre"]}">
    <input type="hidden" name="precio" value="{p["precio"]}">
    <input type="hidden" name="peso" value="{p["peso"]}">
    <button class="bg" style="margin-top:8px;width:100%">+ Añadir</button>
  </form>
</div>'''
        prods_html += '</div>'
    elif request.method == 'POST' and not error:
        prods_html = '<p class="empty">No se encontraron productos con esos filtros.</p>'

    body = f'''<h1>🔍 Buscar productos</h1>
<div class="card">
<form method="post" style="display:grid;grid-template-columns:1fr 1fr 1fr 1fr;gap:10px;align-items:end">
  <div class="fg" style="margin:0"><label>Nombre</label><input type="text" name="nombre" value="{filtros["nombre"]}" placeholder="ej: silla"></div>
  <div class="fg" style="margin:0"><label>Categoría</label><input type="text" name="categoria" value="{filtros["categoria"]}" placeholder="ej: electronica"></div>
  <div class="fg" style="margin:0"><label>Precio máx (€)</label><input type="number" name="precio_max" value="{filtros["precio_max"]}" placeholder="ej: 100" min="0" step="0.01"></div>
  <div class="fg" style="margin:0"><label>Valoración mín</label><input type="number" name="val_min" value="{filtros["val_min"]}" placeholder="1-5" min="0" max="5" step="0.1"></div>
  <input type="submit" value="Buscar" style="grid-column:span 4">
</form>
</div>
{err_html}{prods_html}'''
    return resp(html_page(body, nc))


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
    nc = len(carrito)
    if carrito:
        filas = ''.join(f'''<tr>
  <td>{i["nombre"]}</td><td>{i["precio"]:.2f} €</td><td>{i["cantidad"]}</td>
  <td><strong>{i["precio"]*i["cantidad"]:.2f} €</strong></td>
  <td><a href="/carrito/eliminar/{i["id"]}"><button class="br" style="padding:3px 10px;font-size:12px">✕</button></a></td>
</tr>''' for i in carrito)
        body = f'''<h1>🛒 Carrito</h1><div class="card">
<table><thead><tr><th>Producto</th><th>Precio</th><th>Qty</th><th>Subtotal</th><th></th></tr></thead>
<tbody>{filas}</tbody></table>
<div style="text-align:right;margin-top:14px"><strong style="font-size:17px">Total: {total:.2f} €</strong></div>
<div style="margin-top:14px;display:flex;gap:10px">
  <a href="/pedido" class="bg">✅ Tramitar pedido</a>
  <a href="/carrito/vaciar" class="br">🗑 Vaciar</a>
  <a href="/buscar" class="nb">← Seguir comprando</a>
</div></div>'''
    else:
        body = '<h1>🛒 Carrito</h1><div class="card"><p class="empty">El carrito está vacío. <a href="/buscar">Buscar productos</a></p></div>'
    return resp(html_page(body, nc))


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
    nc = len(carrito)
    if not carrito:
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
    err_html = f'<div class="err">{error}</div>' if error else ''
    body = f'''<h1>📦 Tramitar pedido</h1>
{err_html}
<div class="card">
<form method="post">
  <div class="fg"><label>Nombre comprador *</label><input type="text" name="comprador" required></div>
  <div class="fg"><label>Dirección de entrega *</label><input type="text" name="direccion" required></div>
  <div class="fg"><label>Prioridad</label>
    <select name="prioridad">
      <option value="normal">Normal (2-4 días)</option>
      <option value="urgente">Urgente (1-2 días)</option>
      <option value="economica">Económica (4-6 días)</option>
    </select></div>
  <div class="fg"><label>Método de pago</label>
    <select name="metodo_pago">
      <option value="tarjeta">Tarjeta</option>
      <option value="paypal">PayPal</option>
      <option value="transferencia">Transferencia</option>
    </select></div>
  <div style="background:#f8f8f8;padding:12px;border-radius:6px;margin-bottom:14px">
    <strong>Resumen:</strong> {len(carrito)} producto(s) — Total: <strong>{total:.2f} €</strong>
  </div>
  <input type="submit" value="Confirmar pedido" class="bg">
</form></div>'''
    return resp(html_page(body, nc))


@app.route('/pedido/confirmado')
def pedido_confirmado():
    datos = session.get('ultimo_pedido')
    if not datos:
        return redirect(url_for('index'))
    factura = datos['factura']
    envios  = _envios_notificados.get(factura.get('id', ''), [])
    env_html = ''
    if envios:
        env_html = '<h2 style="margin-top:16px">🚚 Información de envío</h2>'
        for e in envios:
            prods = ', '.join(e.get('productos', []))
            env_html += f'<div class="envtag">📦 <b>{e["centro"]}</b> → <b>{e["transportista"]}</b> — entrega el <b>{e["fecha"]}</b> ({prods})</div><br>'
    else:
        env_html = '<p style="color:#888;font-size:13px;margin-top:10px">Los detalles de envío se notificarán cuando el sistema logístico los procese.</p>'
    body = f'''<h1>✅ Pedido confirmado</h1>
<div class="card">
  <div class="ok">¡Tu pedido ha sido procesado correctamente!</div>
  <p><strong>Factura:</strong> <span class="badge">{factura.get("id","")}</span></p>
  <p style="margin-top:6px"><strong>Comprador:</strong> {datos["comprador"]}</p>
  <p style="margin-top:6px"><strong>Total:</strong> {factura.get("total",0):.2f} €</p>
  <p style="margin-top:6px"><strong>Fecha:</strong> {factura.get("fecha","")[:19]}</p>
  {env_html}
  <div style="margin-top:16px;display:flex;gap:10px">
    <a href="/historial" class="nb">📋 Ver historial</a>
    <a href="/buscar" class="bg">🛍 Seguir comprando</a>
  </div>
</div>'''
    return resp(html_page(body, 0))


@app.route('/historial')
def historial():
    nc = len(session.get('carrito', []))
    facturas = load_json(FACTURAS_PATH)
    pedidos_map = {p['id']: p for p in load_json(PEDIDOS_PATH)}
    for f in facturas:
        fid = f.get('id', '')
        f['_envios'] = _envios_notificados.get(fid, pedidos_map.get(fid, {}).get('envios', []))
    facturas = list(reversed(facturas))
    if facturas:
        rows = ''
        for f in facturas:
            prods = ', '.join(p.get('nombre', '') for p in f.get('productos', []))
            envs  = ' '.join(f'<span class="envtag">{e.get("transportista","")} ({e.get("fecha","")[:10]})</span>' for e in f['_envios']) or '<span style="color:#aaa">Pendiente</span>'
            rows += f'''<tr>
  <td><span class="badge">{f.get("id","")}</span></td>
  <td>{f.get("comprador","")}</td>
  <td style="font-size:12px">{prods[:55]}{"..." if len(prods)>55 else ""}</td>
  <td><strong>{f.get("total",0):.2f} €</strong></td>
  <td style="font-size:12px">{f.get("fecha","")[:10]}</td>
  <td>{envs}</td>
</tr>'''
        body = f'''<h1>📋 Historial de pedidos</h1>
<div class="card"><table><thead><tr><th>Factura</th><th>Comprador</th><th>Productos</th><th>Total</th><th>Fecha</th><th>Envío</th></tr></thead>
<tbody>{rows}</tbody></table></div>'''
    else:
        body = '<h1>📋 Historial</h1><div class="card"><p class="empty">No hay pedidos todavía. <a href="/buscar">Haz tu primera compra</a></p></div>'
    return resp(html_page(body, nc))


@app.route('/devolucion', methods=['GET', 'POST'])
def devolucion():
    nc = len(session.get('carrito', []))
    facturas = load_json(FACTURAS_PATH)
    resultado = error = None
    if request.method == 'POST':
        comprador       = request.form.get('comprador', '').strip()
        factura_id      = request.form.get('factura_id', '').strip()
        razon           = request.form.get('razon', '').strip()
        fecha_recepcion = request.form.get('fecha_recepcion', '').strip()
        if not all([comprador, factura_id, razon, fecha_recepcion]):
            error = 'Rellena todos los campos'
        else:
            resultado, error = solicitar_devolucion(comprador, factura_id, razon, fecha_recepcion)
    opts = ''.join(f'<option value="{f["id"]}">{f["id"]} — {f.get("comprador","")}</option>' for f in facturas) or '<option>— Sin pedidos —</option>'
    res_html = ''
    if resultado:
        estado = '✅ Aceptada' if resultado['aceptada'] else '❌ Rechazada'
        res_html = f'<div class="{"ok" if resultado["aceptada"] else "err"}">{estado} — {resultado["motivo"]} {("| Empresa: "+resultado["empresa"]) if resultado["empresa"] else ""}</div>'
    err_html = f'<div class="err">{error}</div>' if error else ''
    body = f'''<h1>↩️ Solicitar devolución</h1>
{res_html}{err_html}
<div class="card"><form method="post">
  <div class="fg"><label>Nombre comprador</label><input type="text" name="comprador" required></div>
  <div class="fg"><label>Factura</label><select name="factura_id">{opts}</select></div>
  <div class="fg"><label>Razón de devolución</label><input type="text" name="razon" required></div>
  <div class="fg"><label>Fecha de recepción del producto</label><input type="date" name="fecha_recepcion" required></div>
  <input type="submit" value="Solicitar devolución" class="bg">
</form></div>'''
    return resp(html_page(body, nc))


@app.route('/valorar', methods=['GET', 'POST'])
def valorar():
    nc = len(session.get('carrito', []))
    facturas = load_json(FACTURAS_PATH)
    prods_comprados = {}
    for f in facturas:
        for p in f.get('productos', []):
            prods_comprados[p['id']] = p
    resultado = error = None
    if request.method == 'POST':
        comprador  = request.form.get('comprador', '').strip()
        prod_id    = request.form.get('producto_id', '').strip()
        puntuacion = request.form.get('puntuacion', 3)
        comentario = request.form.get('comentario', '').strip()
        ok, err = enviar_valoracion(comprador, prod_id, puntuacion, comentario)
        resultado = 'Valoración enviada correctamente' if ok else None
        error = err if not ok else None
    opts = ''.join(f'<option value="{p["id"]}">{p["nombre"]} ({p["id"]})</option>' for p in prods_comprados.values()) or '<option value="">— Compra primero algún producto —</option>'
    res_html = f'<div class="ok">✅ {resultado}</div>' if resultado else ''
    err_html = f'<div class="err">❌ {error}</div>' if error else ''
    body = f'''<h1>⭐ Valorar productos</h1>
{res_html}{err_html}
<div class="card"><form method="post">
  <div class="fg"><label>Tu nombre</label><input type="text" name="comprador" required></div>
  <div class="fg"><label>Producto</label><select name="producto_id">{opts}</select></div>
  <div class="fg"><label>Puntuación (1-5)</label>
    <select name="puntuacion">
      <option value="5">★★★★★ Excelente</option>
      <option value="4">★★★★ Muy bueno</option>
      <option value="3" selected>★★★ Normal</option>
      <option value="2">★★ Regular</option>
      <option value="1">★ Malo</option>
    </select></div>
  <div class="fg"><label>Comentario (opcional)</label><textarea name="comentario" rows="3"></textarea></div>
  <input type="submit" value="Enviar valoración" class="bg">
</form></div>'''
    return resp(html_page(body, nc))


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
        logger.info('[Usuario] SolicitudFeedback recibida')
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
