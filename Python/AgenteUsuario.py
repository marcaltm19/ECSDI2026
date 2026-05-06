import argparse
import socket
import sys
import os

from rdflib import Graph, Literal, Namespace
from rdflib.namespace import RDF

import requests as http_requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from AgentUtil.ACL import ACL
from AgentUtil.ACLMessages import build_message, get_message_properties, send_message
from AgentUtil.Agent import Agent
from AgentUtil.DSO import DSO
from ontologia import ECSNS

parser = argparse.ArgumentParser()
parser.add_argument('--dhost', default=None)
parser.add_argument('--dport', type=int, default=9000)
args = parser.parse_args()

agn = Namespace('http://www.agentes.org#')
hostname = socket.gethostname()
dhostname = args.dhost if args.dhost else hostname
dport = args.dport
mss_cnt = 0

DirectoryAgent = Agent(
    'DirectoryAgent',
    agn.Directory,
    'http://%s:%d/Register' % (dhostname, dport),
    'http://%s:%d/Stop' % (dhostname, dport),
)
UsuarioAgent = Agent(
    'AgenteUsuario',
    agn.AgenteUsuario,
    'http://%s:9010/comm' % hostname,
    'http://%s:9010/Stop' % hostname,
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


def test_busqueda():
    print('\n=== TEST 1: Búsqueda de productos ===')
    addr = get_agent_address(ECSNS['Ag.Comprador'])
    if not addr:
        print('[ERROR] No se encontró el AgenteComprador en el DS')
        return

    gmess = Graph()
    gmess.bind('ecsns', ECSNS)
    busq = ECSNS['busqueda-1']
    gmess.add((busq, RDF.type,                ECSNS.Busqueda))
    gmess.add((busq, ECSNS.precioMaximo,      Literal(200.0)))
    gmess.add((busq, ECSNS.categoria,         Literal('Electronica')))
    gmess.add((busq, ECSNS.valoracionMinima,  Literal(4.0)))

    gr = send_message(
        build_message(gmess, perf=ACL.request, sender=UsuarioAgent.uri,
              receiver=agn.AgenteComprador, content=busq, msgcnt=0),
        addr,
    )

    print('Productos encontrados:')
    for s, p, o in gr:
        if p == ECSNS.nombre:
            nombre = str(o)
            precio = str(gr.value(s, ECSNS.precio))
            val    = str(gr.value(s, ECSNS.valoracion))
            print(f'  - {nombre} | {precio}€ | valoración: {val}')


def test_pedido():
    print('\n=== TEST 2: Realizar pedido ===')
    addr = get_agent_address(ECSNS['Ag.GestorDePedidos'])
    if not addr:
        print('[ERROR] No se encontró el AgenteGestorPedidos en el DS')
        return

    gmess = Graph()
    gmess.bind('ecsns', ECSNS)
    ped = ECSNS['pedido-test-1']
    gmess.add((ped, RDF.type,           ECSNS.Pedido))
    gmess.add((ped, ECSNS.comprador,    Literal('Marc')))
    gmess.add((ped, ECSNS.direccion,    Literal('Carrer de Pau Claris 10, Barcelona')))
    gmess.add((ped, ECSNS.prioridad,    Literal('urgente')))
    gmess.add((ped, ECSNS.metodoPago,   Literal('tarjeta')))

    # Producto 1
    p1 = ECSNS['ped-prod-1']
    gmess.add((ped, ECSNS.tieneProducto, p1))
    gmess.add((p1,  ECSNS.idProducto,    Literal('p002')))
    gmess.add((p1,  ECSNS.nombre,        Literal('Auriculares Bluetooth')))
    gmess.add((p1,  ECSNS.precio,        Literal(89.99)))
    gmess.add((p1,  ECSNS.cantidad,      Literal(2)))
    gmess.add((p1,  ECSNS.peso,          Literal(0.3)))

    # Producto 2
    p2 = ECSNS['ped-prod-2']
    gmess.add((ped, ECSNS.tieneProducto, p2))
    gmess.add((p2,  ECSNS.idProducto,    Literal('p005')))
    gmess.add((p2,  ECSNS.nombre,        Literal('Teclado Mecanico RGB')))
    gmess.add((p2,  ECSNS.precio,        Literal(110.0)))
    gmess.add((p2,  ECSNS.cantidad,      Literal(1)))
    gmess.add((p2,  ECSNS.peso,          Literal(1.1)))

    gr = send_message(
        build_message(gmess, perf=ACL.request, sender=UsuarioAgent.uri,
              receiver=agn.AgenteGestorPedidos, content=ped, msgcnt=1),
        addr,
    )

    print('Respuesta del GestorPedidos:')
    for s, p, o in gr:
        if p == ECSNS.idFactura:
            print(f'  Factura ID: {o}')
        if p == ECSNS.total:
            print(f'  Total: {o}€')
        if p == ECSNS.fecha:
            print(f'  Fecha: {o}')


if __name__ == '__main__':
    test_busqueda()
    test_pedido()
    print('\n=== Tests completados ===')
    print('Revisa Python/data/facturas.json y Python/data/pedidos.json para ver los resultados')