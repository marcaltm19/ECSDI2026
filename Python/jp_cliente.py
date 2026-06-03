#!/usr/bin/env python3
"""
jp_cliente.py  —  Script de prueba para los juegos de prueba

Ejecucion:
    python jp_cliente.py --jp 1        # JP1: mas barato gana
    python jp_cliente.py --jp 2        # JP2: prioridad urgente (mas rapido gana)
    python jp_cliente.py --jp 3        # JP3: sin transportistas (fallback)
    python jp_cliente.py --jp 5        # JP5: multiples pedidos simultaneos
    python jp_cliente.py --jp 6        # JP6: pedido multi-centro (2 sub-envios)

Requiere que start_demo.sh este corriendo.
"""

import argparse
import sys
import os
import time
import threading

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from rdflib import Graph, Literal, Namespace, URIRef
from rdflib.namespace import RDF
from AgentUtil.ACL import ACL
from AgentUtil.ACLMessages import build_message
from ontologia import ECSNS
import requests as http_requests

agn = Namespace('http://www.agentes.org#')
LOGISTICO_URL = 'http://192.168.68.121:9003/comm'
mss_cnt = 0


def enviar_pedido(pedido_id, direccion, prioridad, productos):
    global mss_cnt
    g = Graph()
    g.bind('ecsns', ECSNS)

    pedido_uri = agn[pedido_id]
    g.add((pedido_uri, RDF.type, ECSNS.SolicitudPedido))
    g.add((pedido_uri, ECSNS.idPedido, Literal(pedido_id)))
    g.add((pedido_uri, ECSNS.direccion, Literal(direccion)))
    g.add((pedido_uri, ECSNS.prioridad, Literal(prioridad)))

    for i, prod in enumerate(productos):
        prod_uri = agn[f'{pedido_id}-prod{i}']
        g.add((pedido_uri, ECSNS.tieneProducto, prod_uri))
        g.add((prod_uri, ECSNS.idProducto, Literal(prod['id'])))
        g.add((prod_uri, ECSNS.cantidad, Literal(prod.get('cantidad', 1))))
        g.add((prod_uri, ECSNS.peso, Literal(prod.get('peso', 1.0))))

    msg = build_message(g, perf=ACL.request,
                        sender=agn.ClienteTest,
                        receiver=agn.AgenteLogistico,
                        content=pedido_uri,
                        msgcnt=mss_cnt)
    mss_cnt += 1

    try:
        resp = http_requests.get(LOGISTICO_URL,
                                 params={'content': msg.serialize(format='xml')},
                                 timeout=10)
        print(f'  [OK] Pedido {pedido_id} enviado. HTTP {resp.status_code}')
    except Exception as e:
        print(f'  [ERROR] No se pudo enviar {pedido_id}: {e}')


def jp1():
    print('\n=== JP1: El transportista más barato debe ganar ===')
    print('Enviando pedido normal a Barcelona...')
    print('Espera ~20s para que el logistico procese el pedido y veas en los logs quien gana.\n')
    enviar_pedido(
        pedido_id='PED-JP1-001',
        direccion='Barcelona',
        prioridad='normal',
        productos=[{'id': 'PROD-A', 'cantidad': 2, 'peso': 5.0}]
    )
    print('Revisa los logs de AgenteLogistico para ver las ofertas y el ganador (menor precio).')


def jp2():
    print('\n=== JP2: Prioridad URGENTE — debe ganar el más rápido, no el más barato ===')
    print('Enviando pedido urgente a Madrid...')
    print('Espera ~20s. El ganador deberia ser el transportista con menos dias de entrega.\n')
    enviar_pedido(
        pedido_id='PED-JP2-001',
        direccion='Madrid',
        prioridad='urgente',
        productos=[{'id': 'PROD-B', 'cantidad': 1, 'peso': 2.0}]
    )
    print('Revisa los logs de AgenteLogistico: GANADOR debe tener el minimo de dias, no el minimo precio.')


def jp3():
    print('\n=== JP3: Sin transportistas — el sistema no debe bloquearse ===')
    print('ATENCION: Este JP requiere que ningun AgenteTransportista este registrado.')
    print('Para probarlo: para los transportistas con stop_demo.sh, arranca solo DS y Logistico,')
    print('luego ejecuta este script.\n')
    enviar_pedido(
        pedido_id='PED-JP3-001',
        direccion='Valencia',
        prioridad='normal',
        productos=[{'id': 'PROD-C', 'cantidad': 1, 'peso': 3.0}]
    )
    print('El logistico deberia responder con transportista="Desconocido" sin lanzar excepcion.')


def jp5():
    print('\n=== JP5: 3 pedidos simultaneos — cada uno se procesa independientemente ===')
    print('Enviando 3 pedidos a la vez...')
    pedidos = [
        ('PED-JP5-001', 'Barcelona',  'normal',   [{'id': 'P1', 'cantidad': 1, 'peso': 2.0}]),
        ('PED-JP5-002', 'Madrid',     'urgente',  [{'id': 'P2', 'cantidad': 3, 'peso': 8.0}]),
        ('PED-JP5-003', 'Sevilla',    'economica',[{'id': 'P3', 'cantidad': 1, 'peso': 1.0}]),
    ]
    threads = []
    for args in pedidos:
        t = threading.Thread(target=enviar_pedido, args=args)
        threads.append(t)
        t.start()
    for t in threads:
        t.join()
    print('\nRevisa data/listado_pedidos.json y data/listado_envios.json para ver los 3 envios procesados.')
    print('Cada pedido debe haberse asignado a un transportista de forma independiente.')


def jp6():
    print('\n=== JP6: Pedido multi-centro — dos sub-envios ===')
    print('Productos p001 (Madrid) y p002 (Barcelona) en un solo pedido.')
    print('Espera a que el logistico procese; deben aparecer 2 envios en listado_envios.json.\n')
    enviar_pedido(
        pedido_id='PED-JP6-001',
        direccion='Barcelona',
        prioridad='normal',
        productos=[
            {'id': 'p001', 'cantidad': 1, 'peso': 2.1},
            {'id': 'p002', 'cantidad': 1, 'peso': 0.3},
        ],
    )
    print('Revisa data/listado_envios.json: dos envios con el mismo pedido_id y centros distintos.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Juegos de prueba ECSDI 2026')
    parser.add_argument('--jp', type=int, required=True, choices=[1, 2, 3, 5, 6],
                        help='Numero del juego de prueba (1, 2, 3, 5 o 6)')
    parser.add_argument('--host', default='192.168.68.121',
                        help='Host del AgenteLogistico (default: 192.168.68.121)')
    parser.add_argument('--port', type=int, default=9003,
                        help='Puerto del AgenteLogistico (default: 9003)')
    args = parser.parse_args()

    LOGISTICO_URL = f'http://{args.host}:{args.port}/comm'

    jp_map = {1: jp1, 2: jp2, 3: jp3, 5: jp5, 6: jp6}
    jp_map[args.jp]()
