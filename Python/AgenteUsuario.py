import argparse
import json
import logging
import os
import socket
import sys
import uuid
from datetime import datetime

from flask import Flask, request, redirect, url_for
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
parser.add_argument('--port', type=int, default=9010)
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


def get_agent_address(agent_type):
    global mss_cnt
    gmess = Graph()
    gmess.bind('dso', DSO)
    search_obj = agn['Search-' + str(mss_cnt)]
    gmess.add((search_obj, RDF.type,      DSO.Search))
    gmess.add((search_obj, DSO.AgentType, agent_type))
    msg = build_message(gmess, perf=ACL.request, sender=UsuarioAgent.uri,
                        receiver=DirectoryAgent.uri, content=search_obj, msgcnt=mss_cnt)
    r = http_requests.get(DirectoryAgent.address,
                          params={'content': msg.serialize(format='xml')})
    mss_cnt += 1
    gr = Graph()
    gr.parse(data=r.text, format='xml')
    for s, p, o in gr:
        if p == DSO.Address:
            return str(o)
    return None


STYLE = '''
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: system-ui, sans-serif; background: #f5f5f5; color: #222; }
  header { background: #1a1a2e; color: #fff; padding: 1rem 2rem;
           display: flex; align-items: center; gap: 1rem; }
  header h1 { font-size: 1.3rem; }
  nav a { color: #aad4f5; text-decoration: none; margin-left: 1.5rem; font-size: 0.95rem; }
  nav a:hover { color: #fff; }
  main { max-width: 900px; margin: 2rem auto; padding: 0 1rem; }
  h2 { font-size: 1.2rem; margin-bottom: 1rem; color: #1a1a2e; }
  .card { background: #fff; border-radius: 8px; padding: 1.5rem;
          box-shadow: 0 1px 6px rgba(0,0,0,0.08); margin-bottom: 1.5rem; }
  label { display: block; margin-bottom: 0.3rem; font-size: 0.9rem;
          font-weight: 600; color: #444; }
  input, select, textarea { width: 100%; padding: 0.5rem 0.75rem; border: 1px solid #ccc;
                            border-radius: 5px; font-size: 0.95rem; margin-bottom: 0.9rem; }
  button, .btn { background: #1a1a2e; color: #fff; border: none; padding: 0.6rem 1.4rem;
                 border-radius: 5px; cursor: pointer; font-size: 0.95rem; }
  button:hover, .btn:hover { background: #16213e; }
  .success { background: #d4edda; border: 1px solid #c3e6cb; padding: 0.8rem;
             border-radius: 5px; margin-bottom: 1rem; color: #155724; }
  .error   { background: #f8d7da; border: 1px solid #f5c6cb; padding: 0.8rem;
             border-radius: 5px; margin-bottom: 1rem; color: #721c24; }
  table { width: 100%; border-collapse: collapse; font-size: 0.9rem; }
  th { background: #1a1a2e; color: #fff; padding: 0.5rem 0.75rem; text-align: left; }
  td { padding: 0.5rem 0.75rem; border-bottom: 1px solid #eee; }
  tr:hover td { background: #f9f9f9; }
  .tag { display: inline-block; padding: 0.2rem 0.5rem; border-radius: 3px;
         font-size: 0.8rem; font-weight: 600; }
  .tag-ext { background: #fff3cd; color: #856404; }
  .tag-ok  { background: #d4edda; color: #155724; }
  .tag-ko  { background: #f8d7da; color: #721c24; }
</style>
'''

NAV = '''
<header>
  <h1>🛒 ECSDI Shop</h1>
  <nav>
    <a href="/">Inicio</a>
    <a href="/buscar">Buscar</a>
    <a href="/pedido">Pedido</a>
    <a href="/devolucion">Devolución</a>
    <a href="/facturas">Facturas</a>
  </nav>
</header>
'''


