import argparse
import json
import socket
import sys
import os
import logging

from flask import Flask, request, render_template_string, redirect, url_for
from rdflib import Graph, Literal, Namespace
from rdflib.namespace import RDF

import requests as http_requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from AgentUtil.ACL import ACL
from AgentUtil.ACLMessages import build_message, get_message_properties, send_message
from AgentUtil.Agent import Agent
from AgentUtil.DSO import DSO
from AgentUtil.Logging import config_logger
from ontologia import ECSNS

parser = argparse.ArgumentParser()
parser.add_argument('--open', action='store_true', default=False)
parser.add_argument('--verbose', action='store_true', default=False)
parser.add_argument('--port', type=int, default=9009)
parser.add_argument('--dhost', default=None)
parser.add_argument('--dport', type=int, default=9000)
args = parser.parse_args()

logger = config_logger(level=1)
port = args.port
hostname = socket.gethostname()
hostaddr = hostname if not args.open else '0.0.0.0'
dhostname = args.dhost if args.dhost else socket.gethostname()
dport = args.dport
mss_cnt = 0

app = Flask(__name__)
if not args.verbose:
    logging.getLogger('werkzeug').setLevel(logging.ERROR)

agn = Namespace('http://www.agentes.org#')

DirectoryAgent = Agent(
    'DirectoryAgent',
    agn.Directory,
    'http://%s:%d/Register' % (dhostname, dport),
    'http://%s:%d/Stop' % (dhostname, dport),
)
UsuarioAgent = Agent(
    'AgenteUsuario',
    agn.AgenteUsuario,
    'http://%s:%d/comm' % (hostaddr, port),
    'http://%s:%d/Stop' % (hostaddr, port),
)


def get_agent_address(agent_type):
    global mss_cnt
    gmess = Graph()
    gmess.bind('dso', DSO)
    search_obj = agn['Search-' + str(mss_cnt)]
    gmess.add((search_obj, RDF.type, DSO.Search))
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
    return None


# ──────────────────────────────────────────
# HTML base compartido
# ──────────────────────────────────────────
BASE_HTML = '''
<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ECSDI Shop 2026</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
          background: #f5f5f5; color: #222; }}
  nav {{ background: #1a1a2e; color: white; padding: 1rem 2rem;
         display: flex; align-items: center; gap: 2rem; }}
  nav h1 {{ font-size: 1.3rem; font-weight: 700; }}
  nav a {{ color: #a8b2d8; text-decoration: none; font-size: 0.9rem; }}
  nav a:hover {{ color: white; }}
  .container {{ max-width: 960px; margin: 2rem auto; padding: 0 1rem; }}
  h2 {{ font-size: 1.4rem; margin-bottom: 1.5rem; color: #1a1a2e; }}
  .card {{ background: white; border-radius: 8px; padding: 1.5rem;
           box-shadow: 0 1px 4px rgba(0,0,0,.08); margin-bottom: 1rem; }}
  .grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(220px,1fr)); gap: 1rem; }}
  .prod-card {{ background: white; border-radius: 8px; padding: 1rem;
                box-shadow: 0 1px 4px rgba(0,0,0,.08); }}
  .prod-card h3 {{ font-size: 1rem; margin-bottom: .4rem; }}
  .prod-card .price {{ color: #e63946; font-weight: 700; font-size: 1.1rem; }}
  .prod-card .meta {{ font-size: 0.8rem; color: #666; margin-top: .3rem; }}
  .badge {{ display: inline-block; background: #e8f4fd; color: #1a6fa8;
            font-size: 0.7rem; padding: 2px 8px; border-radius: 99px; margin-top: .4rem; }}
  .badge.ext {{ background: #fef3e2; color: #c07000; }}
  form label {{ display: block; font-size: 0.85rem; color: #555; margin-bottom: .2rem; margin-top: .8rem; }}
  form input, form select, form textarea {{
    width: 100%; padding: .5rem .75rem; border: 1px solid #ddd;
    border-radius: 6px; font-size: 0.9rem; }}
  form textarea {{ min-height: 80px; resize: vertical; }}
  .btn {{ display: inline-block; padding: .55rem 1.4rem; border-radius: 6px;
          border: none; cursor: pointer; font-size: 0.9rem; font-weight: 600; }}
  .btn-primary {{ background: #1a1a2e; color: white; }}
  .btn-primary:hover {{ background: #16213e; }}
  .btn-danger  {{ background: #e63946; color: white; margin-top: 1rem; }}
  .alert {{ padding: .75rem 1rem; border-radius: 6px; margin-bottom: 1rem; font-size: .9rem; }}
  .alert-ok  {{ background: #d1fae5; color: #065f46; border: 1px solid #6ee7b7; }}
  .alert-err {{ background: #fee2e2; color: #991b1b; border: 1px solid #fca5a5; }}
  .alert-info {{ background: #e0f2fe; color: #075985; border: 1px solid #7dd3fc; }}
  table {{ width: 100%; border-collapse: collapse; font-size: .88rem; }}
  th {{ background: #f0f0f5; text-align: left; padding: .6rem .8rem; }}
  td {{ padding: .55rem .8rem; border-bottom: 1px solid #eee; }}
</style>
</head>
<body>
<nav>
  <h1>&#128722; ECSDI Shop</h1>
  <a href="/">Inicio</a>
  <a href="/buscar">Buscar</a>
  <a href="/pedido">Nuevo pedido</a>
  <a href="/devolucion">Devoluciones</a>
</nav>
<div class="container">
  {content}
</div>
</body>
</html>
'''


