# DER Flex

API abierta de agregación de flexibilidad para recursos energéticos distribuidos.
`v0.2.0` es la publicación experimental actual. Añade un ledger PostgreSQL durable
para reservas y activaciones sobre la primera versión experimental `v0.1.0`.

## Estado ejecutable

Existe una sesión S2/PEBC mínima y determinista entre un Resource Manager (RM) de batería y un Customer Energy Manager (CEM). Intercambia handshake, detalles del recurso, selección PEBC, restricciones, pronóstico e instrucción sobre un WebSocket local. Cada payload se valida con `s2-python`, generado a partir de S2 JSON.

La demo actual contiene 108 recursos —baterías, cargadores VE y bombas de calor— en tres zonas durante una hora simulada. Incluye normalización y remuestreo PEBC, almacenamiento en memoria, agregación con `k=10`, reservas atómicas, activación, webhooks, métricas y una API FastAPI con OpenAPI.

La ingestión rechaza ofertas fuera de orden mediante una secuencia monotónica y limita
cada mensaje PEBC a la envolvente física aprovisionada para el recurso. Un mensaje S2
no puede ampliar por sí solo la potencia, energía acumulada ni velocidad de rampa
admisibles. Los límites proceden de un registro versionado independiente; los recursos
desconocidos, deshabilitados o en otra zona y los pronósticos que cruzan los límites
energéticos se rechazan. La rampa se evalúa contra un tiempo de respuesta explícito,
de un minuto por defecto. Los cálculos de energía solo ajustan errores binarios dentro
de una tolerancia conservadora de `10⁻⁹ kWh`.

El orden temporal usa la pareja `(source_epoch, source_sequence)`: una sesión posterior
puede reiniciar su contador, pero una sesión antigua no puede recuperar capacidad. Las
observaciones admiten por defecto hasta 15 minutos de antigüedad y dos minutos de sesgo
futuro respecto al reloj local de recepción.

Las entradas numéricas no admiten `NaN` ni infinitos. El registro limita cada recurso a
±100 MW, 1 GWh y 100 MW/min; una reserva agregada no puede solicitar más de 1 GW. Son
techos defensivos del MVP, no capacidades anunciadas por defecto.

