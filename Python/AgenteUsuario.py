import argparse
import json
import logging
import os
import socket
import sys
import time
import uuid
from datetime import datetime
from multiprocessing import Process, Queue

from flask import Flask, request, redirect, url_for, session
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

app = Flask(__name__, template_folder='templates')
app.secret_key = 'ecsdi2026-secret'
if not args.verbose:
    logging.getLogger('werkzeug').setLevel(logging.ERROR)

agn = Namespace('http://www.agentes.org#')
mss_cnt = 0

DATA_DIR      = os.path.join(os.path.dirname(__file__), 'data')
FACTURAS_PATH = os.path.join(DATA_DIR, 'facturas.json')
PEDIDOS_PATH  = os.path.join(DATA_DIR, 'pedidos.json')

# Estado en memoria: notificaciones de envio recibidas del GestorPedidos
# clave: pedido_id -> lista de sub_envios
_envios_notificados = {}
# Recomendaciones recibidas del AgenteExperiencia
_recomendaciones = []

comprador_address  = None
gestor_address     = None
experiencia_address = None

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
# Registro y busqueda en DS
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


def get_agent_address(agent_type):
    global mss_cnt
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
                return str(o)
    except Exception as e:
        logger.warning(f'[Usuario] Error buscando agente {agent_type}: {e}')
        mss_cnt += 1
    return None


# ---------------------------------------------------------------------------
# Helpers de datos locales
# ---------------------------------------------------------------------------

def cargar_json(path):
    if os.path.exists(path):
        try:
            with open(path) as f:
                return json.load(f)
        except Exception:
            pass
    return []


def cargar_json_dict(path):
    if os.path.exists(path):
        try:
            with open(path) as f:
                return json.load(f)
        except Exception:
            pass
    return {}


# ---------------------------------------------------------------------------
# Acciones hacia otros agentes
# ---------------------------------------------------------------------------

def buscar_productos_en_comprador(nombre='', categoria='', precio_max=None, val_min=None):
    global mss_cnt, comprador_address
    if comprador_address is None:
        comprador_address = get_agent_address(ECSNS['Ag.Comprador'])
    if comprador_address is None:
        logger.warning('[Usuario] AgenteComprador no encontrado en DS')
        return []

    gmess = Graph()
    gmess.bind('ecsns', ECSNS)
    busq = ECSNS['busqueda-' + str(mss_cnt)]
    gmess.add((busq, RDF.type, ECSNS.Busqueda))
    if categoria:
        gmess.add((busq, ECSNS.categoria, Literal(categoria)))
    if precio_max:
        try:
            gmess.add((busq, ECSNS.precioMaximo, Literal(float(precio_max))))
        except ValueError:
            pass
    if val_min:
        try:
            gmess.add((busq, ECSNS.valoracionMinima, Literal(float(val_min))))
        except ValueError:
            pass
    try:
        gr_resp = send_message(
            build_message(gmess, perf=ACL.request, sender=UsuarioAgent.uri,
                          receiver=agn.AgenteComprador, content=busq, msgcnt=mss_cnt),
            comprador_address
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
            if nombre and nombre.lower() not in prod['nombre'].lower():
                continue
            productos.append(prod)
        return productos
    except Exception as e:
        logger.warning(f'[Usuario] Error buscando productos: {e}')
        mss_cnt += 1
        return []


def enviar_pedido_a_gestor(comprador, direccion, prioridad, metodo_pago, carrito):
    global mss_cnt, gestor_address
    if gestor_address is None:
        gestor_address = get_agent_address(ECSNS['Ag.GestorDePedidos'])
    if gestor_address is None:
        logger.warning('[Usuario] AgenteGestorPedidos no encontrado en DS')
        return None

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
            gestor_address
        )
        mss_cnt += 1
        for s in gr_resp.subjects(RDF.type, ECSNS.Factura):
            return {
                'id':    str(gr_resp.value(s, ECSNS.idFactura) or ''),
                'total': float(gr_resp.value(s, ECSNS.total)   or 0),
                'fecha': str(gr_resp.value(s, ECSNS.fecha)     or ''),
            }
    except Exception as e:
        logger.warning(f'[Usuario] Error enviando pedido: {e}')
        mss_cnt += 1
    return None