def render(content):
    return BASE_HTML.format(content=content)


# ──────────────────────────────────────────
# RUTAS
# ──────────────────────────────────────────

@app.route('/')
def index():
    html = '''
    <h2>Bienvenido a ECSDI Shop</h2>
    <div class="card">
      <p style="margin-bottom:1rem">Sistema multi-agente de e-commerce.</p>
      <a href="/buscar" class="btn btn-primary">&#128269; Buscar productos</a>
      &nbsp;
      <a href="/pedido" class="btn btn-primary" style="margin-left:.5rem">&#128666; Nuevo pedido</a>
      &nbsp;
      <a href="/devolucion" class="btn btn-danger" style="margin-left:.5rem">&#8617; Solicitar devolucion</a>
    </div>
    '''
    return render(html)


@app.route('/buscar', methods=['GET', 'POST'])
def buscar():
    global mss_cnt
    resultados = []
    error = None
    buscado = False

    if request.method == 'POST':
        buscado = True
        addr = get_agent_address(ECSNS['Ag.Comprador'])
        if not addr:
            error = 'AgenteComprador no disponible. Asegurate de que esta en marcha.'
        else:
            gmess = Graph()
            gmess.bind('ecsns', ECSNS)
            busq = ECSNS['busqueda-ui']
            gmess.add((busq, RDF.type, ECSNS.Busqueda))

            precio_max = request.form.get('precio_max', '').strip()
            categoria  = request.form.get('categoria', '').strip()
            val_min    = request.form.get('valoracion', '').strip()
            texto      = request.form.get('texto', '').strip()

            if precio_max:
                gmess.add((busq, ECSNS.precioMaximo, Literal(float(precio_max))))
            if categoria and categoria != 'todas':
                gmess.add((busq, ECSNS.categoria, Literal(categoria)))
            if val_min:
                gmess.add((busq, ECSNS.valoracionMinima, Literal(float(val_min))))
            if texto:
                gmess.add((busq, ECSNS.textoBusqueda, Literal(texto)))

            try:
                gr = send_message(
                    build_message(gmess, perf=ACL.request, sender=UsuarioAgent.uri,
                                  receiver=agn.AgenteComprador, content=busq, msgcnt=mss_cnt),
                    addr,
                )
                mss_cnt += 1
                for s, p, o in gr:
                    if p == ECSNS.nombre:
                        resultados.append({
                            'id':        str(gr.value(s, ECSNS.idProducto) or ''),
                            'nombre':    str(o),
                            'precio':    str(gr.value(s, ECSNS.precio) or ''),
                            'categoria': str(gr.value(s, ECSNS.categoria) or ''),
                            'valoracion':str(gr.value(s, ECSNS.valoracion) or ''),
                            'vendedor':  str(gr.value(s, ECSNS.vendedor) or ''),
                            'externo':   bool(gr.value(s, RDF.type) == ECSNS.ProductoExterno),
                        })
            except Exception as e:
                error = f'Error al buscar: {e}'

    form_html = '''
    <h2>&#128269; Buscar productos</h2>
    <div class="card">
      <form method="POST">
        <label>Texto libre</label>
        <input type="text" name="texto" placeholder="p.ej. auriculares">
        <label>Categoria</label>
        <select name="categoria">
          <option value="todas">Todas</option>
          <option>Electronica</option>
          <option>Hogar</option>
          <option>Libros</option>
          <option>Ropa</option>
          <option>Deporte</option>
        </select>
        <label>Precio maximo (EUR)</label>
        <input type="number" step="0.01" name="precio_max" placeholder="200">
        <label>Valoracion minima (1-5)</label>
        <input type="number" step="0.1" min="1" max="5" name="valoracion" placeholder="4.0">
        <br><br>
        <button type="submit" class="btn btn-primary">Buscar</button>
      </form>
    </div>
    '''

    result_html = ''
    if error:
        result_html = f'<div class="alert alert-err">{error}</div>'
    elif buscado:
        if resultados:
            cards = ''
            for p in resultados:
                badge = '<span class="badge ext">Vendedor externo: ' + p["vendedor"] + '</span>' if p['externo'] else '<span class="badge">Tienda propia</span>'
                cards += f'''
                <div class="prod-card">
                  <h3>{p["nombre"]}</h3>
                  <div class="price">{p["precio"]} EUR</div>
                  <div class="meta">Categoria: {p["categoria"]}</div>
                  <div class="meta">Valoracion: {p["valoracion"]} / 5</div>
                  {badge}
                </div>'''
            result_html = f'<h3 style="margin:1rem 0 .5rem">Resultados ({len(resultados)})</h3><div class="grid">{cards}</div>'
        else:
            result_html = '<div class="alert alert-info">No se encontraron productos con esos filtros.</div>'

    return render(form_html + result_html)


