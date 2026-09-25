# Arquitectura

DER Flex es un monolito modular. Los adaptadores convierten protocolos externos a un
dominio pequeño; el agregador nunca publica objetos por recurso.

```text
registro físico ─────────┐
                        ├──> normalización ──> almacén de ofertas
RM simulados ──S2/PEBC──┘                         │
                                                 ├──> agregación + privacidad ──> REST
                                                 │
                                                 └──> ledger PostgreSQL ──> outbox durable
                                                                     │          │
                                                                     │          ├──> activación PEBC
                                                                     │          └──> webhooks
OpenADR evento ──> adaptador ────────────────────────────────────────┤
OpenADR reporte <────────────────────────── resultado agregado <─────┘
```

## Límites de módulos

- `domain`: modelos internos, registro físico y almacenes en memoria/PostgreSQL.
- `aggregation`: materializada actualmente en las consultas del almacén; suma capacidad,
  energía y confianza por zona, intervalo y consecuencia.
- `reservations`: asignación determinista, idempotencia y máquina de estados; puede usar
  memoria o un ledger PostgreSQL compartido.
- `adapters/s2`: validación, normalización y generación de instrucciones PEBC.
- `adapters/openadr`: traducción opcional entre eventos, activaciones y reportes.
- `api`: contrato REST; solo usa modelos agregados en las respuestas públicas.
- `simulators`: perfiles sintéticos reproducibles sin hardware.
- `observability`: límites HTTP, correlación, métricas y logs sin datos domésticos.

## Invariantes

1. La capacidad residual nunca es negativa ni supera la oferta elegible.
2. Una misma clave idempotente no representa dos solicitudes distintas.
3. Dos reservas concurrentes no pueden vender dos veces la misma capacidad.
4. Un intervalo público requiere al menos `k` participantes distintos.
5. Ningún contrato público de flexibilidad contiene `resource_id`.
6. El núcleo trabaja en UTC, kW, kWh e intervalos `[inicio, fin)`.
7. S2 solo puede reducir la envolvente física aprovisionada para un recurso habilitado.
8. Una sesión anterior o un reloj fuera de tolerancia no puede recuperar capacidad.
9. Ningún valor no finito atraviesa una frontera de dominio o una respuesta JSON.

## Sustituciones previstas

Las interfaces `OfferStore`, `ResourceRegistry` y `ReservationBackend` conservan una
implementación en memoria para tests y una implementación PostgreSQL para ejecución
durable. PostgreSQL conserva registro físico, posiciones de fuente, ofertas, cohortes,
reservas, asignaciones, idempotencia, activaciones, instrucciones, outbox y supresión pública.
Los bloqueos asesores transaccionales coordinan actualizaciones de oferta y decisiones
de reserva sobre el mismo producto. Los workers de outbox usan leasing y
`SKIP LOCKED`; una caída deja el trabajo recuperable y una entrega puede repetirse.
Todavía faltan OAuth2/OIDC,
autorización por ámbito, gestión de secretos, retención auditada y observabilidad
distribuida.
