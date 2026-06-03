import argparse
import json
import logging
import os
import socket
import sys
import time
import uuid
from datetime import date, datetime
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
parser.add_argument('--port', type=int, default=9006)
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

DEVOLUCIONES_PATH = os.path.join(os.path.dirname(__file__), 'data', 'listado_devoluciones.json')
PLAZO_DIAS        = 15

DevolucionAgent = Agent(
    'AgenteDevolucion',
    agn.AgenteDevolucion,
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
gestor_address      = None
experiencia_address = None
usuario_address     = None


def register_message():
    global mss_cnt
    gmess = Graph()
    gmess.bind('foaf', FOAF)
    gmess.bind('dso', DSO)
    reg_obj = agn[DevolucionAgent.name + '-Register']
    gmess.add((reg_obj, RDF.type,        DSO.Register))
    gmess.add((reg_obj, DSO.Uri,         DevolucionAgent.uri))
    gmess.add((reg_obj, FOAF.name,       Literal(DevolucionAgent.name)))
    gmess.add((reg_obj, DSO.Address,     Literal(DevolucionAgent.address)))
    gmess.add((reg_obj, DSO.AgentType,   ECSNS['Ag.Devolucion']))
    gr = send_message(
        build_message(gmess, perf=ACL.request, sender=DevolucionAgent.uri,
                      receiver=DirectoryAgent.uri, content=reg_obj, msgcnt=mss_cnt),
        DirectoryAgent.address,
    )
    mss_cnt += 1
    return gr


def cargar_devoluciones():
    if os.path.exists(DEVOLUCIONES_PATH):
        with open(DEVOLUCIONES_PATH) as f:
            return json.load(f)
    return []


def guardar_devoluciones(devs):
    with open(DEVOLUCIONES_PATH, 'w') as f:
        json.dump(devs, f, indent=2)


def get_gestor_address():
    global mss_cnt, gestor_address
    if gestor_address is not None:
        return gestor_address
    gmess = Graph()
    gmess.bind('dso', DSO)
    search_obj = agn['SearchGestor-' + str(mss_cnt)]
    gmess.add((search_obj, RDF.type,      DSO.Search))
    gmess.add((search_obj, DSO.AgentType, ECSNS['Ag.GestorDePedidos']))
    msg = build_message(gmess, perf=ACL.request, sender=DevolucionAgent.uri,
                        receiver=DirectoryAgent.uri, content=search_obj, msgcnt=mss_cnt)
    mss_cnt += 1
    try:
        r = http_requests.get(DirectoryAgent.address,
                              params={'content': msg.serialize(format='xml')}, timeout=5)
        gr = Graph()
        gr.parse(data=r.text, format='xml')
        for s, p, o in gr:
            if p == DSO.Address:
                gestor_address = str(o)
                return gestor_address
    except Exception as e:
        logger.warning(f'[Devolucion] No se pudo localizar AgenteGestorPedidos: {e}')
    return None


def get_experiencia_address():
    global mss_cnt, experiencia_address
    if experiencia_address is not None:
        return experiencia_address
    gmess = Graph()
    gmess.bind('dso', DSO)
    search_obj = agn['SearchExp-' + str(mss_cnt)]
    gmess.add((search_obj, RDF.type,      DSO.Search))
    gmess.add((search_obj, DSO.AgentType, ECSNS['Ag.Experiencia']))
    msg = build_message(gmess, perf=ACL.request, sender=DevolucionAgent.uri,
                        receiver=DirectoryAgent.uri, content=search_obj, msgcnt=mss_cnt)
    mss_cnt += 1
    try:
        r = http_requests.get(DirectoryAgent.address,
                              params={'content': msg.serialize(format='xml')}, timeout=5)
        gr = Graph()
        gr.parse(data=r.text, format='xml')
        for s, p, o in gr:
            if p == DSO.Address:
                experiencia_address = str(o)
                return experiencia_address
    except Exception as e:
        logger.warning(f'[Devolucion] No se pudo localizar AgenteExperiencia: {e}')
    return None


def notificar_experiencia_devolucion(comprador, factura_id):
    global mss_cnt
    addr = get_experiencia_address()
    if addr is None:
        logger.warning('[Devolucion] AgenteExperiencia no disponible, no se notifica la devolución')
        return
    gmess = Graph()
    gmess.bind('ecsns', ECSNS)
    node = ECSNS['devolucion-notif-' + str(mss_cnt)]
    gmess.add((node, RDF.type,        ECSNS.DevolucionAceptada))
    gmess.add((node, ECSNS.comprador, Literal(comprador)))
    gmess.add((node, ECSNS.idFactura, Literal(factura_id)))
    try:
        send_message(
            build_message(gmess, perf=ACL.inform, sender=DevolucionAgent.uri,
                          receiver=agn.AgenteExperiencia, content=node, msgcnt=mss_cnt),
            addr,
        )
        mss_cnt += 1
        logger.info(f'[Devolucion] Experiencia notificada: eliminar compra {factura_id} de {comprador}')
    except Exception as e:
        mss_cnt += 1
        logger.warning(f'[Devolucion] Error notificando a AgenteExperiencia: {e}')


def verificar_compra_con_gestor(factura_id, comprador):
    global mss_cnt
    addr = get_gestor_address()
    if addr is None:
        return False, 'AgenteGestorPedidos no disponible', []
    gmess = Graph()
    gmess.bind('ecsns', ECSNS)
    node = ECSNS['verificar-' + str(mss_cnt)]
    gmess.add((node, RDF.type,        ECSNS.VerificarCompra))
    gmess.add((node, ECSNS.idFactura, Literal(factura_id)))
    gmess.add((node, ECSNS.comprador, Literal(comprador)))
    try:
        resp = send_message(
            build_message(gmess, perf=ACL.request, sender=DevolucionAgent.uri,
                          receiver=agn.AgenteGestorPedidos, content=node, msgcnt=mss_cnt),
            addr,
        )
        mss_cnt += 1
        for s in resp.subjects(RDF.type, ECSNS.ResultadoVerificacion):
            aceptada_lit = resp.value(s, ECSNS.aceptada)
            aceptada = aceptada_lit.toPython() if aceptada_lit is not None else False
            motivo   = str(resp.value(s, ECSNS.motivo) or '')
            productos = [
                {
                    'id':      str(resp.value(pn, ECSNS.idProducto) or ''),
                    'vendedor': str(resp.value(pn, ECSNS.vendedor)  or 'tienda'),
                }
                for pn in resp.objects(s, ECSNS.tieneProducto)
            ]
            return aceptada, motivo, productos
        return False, 'Respuesta inesperada del gestor de pedidos', []
    except Exception as e:
        mss_cnt += 1
        logger.warning(f'[Devolucion] Error al verificar compra con gestor: {e}')
        return False, 'Error al contactar con el gestor de pedidos', []


def _buscar_address_vendedor_externo(nombre_vendedor):
    global mss_cnt
    gmess = Graph()
    gmess.bind('dso', DSO)
    search_obj = agn['SearchVend-' + str(mss_cnt)]
    gmess.add((search_obj, RDF.type,      DSO.Search))
    gmess.add((search_obj, DSO.AgentType, ECSNS['Ag.VendedorExterno']))
    msg = build_message(gmess, perf=ACL.request, sender=DevolucionAgent.uri,
                        receiver=DirectoryAgent.uri, content=search_obj, msgcnt=mss_cnt)
    mss_cnt += 1
    try:
        r = http_requests.get(DirectoryAgent.address,
                              params={'content': msg.serialize(format='xml')}, timeout=5)
        gr = Graph()
        gr.parse(data=r.text, format='xml')
        for entry in gr.subjects(DSO.Uri):
            uri  = gr.value(entry, DSO.Uri)
            addr = gr.value(entry, DSO.Address)
            if uri and str(uri).endswith(nombre_vendedor) and addr:
                return str(addr)
    except Exception as e:
        logger.warning(f'[Devolucion] Error buscando vendedor {nombre_vendedor}: {e}')
    return None


def notificar_devolucion_a_vendedores(factura_id, comprador, productos):
    global mss_cnt
    vendedores_externos = {
        p['vendedor'] for p in productos
        if p.get('vendedor', 'tienda') != 'tienda'
    }
    for nombre_vendedor in vendedores_externos:
        addr = _buscar_address_vendedor_externo(nombre_vendedor)
        if addr is None:
            logger.warning(f'[Devolucion] Vendedor externo {nombre_vendedor} no encontrado en DS')
            continue
        gmess = Graph()
        gmess.bind('ecsns', ECSNS)
        node = ECSNS['dev-ext-' + str(mss_cnt)]
        gmess.add((node, RDF.type,        ECSNS.PedirReembolso))
        gmess.add((node, ECSNS.idFactura, Literal(factura_id)))
        gmess.add((node, ECSNS.comprador, Literal(comprador)))
        for p in productos:
            if p.get('vendedor', 'tienda') == nombre_vendedor:
                pn = ECSNS['devprod-' + str(mss_cnt) + '-' + p['id']]
                gmess.add((node, ECSNS.tieneProducto, pn))
                gmess.add((pn, ECSNS.idProducto, Literal(p['id'])))
        try:
            send_message(
                build_message(gmess, perf=ACL.request, sender=DevolucionAgent.uri,
                              content=node, msgcnt=mss_cnt),
                addr,
            )
            mss_cnt += 1
            logger.info(f'[Devolucion] Devolución notificada a {nombre_vendedor}')
        except Exception as e:
            mss_cnt += 1
            logger.warning(f'[Devolucion] Error notificando devolución a {nombre_vendedor}: {e}')


def get_usuario_address():
    global mss_cnt, usuario_address
    if usuario_address is not None:
        return usuario_address
    gmess = Graph()
    gmess.bind('dso', DSO)
    search_obj = agn['SearchUser-' + str(mss_cnt)]
    gmess.add((search_obj, RDF.type,      DSO.Search))
    gmess.add((search_obj, DSO.AgentType, ECSNS['Ag.Usuario']))
    msg = build_message(gmess, perf=ACL.request, sender=DevolucionAgent.uri,
                        receiver=DirectoryAgent.uri, content=search_obj, msgcnt=mss_cnt)
    mss_cnt += 1
    try:
        r = http_requests.get(DirectoryAgent.address,
                              params={'content': msg.serialize(format='xml')}, timeout=5)
        gr = Graph()
        gr.parse(data=r.text, format='xml')
        for s, p, o in gr:
            if p == DSO.Address:
                usuario_address = str(o)
                return usuario_address
    except Exception as e:
        logger.warning(f'[Devolucion] No se pudo localizar AgenteUsuario: {e}')
    return None


def notificar_gestor_devolucion_aceptada(factura_id, comprador):
    """Tells GestorPedidos to mark the invoice as returned."""
    global mss_cnt
    addr = get_gestor_address()
    if addr is None:
        logger.warning('[Devolucion] GestorPedidos no disponible para marcar factura devuelta')
        return
    gmess = Graph()
    gmess.bind('ecsns', ECSNS)
    node = ECSNS['dev-aceptada-gestor-' + str(mss_cnt)]
    gmess.add((node, RDF.type,        ECSNS.DevolucionAceptada))
    gmess.add((node, ECSNS.idFactura, Literal(factura_id)))
    gmess.add((node, ECSNS.comprador, Literal(comprador)))
    try:
        send_message(
            build_message(gmess, perf=ACL.inform, sender=DevolucionAgent.uri,
                          receiver=agn.AgenteGestorPedidos, content=node, msgcnt=mss_cnt),
            addr,
        )
        mss_cnt += 1
    except Exception as e:
        mss_cnt += 1
        logger.warning(f'[Devolucion] Error notificando GestorPedidos para marcar factura: {e}')


def notificar_usuario_devolucion(comprador, dev_id, motivo, empresa):
    """Sends InformarDesicion to AgenteUsuario with the accepted return details."""
    global mss_cnt
    addr = get_usuario_address()
    if addr is None:
        logger.warning('[Devolucion] AgenteUsuario no disponible para notificar devolución')
        return
    gmess = Graph()
    gmess.bind('ecsns', ECSNS)
    node = ECSNS['dev-decision-' + dev_id]
    gmess.add((node, RDF.type,               ECSNS.InformarDesicion))
    gmess.add((node, ECSNS.comprador,        Literal(comprador)))
    gmess.add((node, ECSNS.idDevolucion,     Literal(dev_id)))
    gmess.add((node, ECSNS.motivoDevolucion, Literal(motivo)))
    if empresa:
        gmess.add((node, ECSNS.empresaMensajeria, Literal(empresa)))
    try:
        send_message(
            build_message(gmess, perf=ACL.inform, sender=DevolucionAgent.uri,
                          receiver=agn.AgenteUsuario, content=node, msgcnt=mss_cnt),
            addr,
        )
        mss_cnt += 1
        logger.info(f'[Devolucion] AgenteUsuario notificado — devolución {dev_id} aceptada')
    except Exception as e:
        mss_cnt += 1
        logger.warning(f'[Devolucion] Error notificando AgenteUsuario devolución: {e}')


def _parse_fecha_recepcion(fecha_str):
    """Interpreta YYYY-MM-DD (formulario) o ISO completo."""
    s = (fecha_str or '').strip()
    if not s:
        raise ValueError('fecha vacía')
    if 'T' in s:
        return datetime.fromisoformat(s).replace(hour=0, minute=0, second=0, microsecond=0)
    return datetime.combine(date.fromisoformat(s[:10]), datetime.min.time())


def evaluar_devolucion(razon, fecha_recepcion_str):
    razon_lower = razon.lower()
    siempre = ['defectuoso', 'defecto', 'equivocado', 'incorrecto', 'roto', 'danado', 'dañado']
    if any(r in razon_lower for r in siempre):
        return True, 'Devolución aceptada: producto defectuoso o equivocado', 'MensajeriaRapida S.L.'
    try:
        fecha_rec = _parse_fecha_recepcion(fecha_recepcion_str)
        hoy = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        if fecha_rec > hoy:
            return False, 'Devolución rechazada: la fecha de recepción no puede ser futura', None
        dias = (hoy - fecha_rec).days
        if dias <= PLAZO_DIAS:
            return True, f'Devolución aceptada: dentro del plazo ({dias} días)', 'MensajeriaEstandar S.A.'
        return False, (
            f'Devolución rechazada: fuera del plazo de {PLAZO_DIAS} días '
            f'({dias} días desde la recepción)'
        ), None
    except (ValueError, TypeError):
        return False, 'Fecha de recepción inválida', None


def procesar_solicitud(gm, content):
    comprador       = str(gm.value(content, ECSNS.comprador)       or 'Anonimo')
    factura_id      = str(gm.value(content, ECSNS.idFactura)        or '')
    razon           = str(gm.value(content, ECSNS.razonDevolucion)  or 'insatisfaccion')
    fecha_recepcion = str(gm.value(content, ECSNS.fechaRecepcion)   or datetime.now().isoformat())

    valida, motivo_verificacion, productos = verificar_compra_con_gestor(factura_id, comprador)
    if not valida:
        aceptada, motivo, empresa = False, motivo_verificacion, None
    else:
        aceptada, motivo, empresa = evaluar_devolucion(razon, fecha_recepcion)

    dev_id = 'DEV-' + str(uuid.uuid4())[:8].upper()
    devs   = cargar_devoluciones()
    devs.append({
        'id': dev_id, 'comprador': comprador, 'factura_id': factura_id,
        'razon': razon, 'fecha_solicitud': datetime.now().isoformat(),
        'fecha_recepcion': fecha_recepcion, 'aceptada': aceptada,
        'motivo': motivo, 'empresa_mensajeria': empresa,
    })
    guardar_devoluciones(devs)
    if aceptada:
        notificar_gestor_devolucion_aceptada(factura_id, comprador)
        notificar_experiencia_devolucion(comprador, factura_id)
        notificar_usuario_devolucion(comprador, dev_id, motivo, empresa)
        notificar_devolucion_a_vendedores(factura_id, comprador, productos)
    logger.info(f'[Devolucion] {dev_id} — aceptada={aceptada} — {motivo}')

    gr = Graph()
    gr.bind('ecsns', ECSNS)
    node = ECSNS['devolucion-' + dev_id]
    gr.add((node, RDF.type,               ECSNS.Devolucion))
    gr.add((node, ECSNS.idDevolucion,     Literal(dev_id)))
    gr.add((node, ECSNS.aceptada,         Literal(aceptada)))
    gr.add((node, ECSNS.motivoDevolucion, Literal(motivo)))
    if empresa:
        gr.add((node, ECSNS.empresaMensajeria, Literal(empresa)))
    return gr


def procesar_consulta(gm, content):
    comprador = str(gm.value(content, ECSNS.comprador) or '')
    devs = cargar_devoluciones()
    if comprador:
        devs = [d for d in devs if d.get('comprador') == comprador]

    gr = Graph()
    gr.bind('ecsns', ECSNS)
    lista = ECSNS['listaDevoluciones']
    gr.add((lista, RDF.type, ECSNS.ListaDevoluciones))
    for d in devs:
        node = ECSNS['devolucion-' + d['id']]
        gr.add((lista, ECSNS.tieneDevolucion,  node))
        gr.add((node, ECSNS.idDevolucion,      Literal(d['id'])))
        gr.add((node, ECSNS.aceptada,          Literal(d['aceptada'])))
        gr.add((node, ECSNS.motivoDevolucion,  Literal(d['motivo'])))
        if d.get('empresa_mensajeria'):
            gr.add((node, ECSNS.empresaMensajeria, Literal(d['empresa_mensajeria'])))
    return gr


@app.route('/stop')
def stop():
    cola1.put(0)
    shutdown_server()
    return 'Parando AgenteDevolucion'


@app.route('/comm', methods=['GET', 'POST'])
def comunicacion():
    global mss_cnt
    logger.info('[Devolucion] Mensaje recibido')
    message = request.args.get('content') or request.form.get('content')
    gm = Graph()
    gm.parse(data=message, format='xml')
    msgdic = get_message_properties(gm)

    if msgdic is None or msgdic.get('performative') != ACL.request:
        gr = build_message(Graph(), ACL['not-understood'],
                           sender=DevolucionAgent.uri, msgcnt=mss_cnt)
    else:
        content = msgdic.get('content')
        accion  = gm.value(subject=content, predicate=RDF.type)

        if accion == ECSNS.SolicitudDevolucion:
            resp = procesar_solicitud(gm, content)
            gr   = build_message(resp, ACL.inform, sender=DevolucionAgent.uri,
                                 receiver=msgdic['sender'], msgcnt=mss_cnt)
        elif accion == ECSNS.ConsultaDevoluciones:
            resp = procesar_consulta(gm, content)
            gr   = build_message(resp, ACL.inform, sender=DevolucionAgent.uri,
                                 receiver=msgdic['sender'], msgcnt=mss_cnt)
        else:
            gr = build_message(Graph(), ACL['not-understood'],
                               sender=DevolucionAgent.uri, msgcnt=mss_cnt)

    mss_cnt += 1
    return gr.serialize(format='xml')


def agentbehavior1(cola):
    register_message()
    logger.info(f'[Devolucion] Registrado en puerto {port}')
    fin = False
    while not fin:
        time.sleep(1)
        if not cola.empty() and cola.get() == 0:
            fin = True


if __name__ == '__main__':
    os.makedirs(os.path.join(os.path.dirname(__file__), 'data'), exist_ok=True)
    ab1 = Process(target=agentbehavior1, args=(cola1,))
    ab1.start()
    app.run(host=flask_host, port=port)
    ab1.join()
    logger.info('[Devolucion] Fin')
