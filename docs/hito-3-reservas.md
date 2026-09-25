# Hito 3 — Reserva y activación

**Estado:** completado el 12 de septiembre de 2026.

## Entregado

- Reserva atómica de potencia ascendente o descendente por zona e intervalo.
- Distribución determinista entre recursos elegibles.
- Claves `Idempotency-Key` con detección de reutilización incompatible.
- Prevención de doble venta mediante sección crítica y capacidad por recurso.
- Descuento inmediato de reservas sobre `GET /flexibility`.
- Cancelación y caducidad de reservas.
- Activación idempotente y generación de una instrucción `PEBC.Instruction` válida por recurso asignado.
- Consulta de reservas y activaciones.
- Errores de conflicto en formato `application/problem+json`.
- Protección del tamaño de cohorte: la respuesta muestra participantes elegibles, no el subconjunto pequeño utilizado por el algoritmo de asignación.
- Webhooks firmados con HMAC-SHA256, tres reintentos acotados y registro del resultado.
- Eventos `activation.accepted`, `activation.started`, `activation.completed` y `activation.failed`.
- `correlation_id` estable desde la reserva hasta la activación y sus eventos.
- Rechazo parcial simulado: solo la potencia aceptada permanece descontada; la parte rechazada vuelve a estar disponible y la activación queda como `FAILED`.

## Endpoints añadidos

- `POST /api/v1/reservations`
- `GET /api/v1/reservations/{reservation_id}`
- `DELETE /api/v1/reservations/{reservation_id}`
- `POST /api/v1/reservations/{reservation_id}/activate`
- `GET /api/v1/activations/{activation_id}`

## Límites deliberados del MVP

- Los reintentos son inmediatos y en memoria; producción necesitará backoff, cola duradera y dead-letter queue.
- La URL del webhook se valida como HTTP(S), pero producción necesitará defensa SSRF y lista de destinos autorizados.
- El secreto compartido es de desarrollo; producción necesitará rotación y gestión externa de secretos.
