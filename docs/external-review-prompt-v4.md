# Prompt de revisión adversarial independiente — bundle v8.0

Actúa como auditor independiente y hostil a las afirmaciones. Trabaja únicamente con
los archivos recibidos y no presupongas que su evidencia es correcta. Si recibes el
ZIP de transporte, comprueba su SHA-256, extrae primero el `.tar.gz` que contiene y
comprueba también el SHA-256 declarado en `TAR-SHA256.txt`. El TAR es el expediente
canónico.

Primero ejecuta `python verify_bundle.py`. Si falla un hash, falta un archivo o no
coincide `source/` con `source-manifest.json`, detén la revisión y dicta `FAIL` de
procedencia. No uses resultados narrados para sustituir una ejecución que sí puedas
realizar.

Después revisa los claims T01, T02, P01, P02, PR01, C01, S01, PERF01, SEC01 y PKG01,
además de ORD01, PHY01, FZ01, MT01, SBOM01, OFF01, OUT01, BND01, AUTH01 y PRIV02. Para cada uno comunica:

- `PASS`, `PASS_WITH_SCOPE_LIMIT`, `FAIL` o `NOT_VERIFIABLE`;
- pruebas y comandos realmente ejecutados;
- evidencia examinada;
- discrepancias entre código, documentación y resultados;
- evidencia mínima necesaria para mejorar el veredicto.

Intenta refutar específicamente: supresión tras cambios de cohorte, valor y reserva;
doble venta concurrente; replay de sesiones; ampliación fraudulenta de límites físicos;
valores no finitos; duración OpenADR excesiva; wheelhouse alterado; referencias de
trazabilidad rotas y mutantes supervivientes.

Para OUT01 usa PostgreSQL real y ejecuta `tests/test_postgres_outbox.py`. Comprueba que
ningún webhook ni instrucción se invoca antes del commit; que un proceso nuevo recupera
el trabajo; que rollback no deja tareas; que dos workers no reclaman el mismo evento;
que un lease caducado se recupera sin que el worker antiguo pueda confirmar su intento;
que errores transitorios reintentan y rechazos funcionales terminan en `FAILED`. Verifica
los tres webhooks y la estabilidad de `X-DER-Flex-Event-ID`. No presentes entrega
*al menos una vez* como exactamente una vez ni el aceptador sintético como transporte S2.

Presta atención especial a los cambios y regresiones de este corte:

0. **Revisión incremental v7.1.** La revisión v7.0 encontró que
   `PrivacyPublicationPolicy(minimum_participants=1)` era aceptada y observó que `nbf`
   se validaba solo si estaba presente. Intenta reproducir ambos hallazgos. Exige que
   cualquier umbral público inferior a diez lance `ValueError`, que la API no disponga
   de un bypass equivalente y que un JWT firmado correctamente pero sin `nbf` sea
   rechazado. Comprueba que las pruebas verticales usan diez recursos en lugar de una
   política pública debilitada. Clasifica cada hallazgo anterior como `RESOLVED`,
   `PARTIAL` u `OPEN`.

0 bis. **Rendimiento y resiliencia PERF02.** No uses el antiguo benchmark ASGI para
   conceder este claim. Ejecuta `python scripts/verify_pilot_slo.py` con Docker real y
   examina `pilot-slo-report.json`. Confirma TLS, dos workers API, PostgreSQL separado,
   dos workers outbox, p50/p95/p99, ráfaga con shedding, backlog `PENDING` recuperado,
   ausencia de sobreventa y duplicados, snapshot estable, pausa real de PostgreSQL,
   readiness/negocio `503` y recuperación. Intenta arrancar simultáneamente los workers
   sobre un esquema vacío para refutar la serialización de migraciones. No conviertas
   esta puerta acotada de un host en dimensionamiento o SLO contractual.

1. **Bootstrap limpio.** Crea una venv nueva de Python 3.13 sin reutilizar paquetes
   del sistema. Ejecuta
   `python -m pip install -c requirements-runtime.constraints -e ".[dev]"` y, sin instalar nada más,
   `python scripts/verify_release.py`. La revisión v4.5 fallaba aquí porque la
   construcción usaba `--no-isolation` sin instalar el backend en la venv. Confirma
   que `setuptools==84.0.0` aparece tanto en `[build-system].requires` como en el extra
   `dev`. Confirma además que las constraints reflejan exactamente las versiones del
   lock con hashes. Si necesitas instalar el backend o cambiar versiones manualmente,
   T01 debe ser `FAIL`.

