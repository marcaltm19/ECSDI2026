# ECSDI 2025/2026

Ejemplos y codigo para la practica de ECSDI cuatrimestre de primavera 2025/2026

* `/AgentUtil` Clases con funciones utiles para la practica
* `/Examples/AgentExamples` Plantillas para desarrolar agentes + ejemplos
* `/Exammples/Concurrencia` Ejemplos de la documentacion de laboratorio sobre libreria de concurrencia de python
* `/Examples/Distributed` Ejemplos de sistemas distribuidos de las primeras clases de laboratorio
* `/Examples/flask` Ejemplos de la documentacion de laboratorio sobre libreria flask
* `/Examples/InfoSources` Ejemplos de la documentacion sobre fuentes de datos utiles para la practica
* `/Examples/RDFLib` Ejemplos de la documentacion de laboratorio sobre libreria RDFlib
* `/Ontologias` Ejemplos de ontologias
* `/Python` Notebooks introductorios al lenguaje python


## Requisitos
pip install -r requirements.txt

## Orden de arranque
1. python3 Examples/AgentExamples/SimpleDirectoryService.py --port 9000
2. python3 Python/AgenteTransportista.py --nombre TransRapid --port 9010 --dport 9000
3. python3 Python/AgenteTransportista.py --nombre ExpressGo --port 9011 --dport 9000
4. python3 Python/AgenteComprador.py --port 9001 --dport 9000
5. python3 Python/AgenteGestorPedidos.py --port 9002 --dport 9000
6. python3 Python/AgenteLogistico.py --port 9003 --dport 9000
7. python3 Python/AgenteUsuario.py --dport 9000

## Resultados
- Python/data/facturas.json → facturas generadas
- Python/data/envios.json   → envíos asignados a transportista