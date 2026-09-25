# ADR 010: outbox transaccional para activaciones

## Estado

Aceptada.

## Contexto

Enviar una instrucción PEBC o un webhook dentro de la transacción de reserva puede dejar
la base de datos y el sistema externo en estados distintos. Un fallo tras el envío pero
antes del commit pierde trazabilidad; un commit seguido de una caída pierde el envío.

## Decisión

La activación, las instrucciones y sus tareas externas se guardan en una sola transacción
PostgreSQL. La activación empieza en `PENDING`. Los workers reclaman tareas mediante
`FOR UPDATE SKIP LOCKED`, incrementan el intento y adquieren un lease. Un worker caído no
bloquea la tarea: otro puede reclamarla al caducar el lease.

Un resultado aceptado queda `DELIVERED`; un rechazo funcional queda `DEAD`; los errores
transitorios pasan a `RETRY` con backoff. Cuando todas las instrucciones son terminales,
la misma transacción calcula el resultado y crea el webhook final. Los webhooks incluyen
`X-DER-Flex-Event-ID`, derivado de forma estable, para deduplicación del consumidor.

## Consecuencias

- Ningún efecto externo se inicia antes del commit que lo describe.
- El trabajo sobrevive reinicios y varias instancias pueden repartirlo sin reclamar la
  misma tarea simultáneamente.
- La semántica es *al menos una vez*: una caída después de que el receptor actúe y antes
  de registrar el resultado puede causar una repetición.
- Los receptores deben deduplicar por identificador de evento; el adaptador S2 real sigue
  siendo un trabajo separado del aceptador sintético de la demo.
