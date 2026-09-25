# ADR 009 — Frontera inicial de identidad y tenant en la API

**Estado:** aceptada como incremento parcial, 25 de septiembre de 2026.

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

El modo de desarrollo sigue siendo explícitamente mono-tenant y permisivo para no
convertir la demo en un sistema de identidades ficticio. No se presenta como despliegue
seguro.

## Consecuencias y trabajo pendiente

- Esta frontera permite probar la matriz rol×endpoint sin acoplar FastAPI a un proveedor.
- `tenant_id` se conserva en recursos, ofertas, reservas, agregados internos y estado de
  privacidad; la capacidad, los locks y la idempotencia se particionan por tenant.
- Las activaciones heredan su frontera de la reserva y no exponen una ruta sin comprobar
  antes el tenant propietario.
- Falta un verificador OIDC/JWT con emisor, audiencia, caducidad, rotación y claves
  públicas. Los tokens estáticos no cierran el hito 15.
- Autenticación administrativa, auditoría de rechazos y gestión segura de secretos siguen
  pendientes. Por tanto, el veredicto para DER o datos reales continúa siendo `NO-GO`.