@app.route('/')
def index():
    return STYLE + NAV + '''
<main>
  <div class="card">
    <h2>Bienvenido a ECSDI Shop</h2>
    <p style="margin-top:0.5rem;color:#555">Sistema multi-agente de e-commerce.</p>
    <div style="margin-top:1.5rem;display:flex;gap:1rem;flex-wrap:wrap">
      <a href="/buscar" class="btn">🔍 Buscar productos</a>
      <a href="/pedido" class="btn">📦 Hacer pedido</a>
      <a href="/devolucion" class="btn">↩ Solicitar devolución</a>
      <a href="/facturas" class="btn">🧾 Ver facturas</a>
    </div>
  </div>
</main>'''


@app.route('/buscar', methods=['GET', 'POST'])
def buscar():
    resultados = []
    error = ''
    if request.method == 'POST':
        try:
            addr = get_agent_address(ECSNS['Ag.Comprador'])
            if not addr:
                raise Exception('AgenteComprador no encontrado en el DS')
            gmess = Graph()
            gmess.bind('ecsns', ECSNS)
            busq = ECSNS['busqueda-' + str(uuid.uuid4())[:6]]
            gmess.add((busq, RDF.type, ECSNS.Busqueda))
            precio_max = request.form.get('precio_max', '').strip()
            categoria  = request.form.get('categoria', '').strip()
            val_min    = request.form.get('val_min', '').strip()
            if precio_max:
                gmess.add((busq, ECSNS.precioMaximo,     Literal(float(precio_max))))
            if categoria:
                gmess.add((busq, ECSNS.categoria,        Literal(categoria)))
            if val_min:
                gmess.add((busq, ECSNS.valoracionMinima, Literal(float(val_min))))
            global mss_cnt
            gr = send_message(
                build_message(gmess, perf=ACL.request, sender=UsuarioAgent.uri,
                              receiver=agn.AgenteComprador, content=busq, msgcnt=mss_cnt),
                addr)
            mss_cnt += 1
            productos_dict = {}
            for s, p, o in gr:
                if p == RDF.type and str(o).endswith('Producto'):
                    if str(s) not in productos_dict:
                        productos_dict[str(s)] = {'uri': str(s)}
            for s, p, o in gr:
                uri = str(s)
                if uri in productos_dict:
                    pred = str(p).split('#')[-1]
                    productos_dict[uri][pred] = str(o)
            resultados = list(productos_dict.values())
        except Exception as e:
            error = str(e)

    html = STYLE + NAV + '<main><div class="card"><h2>🔍 Buscar Productos</h2>'
    if error:
        html += f'<div class="error">{error}</div>'
    html += '''
    <form method="post">
      <label>Precio máximo (€)</label>
      <input type="number" name="precio_max" step="0.01" placeholder="Ej: 100">
      <label>Categoría</label>
      <select name="categoria">
        <option value="">Todas</option>
        <option>electronica</option><option>hogar</option>
        <option>libros</option><option>ropa</option>
      </select>
      <label>Valoración mínima</label>
      <input type="number" name="val_min" step="0.1" min="0" max="5" placeholder="Ej: 4.0">
      <button type="submit">Buscar</button>
    </form>'''
    if resultados:
        html += f'<p style="margin:1rem 0;color:#555">{len(resultados)} producto(s) encontrado(s)</p>'
        html += '<table><tr><th>Nombre</th><th>Precio</th><th>Categoría</th><th>Valoración</th><th>Vendedor</th></tr>'
        for p in resultados:
            vendedor = p.get('vendedor', 'tienda')
            tag = '<span class="tag tag-ext">externo</span>' if vendedor != 'tienda' else ''
            html += f"<tr><td>{p.get('nombre','')}</td><td>{p.get('precio','')}€</td>"
            html += f"<td>{p.get('categoria','')}</td><td>⭐ {p.get('valoracion','')}</td>"
            html += f"<td>{vendedor} {tag}</td></tr>"
        html += '</table>'
    html += '</div></main>'
    return html


