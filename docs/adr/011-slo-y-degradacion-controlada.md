# ADR-011: SLO local y degradación controlada

- Estado: aceptada para el piloto técnico local
- Fecha: 2026-09-26

## Contexto

El benchmark ASGI en memoria no recorre TLS, procesos distintos, PostgreSQL ni la
outbox. Tampoco demuestra qué sucede al saturar la API o perder una dependencia. Un
promedio aislado puede ocultar colas largas, doble venta o pérdida de privacidad.

## Decisión

Se añade una puerta desplegada y reproducible con esta topología:

- TLS local con certificado efímero;
- dos workers Uvicorn y un límite de cuatro peticiones de negocio activas por worker;
- PostgreSQL en un contenedor Linux separado;
- dos workers de outbox independientes;
- carga sostenida de consultas y una ráfaga concurrente de reservas y activaciones;
- pausa de PostgreSQL para comprobar detección, respuesta y recuperación.

Los objetivos locales son p95 inferior a 750 ms para consultas, e inferior a 1.500 ms
para reservas y activaciones. La indisponibilidad de PostgreSQL debe detectarse antes de
6.500 ms. Una sobrecarga se rechaza inmediatamente con `503` y `Retry-After`; health y
métricas permanecen accesibles. Los errores del driver se transforman en un mensaje
sanitizado sin DSN ni diagnóstico SQL.

Las métricas exportan contador, histograma pormenorizable, peticiones activas y shedding
sin etiquetas de recurso, tenant o zona. Cada proceso conserva su propio registro; un
despliegue operativo debe agregarlos mediante su sistema de monitorización.

Las migraciones se serializan con un advisory lock transaccional global. Esto evita que
varios workers recién arrancados creen simultáneamente los mismos objetos PostgreSQL.

## Consecuencias y límites

La puerta falla si se excede un SLO, si no aparece backpressure, si se vende más capacidad
de la disponible, si se duplican IDs de evento, si queda una activación pendiente, si no
se vacía el backlog o si cambia el snapshot público durante carga o recuperación.

El certificado, el IdP y la red son fixtures locales. La prueba es una regresión acotada,
no un dimensionamiento, un ensayo multi-host prolongado ni un SLO contractual. Las
particiones se aproximan pausando el contenedor PostgreSQL en el mismo host.