def enviar_valoracion(comprador, producto_id, puntuacion, comentario=''):
    global mss_cnt, experiencia_address
    if experiencia_address is None:
        experiencia_address = get_agent_address(ECSNS['Ag.Experiencia'])
    if experiencia_address is None:
        logger.warning('[Usuario] AgenteExperiencia no encontrado en DS')
        return False
    gmess = Graph()
    gmess.bind('ecsns', ECSNS)
    val = ECSNS['valoracion-ui-' + str(mss_cnt)]
    gmess.add((val, RDF.type,          ECSNS.Valoracion))
    gmess.add((val, ECSNS.comprador,   Literal(comprador)))
    gmess.add((val, ECSNS.idProducto,  Literal(producto_id)))
    gmess.add((val, ECSNS.puntuacion,  Literal(int(puntuacion))))
    gmess.add((val, ECSNS.comentario,  Literal(comentario)))
    gmess.add((val, ECSNS.fecha,       Literal(datetime.now().isoformat())))
    try:
        send_message(
            build_message(gmess, perf=ACL.inform, sender=UsuarioAgent.uri,
                          receiver=agn.AgenteExperiencia, content=val, msgcnt=mss_cnt),
            experiencia_address
        )
        mss_cnt += 1
        logger.info(f'[Usuario] Valoracion enviada: producto {producto_id}, puntuacion {puntuacion}')
        return True
    except Exception as e:
        logger.warning(f'[Usuario] Error enviando valoracion: {e}')
        mss_cnt += 1
        return False


# ---------------------------------------------------------------------------
# Rutas web
# ---------------------------------------------------------------------------

@app.route('/')
def index():
    recomendaciones = _recomendaciones[-5:] if _recomendaciones else []
    return render('index.html', recomendaciones=recomendaciones)


@app.route('/buscar', methods=['GET', 'POST'])
def buscar():
    productos = []
    filtros = {}
    if request.method == 'POST':
        filtros = {
            'nombre':     request.form.get('nombre', '').strip(),
            'categoria':  request.form.get('categoria', '').strip(),
            'precio_max': request.form.get('precio_max', '').strip(),
            'val_min':    request.form.get('val_min', '').strip(),
        }
        productos = buscar_productos_en_comprador(**filtros)
    return render('buscar.html', productos=productos, filtros=filtros)


@app.route('/carrito/añadir', methods=['POST'])
def carrito_anadir():
    if 'carrito' not in session:
        session['carrito'] = []
    carrito = session['carrito']
    prod = {
        'id':       request.form.get('id', ''),
        'nombre':   request.form.get('nombre', ''),
        'precio':   float(request.form.get('precio', 0)),
        'peso':     float(request.form.get('peso', 0)),
        'cantidad': 1,
    }
    # Si ya esta en el carrito, incrementar cantidad
    for item in carrito:
        if item['id'] == prod['id']:
            item['cantidad'] += 1
            session['carrito'] = carrito
            return redirect(url_for('carrito_ver'))
    carrito.append(prod)
    session['carrito'] = carrito
    return redirect(url_for('carrito_ver'))


@app.route('/carrito')
def carrito_ver():
    carrito = session.get('carrito', [])
    total = sum(i['precio'] * i['cantidad'] for i in carrito)
    return render('carrito.html', carrito=carrito, total=round(total, 2))


@app.route('/carrito/eliminar/<prod_id>')
def carrito_eliminar(prod_id):
    carrito = session.get('carrito', [])
    session['carrito'] = [i for i in carrito if i['id'] != prod_id]
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
    if request.method == 'POST':
        comprador   = request.form.get('comprador', '').strip()
        direccion   = request.form.get('direccion', '').strip()
        prioridad   = request.form.get('prioridad', 'normal')
        metodo_pago = request.form.get('metodo_pago', 'tarjeta')
        if not comprador or not direccion:
            error = 'Por favor rellena todos los campos obligatorios.'
        else:
            factura = enviar_pedido_a_gestor(comprador, direccion, prioridad, metodo_pago, carrito)
            if factura:
                session['carrito'] = []
                session['ultimo_pedido'] = {
                    'factura': factura,
                    'comprador': comprador,
                    'carrito': carrito,
                }
                return redirect(url_for('pedido_confirmado'))
            else:
                error = 'Error al procesar el pedido. Comprueba que el sistema está activo.'
    total = sum(i['precio'] * i['cantidad'] for i in carrito)
    return render('pedido.html', carrito=carrito, total=round(total, 2), error=error)