@app.route('/pedido', methods=['GET', 'POST'])
def pedido():
    mensaje = ''
    error   = ''
    if request.method == 'POST':
        try:
            addr = get_agent_address(ECSNS['Ag.GestorDePedidos'])
            if not addr:
                raise Exception('AgenteGestorPedidos no encontrado en el DS')
            gmess = Graph()
            gmess.bind('ecsns', ECSNS)
            ped_id = 'PED-' + str(uuid.uuid4())[:8].upper()
            ped = ECSNS[ped_id]
            gmess.add((ped, RDF.type,          ECSNS.Pedido))
            gmess.add((ped, ECSNS.comprador,   Literal(request.form.get('comprador', 'Anonimo'))))
            gmess.add((ped, ECSNS.direccion,   Literal(request.form.get('direccion', ''))))
            gmess.add((ped, ECSNS.prioridad,   Literal(request.form.get('prioridad', 'normal'))))
            gmess.add((ped, ECSNS.metodoPago,  Literal(request.form.get('metodo_pago', 'tarjeta'))))

            ids_str     = request.form.get('ids_productos', '')
            nombres_str = request.form.get('nombres_productos', '')
            precios_str = request.form.get('precios_productos', '')
            pesos_str   = request.form.get('pesos_productos', '')

            ids     = [x.strip() for x in ids_str.split(',') if x.strip()]
            nombres = [x.strip() for x in nombres_str.split(',') if x.strip()]
            precios = [x.strip() for x in precios_str.split(',') if x.strip()]
            pesos   = [x.strip() for x in pesos_str.split(',') if x.strip()]

            for i, pid in enumerate(ids):
                p_node = ECSNS[f'prod-{ped_id}-{i}']
                gmess.add((ped, ECSNS.tieneProducto, p_node))
                gmess.add((p_node, ECSNS.idProducto, Literal(pid)))
                gmess.add((p_node, ECSNS.nombre,     Literal(nombres[i] if i < len(nombres) else pid)))
                gmess.add((p_node, ECSNS.precio,     Literal(float(precios[i]) if i < len(precios) else 0)))
                gmess.add((p_node, ECSNS.cantidad,   Literal(1)))
                gmess.add((p_node, ECSNS.peso,       Literal(float(pesos[i]) if i < len(pesos) else 0)))

            global mss_cnt
            gr = send_message(
                build_message(gmess, perf=ACL.request, sender=UsuarioAgent.uri,
                              receiver=agn.AgenteGestorPedidos, content=ped, msgcnt=mss_cnt),
                addr)
            mss_cnt += 1
            factura_id = ''
            total      = ''
            for s, p, o in gr:
                if p == ECSNS.idFactura: factura_id = str(o)
                if p == ECSNS.total:     total      = str(o)
            mensaje = f'✅ Pedido confirmado. Factura: <strong>{factura_id}</strong> — Total: <strong>{total}€</strong>'
        except Exception as e:
            error = str(e)

    html = STYLE + NAV + '<main><div class="card"><h2>📦 Realizar Pedido</h2>'
    if mensaje: html += f'<div class="success">{mensaje}</div>'
    if error:   html += f'<div class="error">{error}</div>'
    html += '''
    <form method="post">
      <label>Nombre comprador</label>
      <input type="text" name="comprador" placeholder="Tu nombre" required>
      <label>Dirección de entrega</label>
      <input type="text" name="direccion" placeholder="Calle, número, ciudad" required>
      <label>Prioridad</label>
      <select name="prioridad">
        <option value="normal">Normal</option>
        <option value="urgente">Urgente</option>
        <option value="economica">Económica</option>
      </select>
      <label>Método de pago</label>
      <select name="metodo_pago">
        <option value="tarjeta">Tarjeta</option>
        <option value="paypal">PayPal</option>
        <option value="transferencia">Transferencia</option>
      </select>
      <label>IDs de productos (separados por coma)</label>
      <input type="text" name="ids_productos" placeholder="p001, p002" required>
      <label>Nombres (separados por coma)</label>
      <input type="text" name="nombres_productos" placeholder="Laptop, Raton">
      <label>Precios (separados por coma)</label>
      <input type="text" name="precios_productos" placeholder="999.99, 29.99">
      <label>Pesos kg (separados por coma)</label>
      <input type="text" name="pesos_productos" placeholder="1.5, 0.2">
      <button type="submit">Confirmar pedido</button>
    </form>
    </div></main>'''
    return html


