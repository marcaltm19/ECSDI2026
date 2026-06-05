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
FACTURAS_PATH = os.path.join(os.path.dirname(__file__), 'data', 'listado_facturas.json')
CENTROS_PATH  = os.path.join(os.path.dirname(__file__), 'data', 'centros_logisticos.json')

_logistico_addresses = {}   # centro_nombre -> address
usuario_address      = None
gestor_pagos_address = None

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



def cargar_centros_gp():
    if not os.path.exists(CENTROS_PATH):
        return []
    with open(CENTROS_PATH) as f:
        return json.load(f)


def obtener_centro_de_producto_gp(producto_id):
    centros = cargar_centros_gp()
    for centro in centros:
        if producto_id in centro.get('productos', []):
            return centro
    logger.warning(f'[GestorPedidos] Producto {producto_id} sin centro asignado, usando primero disponible')
    return centros[0] if centros else {'id': 'CL-001', 'nombre': 'Centro Madrid', 'productos': []}


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


def generar_factura(productos, comprador, direccion, metodo_pago, envios_vendedor=None, factura_id=None):
    if factura_id is None:
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
        'envios_vendedor': envios_vendedor or [],
        'envios_logistico': [],
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



def actualizar_factura_envios_logistico(factura_id, sub_envios):
    """Persiste los envíos logísticos en facturas.json para que la UI los muestre al refrescar."""
    if not os.path.exists(FACTURAS_PATH):
        return
    try:
        with open(FACTURAS_PATH) as f:
            facturas = json.load(f)
    except (json.JSONDecodeError, ValueError):
        return
    for factura in facturas:
        if factura.get('id') == factura_id:
            existentes = factura.get('envios_logistico', [])
            ids_existentes = {e.get('id') for e in existentes}
            for e in sub_envios:
                if e.get('id') not in ids_existentes:
                    existentes.append(e)
            factura['envios_logistico'] = existentes
            break
    else:
        return
    with open(FACTURAS_PATH, 'w') as f:
        json.dump(facturas, f, indent=2)


def get_logistico_address_for_centro(centro_nombre):
    global mss_cnt
    if centro_nombre in _logistico_addresses:
        return _logistico_addresses[centro_nombre]
    gmess = Graph()
    gmess.bind('dso', DSO)
    search_obj = agn[f'SearchLog-{mss_cnt}']
    gmess.add((search_obj, RDF.type,      DSO.Search))
    gmess.add((search_obj, DSO.AgentType, ECSNS['Ag.Logistico']))
    msg = build_message(gmess, perf=ACL.request, sender=GestorAgent.uri,
                        receiver=DirectoryAgent.uri, content=search_obj, msgcnt=mss_cnt)
    mss_cnt += 1
    try:
        r = http_requests.get(DirectoryAgent.address,
                              params={'content': msg.serialize(format='xml')}, timeout=5)
        gr_ds = Graph()
        gr_ds.parse(data=r.text, format='xml')
        # Buscar el logístico cuyo tieneCentro coincide
        for entry in gr_ds.subjects(ECSNS.tieneCentro, Literal(centro_nombre)):
            addr = gr_ds.value(entry, DSO.Address)
            if addr:
                _logistico_addresses[centro_nombre] = str(addr)
                logger.info(f'[GestorPedidos] Logístico para {centro_nombre}: {addr}')
                return str(addr)
        # Fallback: cualquier logístico registrado
        for s, p, o in gr_ds:
            if p == DSO.Address:
                logger.warning(f'[GestorPedidos] No hay logístico específico para {centro_nombre}, usando fallback')
                return str(o)
    except Exception as e:
        logger.warning(f'[GestorPedidos] Error buscando logístico para {centro_nombre}: {e}')
    return None


