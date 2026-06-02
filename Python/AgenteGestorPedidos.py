import argparse
import json
import logging
import os
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
parser.add_argument('--port', type=int, default=9002)
parser.add_argument('--dhost', default=None)
parser.add_argument('--dport', type=int, default=9000)
args = parser.parse_args()

logger = config_logger(level=1)
port = args.port
hostname = socket.gethostname()
hostaddr = os.environ.get('ECSDI_PUBLIC_HOST') or hostname
# fix: usar 0.0.0.0 cuando --open para aceptar conexiones externas
flask_host = '0.0.0.0' if args.open else hostname
dport = args.dport
dhostname = os.environ.get('ECSDI_DHOST') or args.dhost or socket.gethostname()

app = Flask(__name__)
if not args.verbose:
    logging.getLogger('werkzeug').setLevel(logging.ERROR)

agn = Namespace('http://www.agentes.org#')
mss_cnt = 0
FACTURAS_PATH = os.path.join(os.path.dirname(__file__), 'data', 'facturas.json')
PEDIDOS_PATH  = os.path.join(os.path.dirname(__file__), 'data', 'pedidos.json')

logistico_address  = None
experiencia_address = None
usuario_address    = None

GestorAgent = Agent(
    'AgenteGestorPedidos',
    agn.AgenteGestorPedidos,
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


def register_message():
    global mss_cnt
    gmess = Graph()
    gmess.bind('foaf', FOAF)
    gmess.bind('dso', DSO)
    reg_obj = agn[GestorAgent.name + '-Register']
    gmess.add((reg_obj, RDF.type,      DSO.Register))
    gmess.add((reg_obj, DSO.Uri,       GestorAgent.uri))
    gmess.add((reg_obj, FOAF.name,     Literal(GestorAgent.name)))
    gmess.add((reg_obj, DSO.Address,   Literal(GestorAgent.address)))
    gmess.add((reg_obj, DSO.AgentType, ECSNS['Ag.GestorDePedidos']))
    gr = send_message(
        build_message(gmess, perf=ACL.request, sender=GestorAgent.uri,
                      receiver=DirectoryAgent.uri, content=reg_obj, msgcnt=mss_cnt),
        DirectoryAgent.address,
    )
    mss_cnt += 1
    return gr


def get_agent_address(agent_type):
    global mss_cnt
    gmess = Graph()
    gmess.bind('dso', DSO)
    search_obj = agn['Search-' + str(mss_cnt)]
    gmess.add((search_obj, RDF.type,        DSO.Search))
    gmess.add((search_obj, DSO.AgentType,   agent_type))
    msg = build_message(gmess, perf=ACL.request, sender=GestorAgent.uri,
                        receiver=DirectoryAgent.uri, content=search_obj, msgcnt=mss_cnt)
    response = http_requests.get(
        DirectoryAgent.address,
        params={'content': msg.serialize(format='xml')}
    )
    mss_cnt += 1
    gr = Graph()
    gr.parse(data=response.text, format='xml')
    for s, p, o in gr:
        if p == DSO.Address:
            return str(o)
    return None


def obtener_info_producto(prod_id):
    propios_path = os.path.join(os.path.dirname(__file__), 'data', 'productos.json')
    if os.path.exists(propios_path):
        with open(propios_path) as f:
            for p in json.load(f):
                if p['id'] == prod_id:
                    return p
    externos_path = os.path.join(os.path.dirname(__file__), 'data', 'productos_externos.json')
    if os.path.exists(externos_path):
        with open(externos_path) as f:
            for p in json.load(f):
                if p['id'] == prod_id:
                    return p
    return None


def obtener_address_vendedor(vendedor_nombre):
    global mss_cnt
    gmess = Graph()
    gmess.bind('dso', DSO)
    search_obj = agn[f'SearchVend-{mss_cnt}']
    gmess.add((search_obj, RDF.type, DSO.Search))
    gmess.add((search_obj, DSO.AgentType, ECSNS['Ag.VendedorExterno']))
    msg = build_message(gmess, perf=ACL.request, sender=GestorAgent.uri,
                        receiver=DirectoryAgent.uri, content=search_obj, msgcnt=mss_cnt)
    mss_cnt += 1
    try:
        r = http_requests.get(DirectoryAgent.address,
                              params={'content': msg.serialize(format='xml')}, timeout=5)
        gr_ds = Graph()
        gr_ds.parse(data=r.text, format='xml')
        for entry in gr_ds.subjects(DSO.Uri):
            uri = gr_ds.value(entry, DSO.Uri)
            addr = gr_ds.value(entry, DSO.Address)
            if uri and str(uri).endswith(vendedor_nombre):
                return str(addr)
        # Fallback: devolver cualquier vendedor externo registrado
        for entry in gr_ds.subjects(DSO.Uri):
            addr = gr_ds.value(entry, DSO.Address)
            if addr:
                return str(addr)
    except Exception as e:
        logger.warning(f'[GestorPedidos] Error buscando vendedor {vendedor_nombre}: {e}')
    return None


def realizar_pedido_externo(vendedor_addr, vendedor_nombre, pedido_id, comprador, direccion, prioridad, productos_vendedor):
    global mss_cnt
    gmess = Graph()
    gmess.bind('ecsns', ECSNS)
    ped_ext = ECSNS['ped-ext-' + pedido_id]
    gmess.add((ped_ext, RDF.type,        ECSNS.PedidoExterno))
    gmess.add((ped_ext, ECSNS.idPedido,  Literal(pedido_id)))
    gmess.add((ped_ext, ECSNS.comprador, Literal(comprador)))
    gmess.add((ped_ext, ECSNS.direccion, Literal(direccion)))
    gmess.add((ped_ext, ECSNS.prioridad, Literal(prioridad)))

    for i, p in enumerate(productos_vendedor):
        pn = ECSNS[f'ped-ext-prod-{pedido_id}-{i}']
        gmess.add((ped_ext, ECSNS.tieneProducto, pn))
        gmess.add((pn, ECSNS.idProducto, Literal(p['id'])))
        gmess.add((pn, ECSNS.cantidad,   Literal(p['cantidad'])))

    try:
        gr_resp = send_message(
            build_message(gmess, perf=ACL.request, sender=GestorAgent.uri,
                          content=ped_ext, msgcnt=mss_cnt),
            vendedor_addr
        )
        mss_cnt += 1
        resp_node = None
        for s, p, o in gr_resp.triples((None, RDF.type, ECSNS.RespuestaPedidoExterno)):
            resp_node = s
            break
        if resp_node:
            fecha_entrega = str(gr_resp.value(resp_node, ECSNS.fechaEntrega) or '')
            transportista = str(gr_resp.value(resp_node, ECSNS.transportista) or '')
            estado        = str(gr_resp.value(resp_node, ECSNS.estado) or '')
            return {
                'fecha_prevista': fecha_entrega,
                'transportista': transportista,
                'estado': estado,
                'exito': True
            }
    except Exception as e:
        logger.warning(f'[GestorPedidos] Error enviando pedido a {vendedor_nombre}: {e}')
    return {'exito': False}


def generar_factura(productos, comprador, direccion, metodo_pago, envios_vendedor=None):
    factura_id = 'FAC-' + str(uuid.uuid4())[:8].upper()
    total = sum(p['precio'] * p.get('cantidad', 1) for p in productos)
    factura = {
        'id':          factura_id,
        'comprador':   comprador,
        'fecha':       datetime.now().isoformat(),
        'productos':   productos,
        'total':       round(total, 2),
        'direccion':   direccion,
        'metodo_pago': metodo_pago,
        'envios_vendedor': envios_vendedor or []
    }
    if os.path.exists(FACTURAS_PATH):
        with open(FACTURAS_PATH) as f:
            facturas = json.load(f)
    else:
        facturas = []
    facturas.append(factura)
    os.makedirs(os.path.dirname(FACTURAS_PATH), exist_ok=True)
    with open(FACTURAS_PATH, 'w') as f:
        json.dump(facturas, f, indent=2)
    logger.info(f'[GestorPedidos] Factura {factura_id} generada -- Total: {total}EUR')
    return factura


def guardar_pedido(pedido_id, comprador, productos, sub_envios):
    """Persiste el resultado de envio en pedidos.json para trazabilidad."""
    registro = {
        'id':        pedido_id,
        'comprador': comprador,
        'fecha':     datetime.now().isoformat(),
        'productos': productos,
        'envios':    sub_envios,
    }
    os.makedirs(os.path.dirname(PEDIDOS_PATH), exist_ok=True)
    if os.path.exists(PEDIDOS_PATH):
        try:
            with open(PEDIDOS_PATH) as f:
                pedidos = json.load(f)
        except (json.JSONDecodeError, ValueError):
            pedidos = []
    else:
        pedidos = []
    # Actualizar si ya existe (por segunda notificacion del logistico)
    for i, p in enumerate(pedidos):
        if p.get('id') == pedido_id:
            pedidos[i] = registro
            break
    else:
        pedidos.append(registro)
    with open(PEDIDOS_PATH, 'w') as f:
        json.dump(pedidos, f, indent=2)


def notificar_logistico(pedido):
    global mss_cnt, logistico_address
    if logistico_address is None:
        logistico_address = get_agent_address(ECSNS['Ag.Logistico'])
    if logistico_address is None:
        logger.warning('[GestorPedidos] AgenteLogistico no encontrado en DS')
        return
    gmess = Graph()
    gmess.bind('ecsns', ECSNS)
    ped_obj = ECSNS['pedido-' + pedido['id']]
    gmess.add((ped_obj, RDF.type,        ECSNS.Pedido))
    gmess.add((ped_obj, ECSNS.idPedido,  Literal(pedido['id'])))
    gmess.add((ped_obj, ECSNS.direccion, Literal(pedido['direccion'])))
    gmess.add((ped_obj, ECSNS.prioridad, Literal(pedido['prioridad'])))
    for i, p in enumerate(pedido['productos']):
        prod_node = ECSNS['ped-prod-' + str(i)]
        gmess.add((ped_obj,   ECSNS.tieneProducto, prod_node))
        gmess.add((prod_node, ECSNS.idProducto,    Literal(p['id'])))
        gmess.add((prod_node, ECSNS.nombre,        Literal(p.get('nombre', ''))))
        gmess.add((prod_node, ECSNS.cantidad,      Literal(p.get('cantidad', 1))))
        gmess.add((prod_node, ECSNS.peso,          Literal(p.get('peso', 0))))
        gmess.add((prod_node, ECSNS.precio,        Literal(p.get('precio', 0))))
    send_message(
        build_message(gmess, perf=ACL.request, sender=GestorAgent.uri,
                      receiver=agn.AgenteLogistico, content=ped_obj, msgcnt=mss_cnt),
        logistico_address,
    )
    mss_cnt += 1


def notificar_experiencia_compra(comprador, factura, productos):
    """
    Notifica al AgenteExperiencia que se finalizo una compra
    para que actualice el historial del comprador.
    """
    global mss_cnt, experiencia_address
    if experiencia_address is None:
        experiencia_address = get_agent_address(ECSNS['Ag.Experiencia'])
    if experiencia_address is None:
        logger.warning('[GestorPedidos] AgenteExperiencia no encontrado en DS')
        return
    gmess = Graph()
    gmess.bind('ecsns', ECSNS)
    compra_node = ECSNS['compra-' + factura['id']]
    gmess.add((compra_node, RDF.type,        ECSNS.CompraFinalizada))
    gmess.add((compra_node, ECSNS.comprador, Literal(comprador)))
    gmess.add((compra_node, ECSNS.idPedido,  Literal(factura['id'])))
    gmess.add((compra_node, ECSNS.total,     Literal(factura['total'])))
    gmess.add((compra_node, ECSNS.fecha,     Literal(factura['fecha'])))
    for i, p in enumerate(productos):
        pn = ECSNS['compra-prod-' + str(i)]
        gmess.add((compra_node, ECSNS.tieneProducto, pn))
        gmess.add((pn, ECSNS.idProducto, Literal(p['id'])))
        gmess.add((pn, ECSNS.nombre,     Literal(p.get('nombre', ''))))
        gmess.add((pn, ECSNS.precio,     Literal(p.get('precio', 0))))
        gmess.add((pn, ECSNS.cantidad,   Literal(p.get('cantidad', 1))))
    send_message(
        build_message(gmess, perf=ACL.inform, sender=GestorAgent.uri,
                      receiver=agn.AgenteExperiencia, content=compra_node, msgcnt=mss_cnt),
        experiencia_address,
    )
    mss_cnt += 1
    logger.info(f'[GestorPedidos] AgenteExperiencia notificado -- comprador: {comprador}')


def notificar_usuario_envios(pedido_id, sub_envios):
    """
    Notifica al AgenteUsuario (interfaz web) el resultado de los envios
    para que lo muestre al cliente: transportista y fecha por cada sub-envio.
    Si el AgenteUsuario no esta registrado en el DS, lo ignora silenciosamente.
    """
    global mss_cnt, usuario_address
    if usuario_address is None:
        try:
            usuario_address = get_agent_address(ECSNS['Ag.Usuario'])
        except Exception:
            usuario_address = None
    if usuario_address is None:
        logger.info('[GestorPedidos] AgenteUsuario no registrado en DS, omitiendo notificacion de envios')
        return
    gmess = Graph()
    gmess.bind('ecsns', ECSNS)
    notif_node = ECSNS['notif-envios-' + pedido_id]
    gmess.add((notif_node, RDF.type,       ECSNS.NotificacionEnvios))
    gmess.add((notif_node, ECSNS.idPedido, Literal(pedido_id)))
    for i, envio in enumerate(sub_envios):
        en = ECSNS[f'notif-sub-{pedido_id}-{i}']
        gmess.add((notif_node, ECSNS.tieneSubEnvio, en))
        gmess.add((en, ECSNS.idEnvio,            Literal(envio.get('id', ''))))
        gmess.add((en, ECSNS.tieneCentro,        Literal(envio.get('centro', ''))))
        gmess.add((en, ECSNS.tieneTransportista, Literal(envio.get('transportista', ''))))
        gmess.add((en, ECSNS.tieneFechaEntrega,  Literal(envio.get('fecha', ''))))
        for pid in envio.get('productos', []):
            gmess.add((en, ECSNS.tieneProductoId, Literal(pid)))
    try:
        send_message(
            build_message(gmess, perf=ACL.inform, sender=GestorAgent.uri,
                          receiver=agn.AgenteUsuario, content=notif_node, msgcnt=mss_cnt),
            usuario_address,
        )
        mss_cnt += 1
        logger.info(f'[GestorPedidos] AgenteUsuario notificado con {len(sub_envios)} envio/s del pedido {pedido_id}')
    except Exception as e:
        logger.warning(f'[GestorPedidos] No se pudo notificar al AgenteUsuario: {e}')


def procesar_compra(gm, content):
    comprador   = str(gm.value(subject=content, predicate=ECSNS.comprador)  or 'Anonimo')
    direccion   = str(gm.value(subject=content, predicate=ECSNS.direccion)  or '')
    prioridad   = str(gm.value(subject=content, predicate=ECSNS.prioridad)  or 'normal')
    metodo_pago = str(gm.value(subject=content, predicate=ECSNS.metodoPago) or '')

    productos = []
    for prod_node in gm.objects(content, ECSNS.tieneProducto):
        pid = str(gm.value(prod_node, ECSNS.idProducto))
        pinfo = obtener_info_producto(pid) or {}
        productos.append({
            'id':       pid,
            'nombre':   str(gm.value(prod_node, ECSNS.nombre)   or pinfo.get('nombre', '')),
            'precio':   float(gm.value(prod_node, ECSNS.precio) or pinfo.get('precio', 0)),
            'cantidad': int(gm.value(prod_node, ECSNS.cantidad) or 1),
            'peso':     float(gm.value(prod_node, ECSNS.peso)   or pinfo.get('peso', 0)),
            'vendedor': pinfo.get('vendedor', 'tienda'),
            'gestion_envio': pinfo.get('gestion_envio', 'tienda')
        })

    shop_products = []
    vendor_products = {}

    for p in productos:
        if p['vendedor'] == 'tienda' or p['gestion_envio'] == 'tienda':
            shop_products.append(p)
        else:
            vname = p['vendedor']
            if vname not in vendor_products:
                vendor_products[vname] = []
            vendor_products[vname].append(p)

    pedido_id = 'PED-' + str(uuid.uuid4())[:8].upper()
    envios_vendedor = []

    # Pedidos gestionados por la tienda propia (via AgenteLogistico)
    if shop_products:
        shop_pedido = {
            'id':        pedido_id,
            'productos': shop_products,
            'direccion': direccion,
            'prioridad': prioridad,
        }
        notificar_logistico(shop_pedido)

    # Pedidos gestionados por vendedores externos
    for vname, vprods in vendor_products.items():
        vaddr = obtener_address_vendedor(vname)
        if vaddr:
            logger.info(f'[GestorPedidos] Contactando vendedor externo {vname} en {vaddr}...')
            info_envio = realizar_pedido_externo(vaddr, vname, pedido_id, comprador, direccion, prioridad, vprods)
            if info_envio['exito']:
                envios_vendedor.append({
                    'vendedor': vname,
                    'productos': [p['id'] for p in vprods],
                    'transportista': info_envio['transportista'],
                    'fecha_prevista': info_envio['fecha_prevista']
                })
            else:
                envios_vendedor.append({
                    'vendedor': vname,
                    'productos': [p['id'] for p in vprods],
                    'transportista': f'Mensajeria {vname}',
                    'fecha_prevista': (datetime.now() + timedelta(days=5)).strftime('%Y-%m-%d')
                })
        else:
            logger.warning(f'[GestorPedidos] No se pudo encontrar direccion del vendedor {vname}')
            envios_vendedor.append({
                'vendedor': vname,
                'productos': [p['id'] for p in vprods],
                'transportista': f'Mensajeria {vname}',
                'fecha_prevista': (datetime.now() + timedelta(days=5)).strftime('%Y-%m-%d')
            })

    factura = generar_factura(productos, comprador, direccion, metodo_pago, envios_vendedor)
    notificar_experiencia_compra(comprador, factura, productos)

    gr = Graph()
    gr.bind('ecsns', ECSNS)
    fac_node = ECSNS['factura-' + factura['id']]
    gr.add((fac_node, RDF.type,        ECSNS.Factura))
    gr.add((fac_node, ECSNS.idFactura, Literal(factura['id'])))
    gr.add((fac_node, ECSNS.total,     Literal(factura['total'])))
    gr.add((fac_node, ECSNS.fecha,     Literal(factura['fecha'])))
    return gr, fac_node


def procesar_resultado_envio(gm, content):
    """
    Recibe el ResultadoEnvio del AgenteLogistico, lo persiste en pedidos.json
    y notifica al AgenteUsuario para que lo muestre al cliente.
    """
    pedido_id  = str(gm.value(content, ECSNS.idPedido)  or 'DESCONOCIDO')
    num_envios = int(gm.value(content, ECSNS.numEnvios) or 0)
    logger.info(f'[GestorPedidos] Pedido {pedido_id} gestionado con {num_envios} envio/s:')
    sub_envios = []
    for envio_node in gm.objects(content, ECSNS.tieneSubEnvio):
        envio_id      = str(gm.value(envio_node, ECSNS.idEnvio)            or '')
        centro        = str(gm.value(envio_node, ECSNS.tieneCentro)        or '')
        transportista = str(gm.value(envio_node, ECSNS.tieneTransportista) or '')
        fecha         = str(gm.value(envio_node, ECSNS.tieneFechaEntrega)  or '')
        productos     = [str(o) for o in gm.objects(envio_node, ECSNS.tieneProductoId)]
        sub_envios.append({
            'id': envio_id, 'centro': centro,
            'transportista': transportista, 'fecha': fecha,
            'productos': productos,
        })
        logger.info(
            f'  [{envio_id}] Centro: {centro} | '
            f'Transportista: {transportista} | Entrega: {fecha} | '
            f'Productos: {productos}'
        )

    # Persistir en pedidos.json
    guardar_pedido(pedido_id, comprador='', productos=[], sub_envios=sub_envios)

    # Notificar al AgenteUsuario (si esta disponible)
    notificar_usuario_envios(pedido_id, sub_envios)

    return sub_envios


@app.route('/stop')
def stop():
    cola1.put(0)
    shutdown_server()
    return 'Parando AgenteGestorPedidos'


@app.route('/comm', methods=['GET', 'POST'])
def comunicacion():
    global mss_cnt
    logger.info('[GestorPedidos] Mensaje recibido')
    message = request.args.get('content') or request.form.get('content')
    gm = Graph()
    gm.parse(data=message, format='xml')
    msgdic = get_message_properties(gm)

    if msgdic is None:
        gr = build_message(Graph(), ACL['not-understood'],
                           sender=GestorAgent.uri, msgcnt=mss_cnt)
        mss_cnt += 1
        return gr.serialize(format='xml')

    perf    = msgdic.get('performative')
    content = msgdic.get('content')
    accion  = gm.value(subject=content, predicate=RDF.type) if content else None

    if perf == ACL.request and accion == ECSNS.Pedido:
        resp_graph, resp_node = procesar_compra(gm, content)
        gr = build_message(resp_graph, ACL.inform,
                           sender=GestorAgent.uri,
                           receiver=msgdic['sender'],
                           content=resp_node,
                           msgcnt=mss_cnt)

    elif perf == ACL.inform and accion == ECSNS.ResultadoEnvio:
        procesar_resultado_envio(gm, content)
        gr = build_message(Graph(), ACL.inform,
                           sender=GestorAgent.uri,
                           receiver=msgdic['sender'],
                           msgcnt=mss_cnt)

    else:
        gr = build_message(Graph(), ACL['not-understood'],
                           sender=GestorAgent.uri, msgcnt=mss_cnt)

    mss_cnt += 1
    return gr.serialize(format='xml')


def agentbehavior1(cola):
    register_message()
    logger.info('[GestorPedidos] Registrado y escuchando')
    fin = False
    while not fin:
        time.sleep(1)
        if not cola.empty() and cola.get() == 0:
            fin = True


if __name__ == '__main__':
    os.makedirs(os.path.join(os.path.dirname(__file__), 'data'), exist_ok=True)
    ab1 = Process(target=agentbehavior1, args=(cola1,))
    ab1.start()
    # fix: respetar --open para escuchar en 0.0.0.0 cuando se requiere
    app.run(host=flask_host, port=port)
    ab1.join()
    logger.info('[GestorPedidos] Fin')