@app.route('/devolucion', methods=['GET', 'POST'])
def devolucion():
    mensaje = ''
    error   = ''
    if request.method == 'POST':
        try:
            addr = get_agent_address(ECSNS['Ag.Devolucion'])
            if not addr:
                raise Exception('AgenteDevolucion no encontrado en el DS')
            gmess = Graph()
            gmess.bind('ecsns', ECSNS)
            sol = ECSNS['sol-dev-' + str(uuid.uuid4())[:6]]
            gmess.add((sol, RDF.type,               ECSNS.SolicitudDevolucion))
            gmess.add((sol, ECSNS.comprador,        Literal(request.form.get('comprador', ''))))
            gmess.add((sol, ECSNS.idFactura,        Literal(request.form.get('factura_id', ''))))
            gmess.add((sol, ECSNS.razonDevolucion,  Literal(request.form.get('razon', 'insatisfaccion'))))
            gmess.add((sol, ECSNS.fechaRecepcion,   Literal(request.form.get('fecha_recepcion', ''))))
            global mss_cnt
            gr = send_message(
                build_message(gmess, perf=ACL.request, sender=UsuarioAgent.uri,
                              receiver=agn.AgenteDevolucion, content=sol, msgcnt=mss_cnt),
                addr)
            mss_cnt += 1
            aceptada = ''
            motivo   = ''
            empresa  = ''
            for s, p, o in gr:
                if p == ECSNS.aceptada:          aceptada = str(o)
                if p == ECSNS.motivoDevolucion:  motivo   = str(o)
                if p == ECSNS.empresaMensajeria: empresa  = str(o)
            if aceptada.lower() == 'true':
                mensaje = f'✅ {motivo}. <br>Empresa de recogida: <strong>{empresa}</strong>'
            else:
                mensaje = f'❌ {motivo}'
        except Exception as e:
            error = str(e)

    html = STYLE + NAV + '<main><div class="card"><h2>↩ Solicitar Devolución</h2>'
    if mensaje: html += f'<div class="{"success" if mensaje.startswith("✅") else "error"}">{mensaje}</div>'
    if error:   html += f'<div class="error">{error}</div>'
    html += '''
    <form method="post">
      <label>Nombre comprador</label>
      <input type="text" name="comprador" placeholder="Tu nombre" required>
      <label>ID de factura</label>
      <input type="text" name="factura_id" placeholder="FAC-XXXXXXXX" required>
      <label>Razón de la devolución</label>
      <select name="razon">
        <option value="insatisfaccion">Insatisfacción</option>
        <option value="defectuoso">Producto defectuoso</option>
        <option value="equivocado">Producto equivocado</option>
        <option value="danado">Producto dañado</option>
      </select>
      <label>Fecha de recepción del pedido</label>
      <input type="date" name="fecha_recepcion" required>
      <button type="submit">Solicitar devolución</button>
    </form>
    </div></main>'''
    return html


@app.route('/facturas')
def facturas():
    FACTURAS_PATH = os.path.join(os.path.dirname(__file__), 'data', 'facturas.json')
    facturas_data = []
    if os.path.exists(FACTURAS_PATH):
        with open(FACTURAS_PATH) as f:
            facturas_data = json.load(f)

    html = STYLE + NAV + '<main><div class="card"><h2>🧾 Facturas</h2>'
    if not facturas_data:
        html += '<p style="color:#888">No hay facturas registradas todavía.</p>'
    else:
        html += '<table><tr><th>ID</th><th>Comprador</th><th>Fecha</th><th>Total</th><th>Método pago</th></tr>'
        for fac in reversed(facturas_data):
            html += f"<tr><td><code>{fac['id']}</code></td><td>{fac.get('comprador','')}</td>"
            html += f"<td>{fac.get('fecha','')[:10]}</td><td><strong>{fac.get('total','')}€</strong></td>"
            html += f"<td>{fac.get('metodo_pago','')}</td></tr>"
        html += '</table>'
    html += '</div></main>'
    return html


if __name__ == '__main__':
    logger.info(f'[Usuario] Interfaz web en http://{hostname}:{port}')
    app.run(host=hostaddr, port=port)