def notificar_gestor_pagos(order_id, comprador, metodo_pago, total, vendedor_externo_id=None):
    """INFORM InformacionPago al AgenteGestorPagos al crear el pedido."""
    global mss_cnt, gestor_pagos_address
    if gestor_pagos_address is None:
        gestor_pagos_address = get_agent_address(ECSNS['Ag.GestorDePagos'])
    if gestor_pagos_address is None:
        logger.warning('[GestorPedidos] AgenteGestorPagos no encontrado en DS')
        return
    gmess = Graph()
    gmess.bind('ecsns', ECSNS)
    node = ECSNS['info-pago-' + order_id]
    gmess.add((node, RDF.type,              ECSNS.InformacionPago))
    gmess.add((node, ECSNS.idPedido,        Literal(order_id)))
    gmess.add((node, ECSNS.comprador,       Literal(comprador)))
    gmess.add((node, ECSNS.metodoPago,      Literal(metodo_pago)))
    gmess.add((node, ECSNS.total,           Literal(round(total, 2))))
    if vendedor_externo_id:
        gmess.add((node, ECSNS.vendedorExternoId, Literal(vendedor_externo_id)))
    try:
        send_message(
            build_message(gmess, perf=ACL.inform, sender=GestorAgent.uri,
                          receiver=agn.AgenteGestorPagos, content=node, msgcnt=mss_cnt),
            gestor_pagos_address,
        )
        mss_cnt += 1
        logger.info(f'[GestorPedidos] Información de pago enviada a GestorPagos — {order_id}')
    except Exception as e:
        logger.warning(f'[GestorPedidos] Error notificando GestorPagos: {e}')


def notificar_confirmacion_envio_pagos(order_id, comprador, total=0, vendedor_externo_id=None):
    """INFORM ConfirmacionEnvio al GestorPagos (p. ej. pedido solo externo)."""
    global mss_cnt, gestor_pagos_address
    if gestor_pagos_address is None:
        gestor_pagos_address = get_agent_address(ECSNS['Ag.GestorDePagos'])
    if gestor_pagos_address is None:
        return
    gmess = Graph()
    gmess.bind('ecsns', ECSNS)
    node = ECSNS['conf-envio-' + order_id]
    gmess.add((node, RDF.type,       ECSNS.ConfirmacionEnvio))
    gmess.add((node, ECSNS.idPedido, Literal(order_id)))
    gmess.add((node, ECSNS.comprador, Literal(comprador)))
    if total:
        gmess.add((node, ECSNS.total, Literal(round(total, 2))))
    if vendedor_externo_id:
        gmess.add((node, ECSNS.vendedorExternoId, Literal(vendedor_externo_id)))
    try:
        send_message(
            build_message(gmess, perf=ACL.inform, sender=GestorAgent.uri,
                          receiver=agn.AgenteGestorPagos, content=node, msgcnt=mss_cnt),
            gestor_pagos_address,
        )
        mss_cnt += 1
        logger.info(f'[GestorPedidos] Confirmación de envío enviada a GestorPagos — {order_id}')
    except Exception as e:
        logger.warning(f'[GestorPedidos] Error enviando confirmación a GestorPagos: {e}')