@app.route('/pedido/confirmado')
def pedido_confirmado():
    datos = session.get('ultimo_pedido')
    if not datos:
        return redirect(url_for('index'))
    factura   = datos['factura']
    comprador = datos['comprador']
    # Buscar sub_envios si ya los tenemos notificados
    envios = _envios_notificados.get(factura.get('id', ''), [])
    return render('pedido_confirmado.html', factura=factura, comprador=comprador, envios=envios)


@app.route('/historial')
def historial():
    facturas = cargar_json(FACTURAS_PATH)
    pedidos  = cargar_json(PEDIDOS_PATH)
    # Enriquecer facturas con info de envio si esta disponible
    pedidos_map = {p['id']: p for p in pedidos}
    for f in facturas:
        fid = f.get('id', '')
        f['envios'] = _envios_notificados.get(fid, pedidos_map.get(fid, {}).get('envios', []))
    return render('historial.html', facturas=facturas)


@app.route('/valorar', methods=['GET', 'POST'])
def valorar():
    facturas = cargar_json(FACTURAS_PATH)
    # Extraer todos los productos comprados (sin duplicados)
    productos_comprados = {}
    for f in facturas:
        for p in f.get('productos', []):
            productos_comprados[p['id']] = p
    mensaje = None
    if request.method == 'POST':
        comprador  = request.form.get('comprador', '').strip()
        prod_id    = request.form.get('producto_id', '').strip()
        puntuacion = request.form.get('puntuacion', 3)
        comentario = request.form.get('comentario', '').strip()
        ok = enviar_valoracion(comprador, prod_id, puntuacion, comentario)
        mensaje = '✅ Valoración enviada correctamente.' if ok else '❌ Error al enviar la valoración.'
    return render('valorar.html', productos=list(productos_comprados.values()), mensaje=mensaje)


# ---------------------------------------------------------------------------
# Endpoint /comm — recibe mensajes de otros agentes
# ---------------------------------------------------------------------------

@app.route('/comm', methods=['GET', 'POST'])
def comunicacion():
    global mss_cnt
    message = request.args.get('content') or request.form.get('content')
    gm = Graph()
    gm.parse(data=message, format='xml')
    msgdic = get_message_properties(gm)

    if msgdic is None:
        gr = build_message(Graph(), ACL['not-understood'],
                           sender=UsuarioAgent.uri, msgcnt=mss_cnt)
        mss_cnt += 1
        return gr.serialize(format='xml')

    perf    = msgdic.get('performative')
    content = msgdic.get('content')
    accion  = gm.value(subject=content, predicate=RDF.type) if content else None

    if perf == ACL.inform and accion == ECSNS.NotificacionEnvios:
        # GestorPedidos nos informa del resultado de envio
        pedido_id = str(gm.value(content, ECSNS.idPedido) or '')
        sub_envios = []
        for envio_node in gm.objects(content, ECSNS.tieneSubEnvio):
            sub_envios.append({
                'id':           str(gm.value(envio_node, ECSNS.idEnvio)            or ''),
                'centro':       str(gm.value(envio_node, ECSNS.tieneCentro)        or ''),
                'transportista':str(gm.value(envio_node, ECSNS.tieneTransportista) or ''),
                'fecha':        str(gm.value(envio_node, ECSNS.tieneFechaEntrega)  or ''),
                'productos':    [str(o) for o in gm.objects(envio_node, ECSNS.tieneProductoId)],
            })
        _envios_notificados[pedido_id] = sub_envios
        logger.info(f'[Usuario] Notificacion de {len(sub_envios)} envio/s recibida para pedido {pedido_id}')
        gr = build_message(Graph(), ACL.confirm, sender=UsuarioAgent.uri,
                           receiver=msgdic['sender'], msgcnt=mss_cnt)

    elif perf == ACL.inform and accion == ECSNS.RecomendacionesProactivas:
        # AgenteExperiencia nos envia recomendaciones periodicas
        for prod_node in gm.objects(content, ECSNS.tieneProducto):
            rec = {
                'id':       str(gm.value(prod_node, ECSNS.idProducto)  or ''),
                'nombre':   str(gm.value(prod_node, ECSNS.nombre)       or ''),
                'precio':   float(gm.value(prod_node, ECSNS.precio)     or 0),
                'categoria':str(gm.value(prod_node, ECSNS.categoria)    or ''),
            }
            if rec['id'] and rec not in _recomendaciones:
                _recomendaciones.append(rec)
        logger.info(f'[Usuario] Recomendaciones proactivas recibidas: {len(_recomendaciones)} total')
        gr = build_message(Graph(), ACL.confirm, sender=UsuarioAgent.uri,
                           receiver=msgdic['sender'], msgcnt=mss_cnt)

    elif perf == ACL.request and accion == ECSNS.SolicitudFeedback:
        # AgenteExperiencia pide feedback — lo logueamos (la UI ofrece /valorar)
        comprador  = str(gm.value(content, ECSNS.comprador)  or '')
        prod_id    = str(gm.value(content, ECSNS.idProducto) or '')
        logger.info(f'[Usuario] Solicitud de feedback para {comprador} / producto {prod_id}')
        gr = build_message(Graph(), ACL.confirm, sender=UsuarioAgent.uri,
                           receiver=msgdic['sender'], msgcnt=mss_cnt)

    else:
        gr = build_message(Graph(), ACL['not-understood'],
                           sender=UsuarioAgent.uri, msgcnt=mss_cnt)

    mss_cnt += 1
    return gr.serialize(format='xml')