@app.route('/pedido', methods=['GET', 'POST'])
def pedido():
    global mss_cnt
    resultado = None
    error = None

    if request.method == 'POST':
        addr = get_agent_address(ECSNS['Ag.GestorDePedidos'])
        if not addr:
            error = 'AgenteGestorPedidos no disponible.'
        else:
            try:
                # Parsear productos del formulario (formato: id|nombre|precio|cantidad|peso)
                productos_raw = request.form.get('productos', '').strip().splitlines()
                productos = []
                for linea in productos_raw:
                    partes = [x.strip() for x in linea.split('|')]
                    if len(partes) >= 3:
                        productos.append({
                            'id':       partes[0],
                            'nombre':   partes[1],
                            'precio':   float(partes[2]),
                            'cantidad': int(partes[3]) if len(partes) > 3 else 1,
                            'peso':     float(partes[4]) if len(partes) > 4 else 0.5,
                        })

                if not productos:
                    error = 'Debes especificar al menos un producto.'
                else:
                    gmess = Graph()
                    gmess.bind('ecsns', ECSNS)
                    ped = ECSNS['pedido-ui']
                    gmess.add((ped, RDF.type,         ECSNS.Pedido))
                    gmess.add((ped, ECSNS.comprador,  Literal(request.form.get('comprador', 'Usuario'))))
                    gmess.add((ped, ECSNS.direccion,  Literal(request.form.get('direccion', ''))))
                    gmess.add((ped, ECSNS.prioridad,  Literal(request.form.get('prioridad', 'normal'))))
                    gmess.add((ped, ECSNS.metodoPago, Literal(request.form.get('metodo_pago', 'tarjeta'))))

                    for i, p in enumerate(productos):
                        pn = ECSNS[f'ped-prod-{i}']
                        gmess.add((ped, ECSNS.tieneProducto, pn))
                        gmess.add((pn, ECSNS.idProducto, Literal(p['id'])))
                        gmess.add((pn, ECSNS.nombre,     Literal(p['nombre'])))
                        gmess.add((pn, ECSNS.precio,     Literal(p['precio'])))
                        gmess.add((pn, ECSNS.cantidad,   Literal(p['cantidad'])))
                        gmess.add((pn, ECSNS.peso,       Literal(p['peso'])))

                    gr = send_message(
                        build_message(gmess, perf=ACL.request, sender=UsuarioAgent.uri,
                                      receiver=agn.AgenteGestorPedidos, content=ped, msgcnt=mss_cnt),
                        addr,
                    )
                    mss_cnt += 1
                    factura_id = str(gr.value(predicate=ECSNS.idFactura) or '')
                    total      = str(gr.value(predicate=ECSNS.total) or '')
                    fecha      = str(gr.value(predicate=ECSNS.fecha) or '')
                    resultado = {'factura_id': factura_id, 'total': total, 'fecha': fecha}
            except Exception as e:
                error = f'Error al realizar pedido: {e}'

    form_html = '''
    <h2>&#128666; Nuevo pedido</h2>
    <div class="card">
      <form method="POST">
        <label>Nombre del comprador</label>
        <input type="text" name="comprador" required placeholder="Marc">
        <label>Direccion de entrega</label>
        <input type="text" name="direccion" required placeholder="Carrer de Pau Claris 10, Barcelona">
        <label>Prioridad</label>
        <select name="prioridad">
          <option value="normal">Normal (2-4 dias)</option>
          <option value="urgente">Urgente (1-2 dias)</option>
          <option value="economica">Economica (4-7 dias)</option>
        </select>
        <label>Metodo de pago</label>
        <select name="metodo_pago">
          <option value="tarjeta">Tarjeta</option>
          <option value="paypal">PayPal</option>
          <option value="transferencia">Transferencia</option>
        </select>
        <label>Productos (una linea por producto: id|nombre|precio|cantidad|peso)</label>
        <textarea name="productos" placeholder="p001|Teclado Mecanico|89.99|1|1.2
p002|Raton Gaming|45.00|1|0.3"></textarea>
        <br><br>
        <button type="submit" class="btn btn-primary">Realizar pedido</button>
      </form>
    </div>
    '''

    result_html = ''
    if error:
        result_html = f'<div class="alert alert-err">{error}</div>'
    elif resultado:
        result_html = f'''
        <div class="alert alert-ok">
          <strong>Pedido realizado correctamente</strong><br>
          Factura: <strong>{resultado["factura_id"]}</strong> &nbsp;|
          Total: <strong>{resultado["total"]} EUR</strong> &nbsp;|
          Fecha: {resultado["fecha"]}
        </div>'''

    return render(result_html + form_html)


