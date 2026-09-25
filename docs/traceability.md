# Matriz de trazabilidad de auditoría

Esta matriz corresponde a la verificación local de DER Flex 0.1.0. El manifiesto
normativo y procesable es `docs/traceability.json`; este documento sirve como índice
para lectura humana. `scripts/verify_traceability.py` comprueba rutas, nodos pytest,
estados, evidencias y la presencia de los diez claims originales.

| ID | Resultado local | Garantía demostrada | Límite principal |
|---|---|---|---|
| T01 | PASS | Suite instalable desde venv vacía y reproducible cross-OS | Sin identidad pública firmada |
| T02 | PASS | Ruff y mypy limpios | Evidencia offline, sin CI público |
| P01 | PASS acotado | 16 mensajes S2 aceptados | Sin hardware ni certificación externa |
| P02 | PASS acotado | Fixtures y procedencia OpenADR 3.1.0 | Sin Test Tool ni certificación externa |
| PR01 | PASS acotado | Supresión de deltas y ausencia de `resource_id` | Estado de un proceso; no anonimización formal |
| C01 | PASS acotado | Sin doble venta entre hilos | Sin atomicidad multiproceso |
| S01 | PASS | 108 DER, 432 ofertas y 12 agregados | Flota sintética |
| PERF01 | PASS acotado | p95 HTTP/ASGI local menor de 500 ms | Sin red, TLS o persistencia |
| SEC01 | PASS acotado | Auditoría puntual y SBOM transitivo | La base de vulnerabilidades cambia |
| PKG01 | PASS acotado | Imagen Linux ejecutada y endurecida | Falta repetición independiente |
| ORD01 | PASS acotado | Rechazo de replay y sesiones antiguas | High-water mark en memoria |
| PHY01 | PASS acotado | Intersección con envolvente aprovisionada | Registro aún local |
| FZ01 | PASS | 1.205 entradas adversarias reproducibles | Campaña finita |
| MT01 | PASS acotado | 6/6 mutantes críticos eliminados | Catálogo dirigido, no exhaustivo |
| SBOM01 | PASS | 18 componentes y grafo transitivo | Hash de metadato, no de wheel upstream |
| OFF01 | PASS acotado | Instalación offline con hashes en venv nueva | CPython 3.13, Windows/Linux x86-64 |
| BND01 | PASS acotado | Bundle y procedencia offline verificables | Sin firma de autoría; dev offline no vendorizado |

## Interpretación correcta

`PASS_WITH_SCOPE_LIMIT` no se presenta como conformidad de producción. Significa que
la afirmación local indicada es reproducible y que la limitación residual está
registrada en la misma fila. El veredicto para DER reales continúa siendo
`NO-GO` mientras falten persistencia, multi-tenant, estado distribuido y validaciones
externas.