@app.route('/Stop')
def stop():
    cola1.put(0)
    shutdown_server()
    return 'Parando AgenteUsuario'


# ---------------------------------------------------------------------------
# Render helper (templates inline para no depender de ficheros externos)
# ---------------------------------------------------------------------------

BASE_HTML = '''
<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ECSDI 2026 — Tienda</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:'Segoe UI',sans-serif;background:#f4f6f8;color:#333;font-size:15px}}
nav{{background:#1a3c5e;color:#fff;padding:12px 24px;display:flex;gap:20px;align-items:center}}
nav a{{color:#cde;text-decoration:none;font-size:14px;padding:4px 10px;border-radius:4px}}
nav a:hover{{background:#2a5480}}
nav .brand{{font-weight:700;font-size:17px;color:#fff;margin-right:auto}}
.container{{max-width:1000px;margin:30px auto;padding:0 16px}}
h1{{font-size:22px;margin-bottom:18px;color:#1a3c5e}}
h2{{font-size:17px;margin-bottom:12px;color:#1a3c5e}}
.card{{background:#fff;border-radius:8px;padding:20px;margin-bottom:16px;box-shadow:0 1px 4px rgba(0,0,0,.08)}}
.grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(210px,1fr));gap:14px}}
.prod-card{{background:#fff;border-radius:8px;padding:16px;box-shadow:0 1px 4px rgba(0,0,0,.08);display:flex;flex-direction:column;gap:6px}}
.prod-card .nombre{{font-weight:600;font-size:15px}}
.prod-card .precio{{color:#1a7a3c;font-weight:700;font-size:16px}}
.prod-card .cat{{font-size:12px;color:#888;background:#f0f0f0;padding:2px 8px;border-radius:10px;display:inline-block}}
.prod-card .val{{font-size:12px;color:#e6a817}}
button,input[type=submit]{{background:#1a3c5e;color:#fff;border:none;padding:8px 18px;border-radius:5px;cursor:pointer;font-size:14px}}
button:hover,input[type=submit]:hover{{background:#2a5480}}
.btn-danger{{background:#c0392b}}
.btn-danger:hover{{background:#a93226}}
.btn-green{{background:#1a7a3c}}
.btn-green:hover{{background:#155f2f}}
form.inline{{display:inline}}
input[type=text],input[type=number],select,textarea{{padding:7px 10px;border:1px solid #ccc;border-radius:5px;font-size:14px;width:100%}}
label{{font-size:13px;color:#555;margin-bottom:3px;display:block}}
.form-group{{margin-bottom:12px}}
table{{width:100%;border-collapse:collapse}}
th,td{{padding:10px 12px;text-align:left;border-bottom:1px solid #eee}}
th{{background:#f0f4f8;font-size:13px;color:#555}}
.badge{{background:#e8f4fd;color:#1a3c5e;padding:2px 9px;border-radius:10px;font-size:12px;font-weight:600}}
.alert{{padding:12px 16px;border-radius:6px;margin-bottom:14px;font-size:14px}}
.alert-ok{{background:#d4edda;color:#155724;border:1px solid #c3e6cb}}
.alert-err{{background:#f8d7da;color:#721c24;border:1px solid #f5c6cb}}
.tag-envio{{background:#e8f8ef;color:#1a7a3c;padding:4px 10px;border-radius:6px;font-size:13px;display:inline-block;margin:2px 0}}
.empty{{text-align:center;color:#aaa;padding:40px 0;font-size:15px}}
</style>
</head>
<body>
<nav>
  <span class="brand">🛒 ECSDI Shop 2026</span>
  <a href="/">Inicio</a>
  <a href="/buscar">Buscar</a>
  <a href="/carrito">Carrito</a>
  <a href="/historial">Historial</a>
  <a href="/valorar">Valorar</a>
</nav>
<div class="container">
{body}
</div>
</body></html>
'''