@app.route('/devolucion', methods=['GET', 'POST'])
def devolucion():
    global mss_cnt
    resultado = None
    error = None

    if request.method == 'POST':
        addr = get_agent_address(ECSNS['Ag.Devolucion'])
        if not addr:
            error = 'AgenteDevolucion no disponible. Asegurate de que esta en marcha (puerto 9006).'
        else:
            try:
                gmess = Graph()
                gmess.bind('ecsns', ECSNS)
                sol = ECSNS['sol-devolucion-ui']
                gmess.add((sol, RDF.type,                ECSNS.SolicitudDevolucion))
                gmess.add((sol, ECSNS.comprador,         Literal(request.form.get('comprador', ''))))
                gmess.add((sol, ECSNS.idFactura,         Literal(request.form.get('factura_id', ''))))
                gmess.add((sol, ECSNS.razonDevolucion,   Literal(request.form.get('razon', 'insatisfaccion'))))
                gmess.add((sol, ECSNS.fechaRecepcion,    Literal(request.form.get('fecha_recepcion', ''))))

                gr = send_message(
                    build_message(gmess, perf=ACL.request, sender=UsuarioAgent.uri,
                                  receiver=agn.AgenteDevolucion, content=sol, msgcnt=mss_cnt),
                    addr,
                )
                mss_cnt += 1

                aceptada       = gr.value(predicate=ECSNS.aceptada)
                motivo         = str(gr.value(predicate=ECSNS.motivoDevolucion) or '')
                empresa        = str(gr.value(predicate=ECSNS.empresaMensajeria) or '')
                dev_id         = str(gr.value(predicate=ECSNS.idDevolucion) or '')
                resultado = {
                    'aceptada': str(aceptada) == 'True',
                    'motivo': motivo,
                    'empresa': empresa,
                    'dev_id': dev_id,
                }
            except Exception as e:
                error = f'Error al procesar devolucion: {e}'

    form_html = '''
    <h2>&#8617; Solicitar devolucion</h2>
    <div class="card">
      <form method="POST">
        <label>Nombre del comprador</label>
        <input type="text" name="comprador" required placeholder="Marc">
        <label>ID de factura</label>
        <input type="text" name="factura_id" required placeholder="FAC-XXXXXXXX">
        <label>Razon de la devolucion</label>
        <select name="razon">
          <option value="insatisfaccion">Insatisfaccion con el producto</option>
          <option value="defectuoso">Producto defectuoso</option>
          <option value="equivocado">Producto equivocado / no es lo que pedi</option>
          <option value="danado">Producto llegó danado</option>
        </select>
        <label>Fecha en que recibiste el pedido</label>
        <input type="date" name="fecha_recepcion" required>
        <br><br>
        <button type="submit" class="btn btn-danger">Solicitar devolucion</button>
      </form>
    </div>
    '''

    result_html = ''
    if error:
        result_html = f'<div class="alert alert-err">{error}</div>'
    elif resultado is not None:
        if resultado['aceptada']:
            result_html = f'''
            <div class="alert alert-ok">
              <strong>Devolucion ACEPTADA</strong> (ID: {resultado["dev_id"]})<br>
              {resultado["motivo"]}<br>
              Empresa de recogida: <strong>{resultado["empresa"]}</strong>
            </div>'''
        else:
            result_html = f'''
            <div class="alert alert-err">
              <strong>Devolucion RECHAZADA</strong><br>
              {resultado["motivo"]}
            </div>'''

    return render(result_html + form_html)


@app.route('/comm', methods=['GET', 'POST'])
def comunicacion():
    return 'AgenteUsuario web activo', 200


if __name__ == '__main__':
    logger.info(f'[Usuario] Interfaz web disponible en http://{hostname}:{port}')
    app.run(host=hostaddr, port=port)