def notificar_logistico(pedido):
    global mss_cnt

    mapa_zonas = {
        'Barcelona':  'Centro Barcelona',
        'Zaragoza':   'Centro Barcelona',
        'Valencia':   'Centro Valencia',
        'Madrid':     'Centro Madrid',
        'Bilbao':     'Centro Madrid',
        'Sevilla':    'Centro Sevilla',
    }

    ciudad  = pedido.get('ciudad', 'Madrid')
    centros = cargar_centros_gp()

    # Agrupar productos por centro logístico usando lógica de stock + preferencia geográfica
    grupos = {}
    for p in pedido['productos']:
        # Paso A: centros que tienen este producto en stock
        centros_con_stock = [c for c in centros if p['id'] in c.get('productos', [])]

        if not centros_con_stock:
            # Sin stock registrado: usar preferencia geográfica como fallback
            centro_nombre = mapa_zonas.get(ciudad, 'Centro Madrid')
            logger.warning(f'[GestorPedidos] Producto {p["id"]} sin stock registrado, fallback a {centro_nombre}')
        elif len(centros_con_stock) == 1:
            # Paso B: stock en un único centro → asignación obligatoria
            centro_nombre = centros_con_stock[0]['nombre']
        else:
            # Paso C: stock en varios centros → preferencia geográfica del usuario
            centro_nombre = mapa_zonas.get(ciudad, 'Centro Madrid')
            nombres_con_stock = {c['nombre'] for c in centros_con_stock}
            if centro_nombre not in nombres_con_stock:
                centro_nombre = centros_con_stock[0]['nombre']

        if centro_nombre not in grupos:
            grupos[centro_nombre] = []
        grupos[centro_nombre].append(p)

    if not grupos:
        logger.warning('[GestorPedidos] Pedido sin productos para logístico')
        return

    for centro_nombre, productos_centro in grupos.items():
        addr = get_logistico_address_for_centro(centro_nombre)
        if addr is None:
            logger.warning(f'[GestorPedidos] No hay logístico para {centro_nombre}, omitiendo')
            continue

        sub_id    = f'{pedido["id"]}-{centro_nombre.replace(" ", "")}'
        agent_uri = agn[f'AgenteLogistico_{centro_nombre.replace(" ", "_")}']
        gmess     = Graph()
        gmess.bind('ecsns', ECSNS)
        sol_obj = ECSNS[f'sol-pedido-{sub_id}']
        ped_obj = ECSNS[f'pedido-{sub_id}']
        gmess.add((sol_obj, RDF.type,           ECSNS.SolicitudPedido))
        gmess.add((sol_obj, ECSNS.tienePedido,  ped_obj))
        gmess.add((ped_obj, RDF.type,           ECSNS.Pedido))
        gmess.add((ped_obj, ECSNS.idPedido,     Literal(pedido['id'])))
        gmess.add((ped_obj, ECSNS.comprador,    Literal(pedido.get('comprador', 'Anonimo'))))
        gmess.add((ped_obj, ECSNS.direccion,    Literal(pedido['direccion'])))
        gmess.add((ped_obj, ECSNS.prioridad,    Literal(pedido['prioridad'])))
        for i, p in enumerate(productos_centro):
            prod_node = ECSNS[f'ped-prod-{sub_id}-{i}']
            gmess.add((ped_obj,   ECSNS.tieneProducto, prod_node))
            gmess.add((prod_node, ECSNS.idProducto,    Literal(p['id'])))
            gmess.add((prod_node, ECSNS.nombre,        Literal(p.get('nombre', ''))))
            gmess.add((prod_node, ECSNS.cantidad,      Literal(p.get('cantidad', 1))))
            gmess.add((prod_node, ECSNS.peso,          Literal(p.get('peso', 0))))
            gmess.add((prod_node, ECSNS.precio,        Literal(p.get('precio', 0))))
            gmess.add((prod_node, ECSNS.tieneCentro,   Literal(centro_nombre)))
        send_message(
            build_message(gmess, perf=ACL.request, sender=GestorAgent.uri,
                          receiver=agent_uri, content=sol_obj, msgcnt=mss_cnt),
            addr,
        )
        mss_cnt += 1
        logger.info(f'[GestorPedidos] Sub-pedido → {centro_nombre} ({len(productos_centro)} producto/s)')