def render(template, **ctx):
    """Renderiza templates inline (sin Jinja2) para autonomia."""
    from flask import make_response
    if template == 'index.html':
        recs = ctx.get('recomendaciones', [])
        recs_html = ''
        if recs:
            recs_html = '<h2>⭐ Recomendaciones para ti</h2><div class="grid">'
            for r in recs:
                recs_html += f'''<div class="prod-card">
  <span class="nombre">{r["nombre"]}</span>
  <span class="precio">{r["precio"]:.2f} €</span>
  <span class="cat">{r["categoria"]}</span>
  <a href="/buscar"><button style="margin-top:6px">Ver productos</button></a>
</div>'''
            recs_html += '</div>'
        body = f'''<h1>Bienvenido a ECSDI Shop 2026</h1>
<div class="card">
  <p style="margin-bottom:14px">Sistema multiagente de comercio electrónico. Usa el menú para buscar productos y realizar pedidos.</p>
  <a href="/buscar"><button class="btn-green">🔍 Buscar productos</button></a>
  &nbsp;
  <a href="/carrito"><button>🛒 Ver carrito</button></a>
</div>
{recs_html}
'''
    elif template == 'buscar.html':
        prods = ctx.get('productos', [])
        filtros = ctx.get('filtros', {})
        prods_html = ''
        if prods:
            prods_html = f'<p style="color:#555;font-size:13px;margin-bottom:10px">{len(prods)} resultado(s)</p><div class="grid">'
            for p in prods:
                prods_html += f'''<div class="prod-card">
  <span class="nombre">{p["nombre"]}</span>
  <span class="cat">{p["categoria"]}</span>
  <span class="precio">{p["precio"]:.2f} €</span>
  <span class="val">{"★" * int(p["valoracion"])} ({p["valoracion"]:.1f})</span>
  <span style="font-size:12px;color:#aaa">Vendedor: {p["vendedor"]}</span>
  <form class="inline" method="post" action="/carrito/añadir">
    <input type="hidden" name="id" value="{p["id"]}">
    <input type="hidden" name="nombre" value="{p["nombre"]}">
    <input type="hidden" name="precio" value="{p["precio"]}">
    <input type="hidden" name="peso" value="{p["peso"]}">
    <button class="btn-green" style="margin-top:8px">+ Añadir</button>
  </form>
</div>'''
            prods_html += '</div>'
        elif filtros:
            prods_html = '<p class="empty">No se encontraron productos con esos filtros.</p>'
        body = f'''<h1>🔍 Buscar productos</h1>
<div class="card">
<form method="post" action="/buscar" style="display:grid;grid-template-columns:1fr 1fr 1fr 1fr;gap:12px;align-items:end">
  <div class="form-group" style="margin:0">
    <label>Nombre</label>
    <input type="text" name="nombre" value="{filtros.get("nombre","")}" placeholder="ej: silla">
  </div>
  <div class="form-group" style="margin:0">
    <label>Categoría</label>
    <input type="text" name="categoria" value="{filtros.get("categoria","")}" placeholder="ej: electronica">
  </div>
  <div class="form-group" style="margin:0">
    <label>Precio máx (€)</label>
    <input type="number" name="precio_max" value="{filtros.get("precio_max","")}" placeholder="ej: 100" min="0" step="0.01">
  </div>
  <div class="form-group" style="margin:0">
    <label>Valoración mín</label>
    <input type="number" name="val_min" value="{filtros.get("val_min","")}" placeholder="1-5" min="0" max="5" step="0.1">
  </div>
  <input type="submit" value="Buscar" style="grid-column:span 4">
</form>
</div>
{prods_html}
'''
    elif template == 'carrito.html':
        carrito = ctx.get('carrito', [])
        total   = ctx.get('total', 0)
        if carrito:
            filas = ''.join(f'''<tr>
  <td>{i["nombre"]}</td>
  <td>{i["precio"]:.2f} €</td>
  <td>{i["cantidad"]}</td>
  <td>{i["precio"]*i["cantidad"]:.2f} €</td>
  <td><a href="/carrito/eliminar/{i["id"]}"><button class="btn-danger" style="padding:4px 10px;font-size:12px">✕</button></a></td>
</tr>''' for i in carrito)
            body = f'''<h1>🛒 Carrito</h1>
<div class="card">
<table><thead><tr><th>Producto</th><th>Precio</th><th>Qty</th><th>Subtotal</th><th></th></tr></thead>
<tbody>{filas}</tbody></table>
<div style="text-align:right;margin-top:14px">
  <strong style="font-size:17px">Total: {total:.2f} €</strong>
</div>
<div style="margin-top:16px;display:flex;gap:10px">
  <a href="/pedido"><button class="btn-green">✅ Tramitar pedido</button></a>
  <a href="/carrito/vaciar"><button class="btn-danger">🗑 Vaciar</button></a>
  <a href="/buscar"><button>← Seguir comprando</button></a>
</div>
</div>'''
        else:
            body = '<h1>🛒 Carrito</h1><div class="card"><p class="empty">El carrito está vacío. <a href="/buscar">Buscar productos</a></p></div>'
    elif template == 'pedido.html':
        carrito = ctx.get('carrito', [])
        total   = ctx.get('total', 0)
        error   = ctx.get('error')
        err_html = f'<div class="alert alert-err">{error}</div>' if error else ''
        body = f'''<h1>📦 Tramitar pedido</h1>
{err_html}
<div class="card">
<form method="post" action="/pedido">
  <div class="form-group"><label>Nombre comprador *</label><input type="text" name="comprador" required></div>
  <div class="form-group"><label>Dirección de entrega *</label><input type="text" name="direccion" required></div>
  <div class="form-group"><label>Prioridad</label>
    <select name="prioridad">
      <option value="normal">Normal (2-4 días)</option>
      <option value="urgente">Urgente (1-2 días)</option>
      <option value="economica">Económica (4-6 días)</option>
    </select>
  </div>
  <div class="form-group"><label>Método de pago</label>
    <select name="metodo_pago">
      <option value="tarjeta">Tarjeta</option>
      <option value="paypal">PayPal</option>
      <option value="transferencia">Transferencia</option>
    </select>
  </div>
  <div style="background:#f8f8f8;padding:12px;border-radius:6px;margin-bottom:14px">
    <strong>Resumen:</strong> {len(carrito)} producto(s) — Total: <strong>{total:.2f} €</strong>
  </div>
  <input type="submit" value="Confirmar pedido" class="btn-green">
</form>
</div>'''
    elif template == 'pedido_confirmado.html':
        factura   = ctx.get('factura', {})
        comprador = ctx.get('comprador', '')
        envios    = ctx.get('envios', [])
        envios_html = ''
        if envios:
            envios_html = '<h2 style="margin-top:18px">🚚 Información de envío</h2>'
            for e in envios:
                prods = ', '.join(e.get('productos', []))
                envios_html += f'<div class="tag-envio">📦 <b>{e["centro"]}</b> → <b>{e["transportista"]}</b> — entrega el <b>{e["fecha"]}</b> ({prods})</div><br>'
        else:
            envios_html = '<p style="color:#888;font-size:13px;margin-top:10px">Los detalles de envío se notificarán cuando el sistema logístico los procese.</p>'
        body = f'''<h1>✅ Pedido confirmado</h1>
<div class="card">
  <div class="alert alert-ok">¡Tu pedido ha sido procesado correctamente!</div>
  <p><strong>Factura:</strong> <span class="badge">{factura.get("id","")}</span></p>
  <p><strong>Comprador:</strong> {comprador}</p>
  <p><strong>Total:</strong> {factura.get("total",0):.2f} €</p>
  <p><strong>Fecha:</strong> {factura.get("fecha","")[:19]}</p>
  {envios_html}
  <div style="margin-top:18px;display:flex;gap:10px">
    <a href="/historial"><button>📋 Ver historial</button></a>
    <a href="/buscar"><button class="btn-green">🛍 Seguir comprando</button></a>
  </div>
</div>'''
    elif template == 'historial.html':
        facturas = ctx.get('facturas', [])
        if facturas:
            rows = ''
            for f in reversed(facturas):
                prods = ', '.join(p.get('nombre','') for p in f.get('productos',[]))
                envios = f.get('envios', [])
                envios_str = ' '.join(f'<span class="tag-envio">{e.get("transportista","")} ({e.get("fecha","")})</span>' for e in envios) or '<span style="color:#aaa">Pendiente</span>'
                rows += f'''<tr>
  <td><span class="badge">{f.get("id","")}</span></td>
  <td>{f.get("comprador","")}</td>
  <td style="font-size:12px">{prods[:60]}{"..." if len(prods)>60 else ""}</td>
  <td><strong>{f.get("total",0):.2f} €</strong></td>
  <td style="font-size:12px">{f.get("fecha","")[:10]}</td>
  <td>{envios_str}</td>
</tr>'''
            body = f'''<h1>📋 Historial de pedidos</h1>
<div class="card">
<table><thead><tr><th>Factura</th><th>Comprador</th><th>Productos</th><th>Total</th><th>Fecha</th><th>Envío</th></tr></thead>
<tbody>{rows}</tbody></table>
</div>'''
        else:
            body = '<h1>📋 Historial de pedidos</h1><div class="card"><p class="empty">No hay pedidos todavía. <a href="/buscar">Haz tu primera compra</a></p></div>'
    elif template == 'valorar.html':
        productos = ctx.get('productos', [])
        mensaje   = ctx.get('mensaje')
        msg_html  = ''
        if mensaje:
            cls = 'alert-ok' if '✅' in mensaje else 'alert-err'
            msg_html = f'<div class="alert {cls}">{mensaje}</div>'
        opts = ''.join(f'<option value="{p["id"]}">{p["nombre"]} ({p["id"]})</option>' for p in productos)
        if not opts:
            opts = '<option value="">— No hay productos comprados aún —</option>'
        body = f'''<h1>⭐ Valorar productos</h1>
{msg_html}
<div class="card">
<form method="post" action="/valorar">
  <div class="form-group"><label>Tu nombre</label><input type="text" name="comprador" required></div>
  <div class="form-group"><label>Producto</label><select name="producto_id">{opts}</select></div>
  <div class="form-group"><label>Puntuación (1-5)</label>
    <select name="puntuacion">
      <option value="5">⭐⭐⭐⭐⭐ Excelente</option>
      <option value="4">⭐⭐⭐⭐ Muy bueno</option>
      <option value="3" selected>⭐⭐⭐ Normal</option>
      <option value="2">⭐⭐ Regular</option>
      <option value="1">⭐ Malo</option>
    </select>
  </div>
  <div class="form-group"><label>Comentario (opcional)</label><textarea name="comentario" rows="3"></textarea></div>
  <input type="submit" value="Enviar valoración" class="btn-green">
</form>
</div>'''
    else:
        body = '<h1>Página no encontrada</h1>'

    html = BASE_HTML.replace('{body}', body)
    resp = make_response(html)
    resp.headers['Content-Type'] = 'text/html; charset=utf-8'
    return resp


# ---------------------------------------------------------------------------
# Comportamiento del agente (registro en DS)
# ---------------------------------------------------------------------------

def agentbehavior1(cola):
    register_message()
    logger.info('[Usuario] Agente activo en http://%s:%d/' % (hostaddr, port))
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
