# ADR 009 — Frontera inicial de identidad y tenant en la API

**Estado:** aceptada y completada, 25 de septiembre de 2026.

## Contexto

La publicación experimental permite ejecutar la API sin identidad. El hito 15 exige
separar lectura, reserva y activación, impedir el acceso cruzado y mantener la lógica
de verificación de credenciales fuera del dominio.

## Decisión

La API recibe un `Authenticator` inyectable que produce un principal con sujeto,
tenant, ámbitos y zonas. Las rutas de negocio exigen ámbitos explícitos y filtran o
ocultan zonas ajenas con `404`. Las claves de idempotencia se incluyen en el espacio
del tenant. La implementación estática usa comparación constante y exige propiedad de
zona no solapada entre tenants.

En ejecución persistente, el arranque exige seleccionar explícitamente `oidc` o
`development`. El modo OIDC valida firmas RSA mediante JWKS rotatorio, algoritmo fijado
por configuración, emisor, audiencia, `exp`, `iat`, `nbf`, `sub` y `tenant_id`. Todos
esos claims son obligatorios; los ámbitos y
zonas proceden de claims verificados. La especificación OpenAPI declara autenticación
Bearer en todas las rutas de negocio.

El modo de desarrollo sigue siendo explícitamente mono-tenant y permisivo para no
convertir la demo en un sistema de identidades ficticio. No se presenta como despliegue
seguro.

Cada denegación de autenticación o autorización genera un evento mínimo con decisión,
motivo, método y plantilla de ruta. No registra token, sujeto, tenant, zona, UUID,
payload ni telemetría doméstica.

## Consecuencias

- Esta frontera permite probar la matriz rol×endpoint sin acoplar FastAPI a un proveedor.
- `tenant_id` se conserva en recursos, ofertas, reservas, agregados internos y estado de
  privacidad; la capacidad, los locks y la idempotencia se particionan por tenant.
- Las activaciones heredan su frontera de la reserva y no exponen una ruta sin comprobar
  antes el tenant propietario.
- La ruta administrativa de estado usa el ámbito separado `admin:read`; lectura,
  reserva y activación conservan ámbitos independientes.
- El modo estático queda limitado a pruebas; la demo local debe declarar conscientemente
  `development` si usa PostgreSQL.
- El hito 15 queda cerrado. Persistencia durable, atomicidad multiproceso, privacidad
  formal y validación protocolaria externa pertenecen a hitos posteriores, por lo que
  el veredicto para DER o datos reales continúa siendo `NO-GO`.
