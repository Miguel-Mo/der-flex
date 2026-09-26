# Hito 17 — Rendimiento y resiliencia de la arquitectura real

**Estado:** completado localmente el 26 de septiembre de 2026.

## Problema cerrado

La única medida anterior atravesaba ASGI en memoria. El nuevo gate ejecuta la pila a
través de una conexión TLS real, dos workers API, PostgreSQL separado y dos workers de
outbox. También fuerza saturación y una caída temporal de la base de datos.

## Ejecución

```powershell
.\.venv\Scripts\python.exe scripts\verify_pilot_slo.py
```

El comando crea y elimina infraestructura efímera, escribe
`build/pilot-slo-report.json` y no utiliza la base de datos de desarrollo.

## Criterios automáticos

| Señal | Límite local |
|---|---:|
| Consulta de flexibilidad p95 | ≤ 750 ms |
| Reserva p95 aceptada | ≤ 1.500 ms |
| Activación p95 | ≤ 1.500 ms |
| Detección de PostgreSQL caído | ≤ 6.500 ms |

Además exige:

- respuestas sostenidas sin error antes de la ráfaga;
- shedding observable con `503` durante saturación;
- suma reservada no superior a capacidad;
- snapshot público idéntico antes y después de reservas y recuperación;
- backlog durable creado con los workers detenidos y vaciado por dos workers;
- ningún ID de evento duplicado ni activación pendiente;
- liveness `200`, readiness y negocio `503` durante la caída, y recuperación a `200`;
- histogramas, gauge de peticiones activas y contador de shedding en `/metrics`.

## Primera ejecución conforme

- 72 peticiones en 2,410 s: 29,875 solicitudes/s;
- flexibilidad p95: 176,830 ms;
- reserva p95: 400,515 ms;
- activación p95: 141,228 ms;
- 24 peticiones rechazadas de forma controlada durante la ráfaga;
- backlog: 8 `PENDING` → 8 `DELIVERED`;
- capacidad: 156,2 kW; asignación: 1,6 kW; sin sobreventa;
- detección de PostgreSQL pausado: 5.015,404 ms;
- snapshot estable y recuperación completa.

Son resultados de esta máquina y no un compromiso universal. El informe JSON conserva
los valores exactos de cada ejecución y el gate compara cada nueva ejecución con los
límites, no con esos números concretos.