def _productos_factura(pedido_id):
    if not os.path.exists(FACTURAS_PATH):
        return [], ''
    try:
        with open(FACTURAS_PATH) as f:
            facturas = json.load(f)
    except (json.JSONDecodeError, ValueError):
        return [], ''
    for factura in facturas:
        if factura.get('id') == pedido_id:
            return factura.get('productos', []), factura.get('comprador', '')
    return [], ''


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
    ped_node    = gm.value(subject=content, predicate=ECSNS.tienePedido)
    comprador   = str(gm.value(subject=ped_node, predicate=ECSNS.comprador)  or 'Anonimo')
    direccion   = str(gm.value(subject=ped_node, predicate=ECSNS.direccion)  or '')
    ciudad      = str(gm.value(subject=ped_node, predicate=ECSNS.ciudad)     or 'Madrid')
    prioridad   = str(gm.value(subject=ped_node, predicate=ECSNS.prioridad)  or 'normal')
    metodo_pago = str(gm.value(subject=ped_node, predicate=ECSNS.metodoPago) or '')

    productos = []
    for prod_node in gm.objects(ped_node, ECSNS.tieneProducto):
        pid = str(gm.value(prod_node, ECSNS.idProducto))
        productos.append({
            'id':          pid,
            'nombre':      str(gm.value(prod_node, ECSNS.nombre)       or ''),
            'precio':      float(gm.value(prod_node, ECSNS.precio)      or 0),
            'cantidad':    int(gm.value(prod_node, ECSNS.cantidad)       or 1),
            'peso':        float(gm.value(prod_node, ECSNS.peso)         or 0),
            'vendedor':    str(gm.value(prod_node, ECSNS.vendedor)       or 'tienda'),
            'gestion_envio': str(gm.value(prod_node, ECSNS.gestionEnvio) or 'tienda'),
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

    # Mismo identificador para factura y pedido logístico (la UI muestra la factura)
    factura_id = 'FAC-' + str(uuid.uuid4())[:8].upper()
    envios_vendedor = []
    total_pedido = sum(p['precio'] * p.get('cantidad', 1) for p in productos)
    vendedor_ext_id = None
    if vendor_products:
        vendedor_ext_id = next(iter(vendor_products.keys()), None)


    # Pedidos gestionados por vendedores externos (antes de persistir factura)
    for vname, vprods in vendor_products.items():
        vaddr = obtener_address_vendedor(vname)
        if vaddr:
            logger.info(f'[GestorPedidos] Contactando vendedor externo {vname} en {vaddr}...')
            info_envio = realizar_pedido_externo(vaddr, vname, factura_id, comprador, direccion, prioridad, vprods)
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

    factura = generar_factura(productos, comprador, direccion, metodo_pago, envios_vendedor, factura_id=factura_id)

    notificar_gestor_pagos(
        factura_id, comprador, metodo_pago, total_pedido, vendedor_ext_id,
    )

    # Pedidos gestionados por la tienda propia (via AgenteLogistico)
    if shop_products:
        shop_pedido = {
            'id':        factura_id,
            'comprador': comprador,
            'productos': shop_products,
            'direccion': direccion,
            'ciudad':    ciudad,
            'prioridad': prioridad,
        }
        notificar_logistico(shop_pedido)

    # Pedido solo vendedor externo: no hay logístico, confirmar envío a GestorPagos
    if not shop_products and vendor_products:
        notificar_confirmacion_envio_pagos(
            factura_id, comprador, factura['total'], vendedor_ext_id,
        )

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
    Recibe el ResultadoEnvio del AgenteLogistico, actualiza la factura y notifica al usuario.
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

    productos, comprador = _productos_factura(pedido_id)

    actualizar_factura_envios_logistico(pedido_id, sub_envios)

    notificar_usuario_envios(pedido_id, sub_envios)
    return sub_envios


def marcar_factura_devuelta(factura_id):
    if not os.path.exists(FACTURAS_PATH):
        return
    try:
        with open(FACTURAS_PATH) as f:
            facturas = json.load(f)
    except (json.JSONDecodeError, ValueError):
        return
    for fac in facturas:
        if fac.get('id') == factura_id:
            fac['devuelta'] = True
            fac['fecha_devolucion'] = datetime.now().isoformat()
            break
    else:
        return
    with open(FACTURAS_PATH, 'w') as f:
        json.dump(facturas, f, indent=2)
    logger.info(f'[GestorPedidos] Factura {factura_id} marcada como devuelta')


def verificar_compra(factura_id, comprador):
    if not os.path.exists(FACTURAS_PATH):
        return False, 'Factura no encontrada', []
    try:
        with open(FACTURAS_PATH) as f:
            facturas = json.load(f)
    except (json.JSONDecodeError, ValueError):
        return False, 'Error al leer facturas', []
    for fac in facturas:
        if fac.get('id') == factura_id:
            if (fac.get('comprador') or '').strip().casefold() != (comprador or '').strip().casefold():
                return False, 'El comprador no coincide con el de la factura', []
            if fac.get('devuelta'):
                return False, 'Esta factura ya fue devuelta', []
            return True, 'Compra verificada', fac.get('productos', [])
    return False, 'Factura no encontrada', []


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
    if not message:
        return ('<html><head><title>AgenteGestorPedidos</title></head>'
                '<body style="font-family:sans-serif;padding:32px"><h2>AgenteGestorPedidos</h2>'
                '<p><strong>Estado:</strong> activo &nbsp;|&nbsp; <strong>Puerto:</strong> ' + str(port) + '</p>'
                '<p style="color:#666">Endpoint ACL/RDF entre agentes.</p>'
                '</body></html>'), 200, {'Content-Type': 'text/html; charset=utf-8'}
    try:
        gm = Graph()
        gm.parse(data=message, format='xml')
    except Exception as e:
        logger.warning(f'[GestorPedidos] /comm parse error: {e}')
        gr = build_message(Graph(), ACL['not-understood'], sender=GestorAgent.uri, msgcnt=mss_cnt)
        mss_cnt += 1
        return gr.serialize(format='xml')
    msgdic = get_message_properties(gm)

    if msgdic is None:
        gr = build_message(Graph(), ACL['not-understood'],
                           sender=GestorAgent.uri, msgcnt=mss_cnt)
        mss_cnt += 1
        return gr.serialize(format='xml')

    perf    = msgdic.get('performative')
    content = msgdic.get('content')
    accion  = gm.value(subject=content, predicate=RDF.type) if content else None

    if perf == ACL.request and accion == ECSNS.SolicitudPedido:
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

    elif perf == ACL.request and accion == ECSNS.VerificarCompra:
        factura_id = str(gm.value(subject=content, predicate=ECSNS.idFactura) or '')
        comprador  = str(gm.value(subject=content, predicate=ECSNS.comprador) or '')
        valida, motivo, productos = verificar_compra(factura_id, comprador)
        resp_gr = Graph()
        resp_gr.bind('ecsns', ECSNS)
        res_node = ECSNS['verificacion-' + str(mss_cnt)]
        resp_gr.add((res_node, RDF.type,       ECSNS.ResultadoVerificacion))
        resp_gr.add((res_node, ECSNS.aceptada, Literal(valida)))
        resp_gr.add((res_node, ECSNS.motivo,   Literal(motivo)))
        for i, p in enumerate(productos):
            pn = ECSNS[f'verifprod-{mss_cnt}-{i}']
            resp_gr.add((res_node, ECSNS.tieneProducto, pn))
            resp_gr.add((pn, ECSNS.idProducto, Literal(p.get('id', ''))))
            resp_gr.add((pn, ECSNS.vendedor,   Literal(p.get('vendedor', 'tienda'))))
        gr = build_message(resp_gr, ACL.inform,
                           sender=GestorAgent.uri,
                           receiver=msgdic['sender'],
                           content=res_node,
                           msgcnt=mss_cnt)
        logger.info(f'[GestorPedidos] Verificacion factura {factura_id}: valida={valida}')

    elif perf == ACL.inform and accion == ECSNS.DevolucionAceptada:
        factura_id = str(gm.value(subject=content, predicate=ECSNS.idFactura) or '')
        marcar_factura_devuelta(factura_id)
        gr = build_message(Graph(), ACL.confirm,
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