2. **Reproducibilidad entre sistemas.** Inspecciona `normalize_wheel`,
   `normalize_sdist`, `deterministic_gzip` y `build_review_bundle.py`. Confirma que
   los ZIP canónicos usan entradas `ZIP_STORED` y que el gzip usa bloques DEFLATE
   almacenados deterministas, no la salida comprimida de zlib. Confirma además que
   cada `ZipInfo.create_system` está fijado a `0`, y que `PKG-INFO` y `.cfg` se
   normalizan a LF. En el TAR confirma modos `0755` para directorios y `0644` para
   las demás entradas, sin conservar permisos del SO anfitrión. Ejecuta las regresiones
   LF/CRLF, metadatos ZIP, modos TAR y gzip. Si dispones de Linux o macOS, reconstruye allí desde la fuente
   exacta y compara los hashes normalizados del wheel y sdist con
   `evidence/evidence.json`. No otorgues una comprobación cross-OS por la mera revisión
   del algoritmo: indica separadamente si hubo reconstrucción real en un segundo SO.
3. **Procedencia de SEC01.** Examina `evidence/security-audit.json`: valida fecha UTC,
   versión de `pip-audit`, servicio consultado, hash de
   `requirements-runtime.lock`, número de dependencias y vulnerabilidades. Recalcula
   el hash del lock y, si tienes red, repite la consulta. El servicio remoto PyPI no
   expone un hash de snapshot; confirma que el expediente lo declara como no
   disponible y no inventa una garantía histórica.

4. **Identidad y aislamiento AUTH01.** No aceptes los tokens estáticos como evidencia
   de producción. Ejecuta la suite OIDC con claves RSA y dos `kid`; intenta tokens
   caducados, prematuros, firmados con clave desconocida, con emisor/audiencia falsos,
   sin tenant y con comodines. Recorre la matriz rol×endpoint y prueba escalada vertical,
   zona ajena y UUID de otro tenant. Confirma que un despliegue con PostgreSQL no arranca
   sin `DER_FLEX_AUTH_MODE` explícito, que OpenAPI declara Bearer y que los eventos de
   rechazo contienen solo decisión, motivo, método y plantilla de ruta: nunca token,
   sujeto, tenant, zona, UUID, payload ni telemetría. Separa la verificación local de
   una integración/certificación con un IdP real, que no se reclama.

5. **Consultas correlacionadas PRIV02.** Ejecuta el modelo adversarial descrito en
   `docs/privacy-threat-model.md`: ventanas solapadas, dos identidades del mismo tenant,
   cambio de oferta, reserva, intervalos vecinos, zona escasa, reinicio y dos procesos
   PostgreSQL. Comprueba que el presupuesto se comparte de forma atómica, que la lista de
   zonas procede del catálogo autorizado y que un snapshot publicado no cambia dentro de
   sus 15 minutos aunque cambie el estado privado. Verifica cuantización y pérdida de
   utilidad. Intenta refutar la protección usando fuentes auxiliares; no otorgues una
   garantía de anonimización ni ε-DP, porque el proyecto las descarta explícitamente.

No conviertas validación con parsers en certificación protocolaria. No conviertas un
`RLock` en garantía multiproceso. No conviertas un benchmark ASGI local en SLO de
producción. No conviertas una consulta puntual de vulnerabilidades en garantía futura.
Para PKG01 ejecuta `python scripts/verify_container.py` después de
`verify_release.py`. Verifica independientemente la imagen y conserva
`NOT_VERIFIABLE` si no ejecutas realmente el contenedor; no confíes solo en el
`container-report.json` incluido.

Intenta ejecutar la imagen sin privilegios, con raíz de solo lectura, `cap-drop=ALL`
y `no-new-privileges`. Confirma UID 10001, ausencia de `source/` en la etapa final,
healthcheck declarado y runtime instalado únicamente desde wheels. Examina también
que ambas etapas fijan la imagen Python base por el mismo digest inmutable. Examina
además
`offline-provenance.json`: sus hashes deben coincidir, pero `signed=false` debe impedir
que se confunda integridad offline con autoría o timestamp confiable.
El digest enlaza la imagen concreta ensayada: el proyecto no declara que dos builds
Docker sin caché deban tener el mismo ID. Si lo pruebas, informa el resultado como
límite de reproducibilidad de imagen, separado de la validez funcional de PKG01.

Finaliza con dos decisiones separadas: publicación experimental y uso con DER/datos
reales. Enumera primero los hallazgos nuevos por severidad y después los límites ya
declarados, para no presentar estos últimos como descubrimientos. Incluye una tabla
 de los 21 claims y una sección específica que diga si el anterior hallazgo medio de
compresión cross-OS queda `RESOLVED`, `PARTIAL` o `OPEN`, con la evidencia que sustenta
   esa decisión. El wheel ya coincidió en una reconstrucción Linux de v4.2. Comprueba
   específicamente si ha desaparecido el vector restante encontrado allí: modos
   `0666`/`0777` de Windows frente a `0644`/`0755` de Unix en el sdist.
