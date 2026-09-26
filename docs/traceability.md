# Matriz de trazabilidad de auditoría

Esta matriz corresponde a la verificación local de DER Flex 0.2.0. El manifiesto
normativo y procesable es `docs/traceability.json`; este documento sirve como índice
para lectura humana. `scripts/verify_traceability.py` comprueba rutas, nodos pytest,
estados, evidencias y la presencia de los diez claims originales.

| ID | Resultado local | Garantía demostrada | Límite principal |
|---|---|---|---|
| T01 | PASS | Suite instalable desde venv vacía y reproducible cross-OS | Sin identidad pública firmada |
| T02 | PASS | Ruff y mypy limpios | Evidencia offline, sin CI público |
| P01 | PASS acotado | 16 mensajes S2 aceptados | Sin hardware ni certificación externa |
| P02 | PASS acotado | Fixtures y procedencia OpenADR 3.1.0 | Sin Test Tool ni certificación externa |
| PR01 | PASS acotado | Supresión durable de deltas y ausencia de `resource_id` | Memoria local; no anonimización formal |
| C01 | PASS acotado | Sin doble venta entre procesos con PostgreSQL | Backend en memoria limitado a un proceso |
| S01 | PASS | 108 DER, 432 ofertas y 12 agregados | Flota sintética |
| PERF01 | PASS acotado | p95 HTTP/ASGI local menor de 500 ms | Sin red, TLS o persistencia |
| SEC01 | PASS acotado | Auditoría puntual y SBOM transitivo | La base de vulnerabilidades cambia |
| PKG01 | PASS acotado | Imagen Linux ejecutada y endurecida | Falta repetición independiente |
| ORD01 | PASS acotado | Rechazo durable de replay y sesiones antiguas | Memoria sigue siendo local |
| PHY01 | PASS acotado | Intersección con envolvente aprovisionada durable | Sin fuente operativa externa |
| FZ01 | PASS | 1.205 entradas adversarias reproducibles | Campaña finita |
| MT01 | PASS acotado | 6/6 mutantes críticos eliminados | Catálogo dirigido, no exhaustivo |
| SBOM01 | PASS | 22 componentes y grafo transitivo | Hash de metadato, no de wheel upstream |
| OFF01 | PASS acotado | Instalación offline con hashes en venv nueva | CPython 3.13, Windows/Linux x86-64 |
| OUT01 | PASS acotado | Outbox transaccional recuperable y concurrente | Al menos una vez; adaptador S2 real pendiente |
| BND01 | PASS acotado | Bundle y procedencia offline verificables | Sin firma de autoría; dev offline no vendorizado |
| AUTH01 | PASS acotado | OIDC/JWT, rotación, ámbitos y aislamiento tenant/zonas | IdP operativo no certificado en esta revisión local |
| PRIV02 | PASS acotado | Snapshots, cadencia, cuantización y presupuesto tenant compartido | Sin ε-DP ni anonimización formal; revisión externa pendiente |

## Interpretación correcta

`PASS_WITH_SCOPE_LIMIT` no se presenta como conformidad de producción. Significa que
la afirmación local indicada es reproducible y que la limitación residual está
registrada en la misma fila. El hito 15 de autenticación y aislamiento queda cerrado
localmente. El veredicto para DER reales continúa siendo `NO-GO` mientras falten los
hitos de privacidad formal, operación distribuida completa y validaciones externas.