Requisitos: Python 3.13.

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -c requirements-runtime.constraints -e ".[dev]"
.\.venv\Scripts\python.exe scripts\s2_pebc_smoke.py
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe scripts\verify_release.py
.\.venv\Scripts\python.exe scripts\build_review_bundle.py
.\.venv\Scripts\der-flex-demo.exe
```

O bien, con un único comando:

```powershell
docker compose up --build
```

El valor de `DER_FLEX_WEBHOOK_SECRET` debe configurarse fuera de la demo y ser idéntico
en la API y todos los workers; el valor por defecto de Compose es solo local.

Con PostgreSQL, `DER_FLEX_AUTH_MODE` es obligatorio: `oidc` exige además
`DER_FLEX_OIDC_ISSUER`, `DER_FLEX_OIDC_AUDIENCE` y `DER_FLEX_OIDC_JWKS_URL`, todos con
HTTPS. Compose declara conscientemente `development` para la demo local. Ese modo no
debe usarse con DER ni datos reales. OIDC valida firma RSA, emisor, audiencia,
caducidad, tenant, ámbitos y zonas, y admite rotación mediante `kid`/JWKS.

Compose arranca PostgreSQL, la API y un worker de outbox, y configura la demo para persistir el registro físico,
ofertas normalizadas, posiciones de sesión, cohortes públicas, reservas, asignaciones,
claves idempotentes, activaciones, instrucciones, entregas pendientes y el estado de
supresión. El esquema
se aplica de forma idempotente al iniciar y también puede aplicarse explícitamente:

```powershell
$env:DER_FLEX_DATABASE_URL = "postgresql://der_flex:der_flex_local@localhost:5432/der_flex"
.\.venv\Scripts\python.exe scripts\migrate_postgres.py
```

Sin `DER_FLEX_DATABASE_URL`, la demo conserva el backend en memoria para desarrollo y
pruebas unitarias. El arranque falla de forma explícita si no puede aplicar el esquema;
una instancia ya arrancada devuelve `503` en `/health/ready` si PostgreSQL deja de estar
disponible.

La ejecución correcta termina con `S2 PEBC smoke session completed: 16 validated wire messages`.
La demo muestra el intervalo UTC cargado al arrancar y publica Swagger UI en http://127.0.0.1:8000/docs.
La verificación de release escribe artefactos y evidencia bajo
`build/local-verification/`; construye dos veces y no publica nada.
Antes de comparar, normaliza LF —también en `PKG-INFO` y `.cfg`—, metadatos ZIP/TAR,
permisos, orden, `create_system` y el `RECORD` del wheel. Los ZIP canónicos almacenan
las entradas sin compresión y el `tar.gz` se emite
con bloques DEFLATE almacenados deterministas, evitando diferencias de implementación
de zlib entre sistemas. El TAR fija `0755` en directorios y `0644` en el resto para
no heredar permisos del host. `.gitattributes` fija LF en checkout para eliminar la
divergencia CRLF/LF observada al reconstruir entre Windows y Linux.
El SBOM CycloneDX recorre todas las dependencias transitivas de producción, conserva
las relaciones entre ellas y registra PURLs y hashes SHA-256 del metadato instalado.
Los extras de desarrollo quedan fuera; el extra `ws` solicitado a S2 sí se incluye.
La misma puerta ejecuta seis mutaciones dirigidas sobre controles críticos y exige que
las pruebas maten todas antes de declarar el release local como válido.
También crea una venv vacía e instala, con red deshabilitada para pip, el wheel y las 22
dependencias desde `vendor/wheelhouse` usando los hashes de `requirements-runtime.lock`.
El wheelhouse cubre CPython 3.13 en Windows AMD64 y Linux x86-64.
La instalación editable usa `requirements-runtime.constraints` para conservar ese mismo
cierre de producción aunque aparezcan versiones transitivas nuevas en el índice.
El segundo script genera un TAR canónico v7.1 y un ZIP de transporte de tres archivos.
Un auditor puede verificarlo con Python estándar sin depender del sitio web ni de que
su plataforma conserve extensiones de código dentro de ZIP anidados.

Con un motor Docker Linux disponible, `python scripts/verify_container.py` construye
la imagen, arranca un contenedor efímero y comprueba versión, importación desde
`site-packages`, readiness y dependencias antes de escribir `container-report.json`.
La ejecución usa UID 10001, raíz de solo lectura, elimina capacidades Linux y activa
`no-new-privileges`; la imagen Python base está fijada por digest. También genera
`offline-provenance.json`, que enlaza artefactos,
imagen y materiales por hash sin fingir una firma de identidad.
El digest identifica exactamente la imagen ensayada; no se afirma que dos builds
Docker independientes produzcan una imagen idéntica byte a byte.

## Documentación

- [Plan de ejecución](PLAN.md)
- [Resultado del Hito 0](docs/hito-0-investigacion.md)
- [Resultado del Hito 2](docs/hito-2-agregacion.md)
- [Progreso del Hito 3](docs/hito-3-reservas.md)
- [Resultado del Hito 4](docs/hito-4-openadr.md)
- [Resultado del Hito 5](docs/hito-5-endurecimiento.md)
- [Preparación del Hito 6](docs/hito-6-publicacion.md)
- [Matriz de trazabilidad](docs/traceability.md)
- [Arquitectura](docs/architecture.md)
- [Cómo contribuir](CONTRIBUTING.md)
- [Política de seguridad](SECURITY.md)
- [Changelog](CHANGELOG.md)
- [Decisiones de arquitectura](docs/adr/)

## Límites actuales

No es una implementación completa de S2 Connect ni una certificación S2. Toda capacidad derivada de PEBC se considera *best effort*, no firme. El umbral `k=10` es una salvaguarda del MVP y no demuestra por sí solo anonimización jurídica.

Para dificultar ataques por diferencia, una celda ya publicada se suprime si cambia su
cohorte, una oferta individual o su capacidad residual tras una reserva. También se
suprime el segundo intervalo adyacente si cambia la cohorte. Esto prioriza privacidad
sobre frescura y no convierte el agregado en anónimo. La API usa ventanas UTC y snapshots
inmutables de 15 minutos, limita cada consulta a 24 horas, comparte 60 consultas por
tenant y cadencia, publica zonas del catálogo autorizado y cuantiza potencia, energía,
confianza y participantes. PostgreSQL conserva snapshots, supresiones y presupuesto
entre procesos y reinicios. Estos controles no se presentan como privacidad diferencial;
el modelo y el riesgo residual se documentan en `docs/privacy-threat-model.md`.
Cuando se configura PostgreSQL,
la atomicidad de reservas serializa decisiones de capacidad por producto con bloqueos
transaccionales compartidos, por lo que varias instancias no pueden confirmar dos veces
la misma capacidad. Ofertas, registro físico, reservas y estados de privacidad se
recuperan después de reiniciar. Las instrucciones y webhooks se escriben en la misma
transacción que la activación. Un worker las reclama con `FOR UPDATE SKIP LOCKED`, lease
recuperable y reintento con backoff; los identificadores estables permiten deduplicar
webhooks. La activación queda `PENDING` hasta que todas las instrucciones terminan y solo
entonces pasa a `COMPLETED` o `FAILED`. La entrega es *al menos una vez*, no exactamente
una vez. El aceptador de recursos de la demo es sintético y debe sustituirse por un
adaptador S2 real.

La autenticación OIDC, los ámbitos, el aislamiento multi-tenant y el endurecimiento local
frente a consultas correlacionadas están implementados. La revisión independiente conjunta
de los hitos 15 y 16 sigue pendiente; además faltan operación distribuida completa y
validación protocolaria externa. Esta rama no está preparada para DER o datos reales.
