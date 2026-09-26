# Plan para una próxima revisión completamente satisfactoria

**Fecha base:** 12 de septiembre de 2026
**Línea base válida:** bundle v3, 34 pruebas. La evaluación de 31 pruebas corresponde
a un corte anterior y se conserva solo como historial.
**Objetivo:** conseguir primero un expediente experimental con todos los claims
verificables en `PASS`, y después retirar, uno por uno, los motivos técnicos del
`NO-GO` para un piloto con DER reales.

## Estrategia de puertas

- **Puerta A — release experimental:** hitos 8 y 9. Debe permitir que un tercero
  reproduzca código, CI, auditorías y contenedor desde un tag inmutable.
- **Puerta B — piloto técnico controlado:** hitos 10 a 17. Debe resolver estado
  distribuido, seguridad, orden temporal, límites físicos, privacidad y operación.
- **Puerta C — interoperabilidad y cumplimiento externos:** hitos 18 a 20. Depende de
  laboratorios, fabricantes, operador del piloto y revisión jurídica independientes.

Mientras las puertas externas estén aplazadas, los hitos locales pueden adelantarse.
Por esa vía, los hitos 10 y 11 se completaron localmente el 12 de septiembre de 2026.
La batería local suma 101 pruebas, 1.205 entradas de fuzzing determinista, seis
mutaciones dirigidas, una matriz de 17 claims, instalación offline y bundle autónomo
por ejecución; ambos quedan pendientes de revisión independiente.

Cada hito resuelve un único problema principal. No se inicia el siguiente si el
criterio de salida del anterior no está demostrado por un artefacto reproducible.

## Resumen ejecutivo

| Hito | Problema único | Estimación | Desbloquea |
|---|---|---:|---|
| 8 | Procedencia del artefacto | 1–2 días | Evidencia inmutable |
| 9 | Contenedor no verificado | 2–3 días | `PKG01 PASS` y R4 |
| 10 | Mensajes fuera de orden | 3–5 días | Estado temporal fiable |
| 11 | Límites físicos no independientes | 4–6 días | Capacidad físicamente acotada |
| 12 | Pérdida de estado al reiniciar | 1–2 semanas | Recuperación durable |
| 13 | Doble venta entre procesos | 1 semana | Escalado seguro |
| 14 | Pérdida/duplicado de eventos | 1 semana | Entrega durable |
| 15 | Acceso sin identidad ni tenant | 1–2 semanas | Frontera de seguridad |
| 16 | Privacidad sin garantía formal | 1–2 semanas | Revisión de privacidad |
| 17 | Rendimiento solo local | 1 semana | SLO de piloto |
| 18 | S2 solo validado por parser | 2–4 semanas externas | Interoperabilidad S2 |
| 19 | Extensiones OpenADR locales | 2–6 semanas externas | Interoperabilidad OpenADR |
| 20 | Falta de autorización real | 2–6 semanas, en paralelo | Decisión de piloto |

La Puerta A puede cerrarse en aproximadamente una semana. La preparación técnica de la
Puerta B requiere unas 8–12 semanas de desarrollo; la Puerta C depende de disponibilidad
de terceros y puede ejecutarse parcialmente en paralelo.

## Hito 8 — Procedencia inmutable del release

**Estado:** completado para distribución offline: construcción reproducible,
instalación aislada, manifiesto, SBOM y procedencia content-addressed. La publicación,
firma de identidad y ejecución pública se excluyen por decisión de alcance.

**Problema a resolver:** un revisor puede recibir un ZIP antiguo o distinto del
expediente que está leyendo.

**Trabajo:**

- Publicar el código en un repositorio público y congelar un commit candidato.
- Generar bundle, ZIP fuente, `evidence.json`, SBOM y hashes desde ese mismo commit.
- Crear un tag firmado `v0.1.0-rc1`; ningún resultado se introduce manualmente después.
- Hacer que el pipeline falle si el hash, el número de pruebas o los marcadores de
  código difieren del expediente.

**Pruebas exigidas:** reconstrucción del bundle dos veces desde el tag; comparación
de manifiestos; prueba negativa alterando un byte y esperando fallo.

**Criterio de salida local:** un tercero recibe un único bundle por cualquier canal,
verifica sus hashes y reproduce el árbol exacto sin depender de una web. Esto demuestra
integridad, no autoría. Una firma de identidad queda como mejora futura opcional.

**Evidencia para la revisión:** URL del tag, commit completo, firma, manifiesto y log de
generación. Resuelve definitivamente las dudas de trazabilidad de T01–SEC01.

## Hito 9 — Contenedor verificable de forma independiente

**Estado:** completado y endurecido localmente; pendiente únicamente de repetición
independiente y atestación de identidad opcional.

**Problema resuelto localmente:** PKG01 se ejecuta de forma automatizada sobre Docker
Linux y conserva evidencia estructurada; falta únicamente repetición independiente.

**Trabajo:**

- Construir la imagen por etapas desde el árbol fuente verificado.
- Ejecutar dentro del contenedor health checks, demo, versión instalada y smoke tests.
- Registrar digest local, SBOM y procedencia offline content-addressed.
- Añadir instrucciones para verificar digest y contenido sin confiar en una web.

**Pruebas exigidas:** construcción limpia Linux; ejecución sin montar el repositorio;
usuario no privilegiado; filesystem de solo lectura; comprobación de licencia y versión.

**Criterio de salida local:** la imagen funciona sin montar el repositorio, usa usuario
no privilegiado, raíz de solo lectura y controles de reducción de privilegios; el
revisor puede asociar digest, SBOM, fuente y evidencia offline.

**Evidencia para la revisión:** `container-report.json`, digest, SBOM,
`offline-provenance.json` y salida de los probes. La repetición externa puede elevar
la confianza, pero no bloquea el alcance local.

## Hito 10 — Rechazo de ofertas antiguas o fuera de orden

**Estado:** completado localmente; pendiente de una nueva revisión independiente.

**Problema a resolver:** un mensaje retrasado puede reemplazar una oferta más reciente
porque `source_version` todavía no expresa un orden verificable.

**Trabajo:** definir por recurso una secuencia monotónica y un instante de observación;
persistir el último valor aceptado; rechazar duplicados conflictivos, regresiones y
mensajes fuera de ventana; documentar la política de reinicio de secuencia.

**Pruebas exigidas:** permutaciones de llegada, replay, duplicado idéntico, UUID distinto
con secuencia antigua, reloj adelantado/atrasado y reconexión.

**Criterio de salida:** para cualquier orden de entrega, el estado final coincide con la
mayor versión válida y ninguna oferta obsoleta recupera capacidad ya retirada.

## Hito 11 — Límites físicos independientes del mensaje S2

**Estado:** completado localmente con registro físico independiente; pendiente de una
nueva revisión independiente.

**Problema a resolver:** confiar exclusivamente en los límites PEBC declarados por el
recurso puede agregar potencia superior a su envolvente física conocida.

**Trabajo:** crear un registro aprovisionado de capacidades por DER; intersectar la
oferta S2 con potencia, energía, rampa y estado operativo conocidos; rechazar límites
incoherentes en vez de ampliarlos silenciosamente.

**Pruebas exigidas:** límites invertidos, exceso de inversor, SoC insuficiente, rampas,
energía multiintervalo y propiedades que demuestren que la oferta nunca supera la
envolvente registrada.

**Criterio de salida:** toda capacidad publicada puede trazarse a la intersección entre
restricción física, estado vigente y mensaje protocolario.

## Hito 12 — Persistencia y recuperación tras caída

**Problema a resolver:** un reinicio pierde ofertas, reservas, activaciones y estado de
supresión.

**Trabajo:** sustituir el almacenamiento operativo por una base transaccional con
migraciones; persistir idempotencia, cohortes publicadas, reservas y activaciones;
definir retención y reconstrucción; mantener la implementación en memoria solo para tests.

**Pruebas exigidas:** matar el proceso tras cada transición, reiniciar, repetir la
solicitud idempotente, caducar reservas y verificar que la privacidad no se reinicia.

**Criterio de salida:** ninguna reserva confirmada se pierde o duplica y una caída no
permite volver a publicar una celda previamente suprimida.

## Hito 13 — Atomicidad con múltiples procesos

**Problema a resolver:** los bloqueos actuales solo protegen un proceso y pueden permitir
doble venta con varios workers o réplicas.

**Trabajo:** mover la decisión de capacidad a una transacción serializable o bloqueo
equivalente en el almacenamiento compartido; imponer invariantes mediante restricciones
de base de datos; eliminar la dependencia de `RLock` para seguridad distribuida.

**Pruebas exigidas:** cuatro procesos y dos réplicas reservando simultáneamente; al menos
10.000 carreras; timeouts, rollback, caída del ganador y reintento idempotente.

**Criterio de salida:** la suma confirmada nunca supera la capacidad elegible y cada clave
idempotente produce como máximo una reserva global.

## Hito 14 — Entrega durable de activaciones y webhooks

**Problema a resolver:** una caída entre confirmar una transición y enviar su webhook
puede perder o duplicar eventos.

**Trabajo:** implementar outbox transaccional, workers reintentables, identificadores de
evento, deduplicación receptora, backoff, cola de fallos y rotación de secretos.

**Pruebas exigidas:** caída antes/después del commit, timeout, respuesta 5xx, duplicado,
reordenación y recuperación de la cola tras reinicio.

**Criterio de salida:** cada evento confirmado termina entregado o visible en una cola de
fallos auditable, sin efectos duplicados en un receptor idempotente.

## Hito 15 — Autenticación, autorización y aislamiento multiempresa

**Estado:** completado localmente. OIDC/JWT con JWKS rotatorio, ámbitos separados,
partición persistente por `tenant_id`, arranque seguro y auditoría mínima de rechazos
están implementados y cubiertos por pruebas. Pendiente únicamente la auditoría externa
que se realizará al cerrar el hito completo.

**Problema a resolver:** cualquier cliente de red puede consultar agregados o reservar
capacidad y no existe una frontera de tenant.

**Trabajo:** integrar identidad fuerte; definir ámbitos separados para lectura, reserva,
activación y administración; asociar zonas, recursos y reservas a tenant; proteger la
comunicación de máquina a máquina y rotar credenciales.

**Pruebas exigidas:** matriz rol×endpoint, token caducado, audiencia/emisor inválidos,
escalada horizontal/vertical, acceso cruzado por UUID y consultas sin tenant.

**Criterio de salida:** ninguna petición puede leer o modificar datos fuera de su ámbito
y todos los rechazos quedan auditados sin registrar secretos o telemetría doméstica.

## Hito 16 — Privacidad resistente a consultas correlacionadas

**Estado:** implementación local completada; revisión independiente pendiente. Están
aplicados catálogo fijo de zonas, ventanas/cadencia UTC, snapshots inmutables,
cuantización y presupuesto compartido por tenant con persistencia PostgreSQL. La revisión
independiente se realizará junto con el hito 15 al terminar la verificación de release.

**Problema a resolver:** la supresión actual bloquea los deltas conocidos, pero no ofrece
una garantía formal frente a vínculo, inferencia, múltiples identidades o reinicios.

**Trabajo:** formalizar el modelo de atacante; autenticar y limitar consultas; fijar
cadencia y ventanas de publicación; conservar supresión en estado compartido; evaluar
zonas adaptativas, redondeo y privacidad diferencial con presupuesto medible.

**Pruebas exigidas:** ataques de diferencia en ventanas solapadas, reservas, intervalos
adyacentes, reinicios, identidades múltiples, fuentes auxiliares y zonas escasas.

**Criterio de salida:** una revisión de privacidad independiente documenta riesgo residual,
parámetros, utilidad perdida y condiciones explícitas de uso; no basta con que los tests
funcionales pasen.

## Hito 17 — Rendimiento y resiliencia de la arquitectura real

**Problema a resolver:** el benchmark ASGI en memoria no predice el comportamiento con
TLS, base de datos, múltiples procesos y colas.

**Trabajo:** definir SLO del piloto; probar la pila desplegada con red, TLS y estado
persistente; medir p50/p95/p99, throughput y saturación; añadir límites, backpressure,
health checks reales y observabilidad distribuida.

**Pruebas exigidas:** carga sostenida, picos, base lenta, caída de réplica, partición de
red, migración, cola saturada y degradación del proveedor de identidad.

**Criterio de salida:** se cumplen los SLO acordados sin doble reserva, pérdida de eventos
ni relajación de privacidad durante fallos.

## Hito 18 — Interoperabilidad S2 con un RM independiente

**Problema a resolver:** el parser valida estructura, pero no demuestra interoperabilidad
de sesión ni comportamiento con hardware/software ajeno al proyecto.

**Trabajo:** ejecutar una matriz de sesiones contra al menos un RM S2 independiente;
probar conexión, negociación, PEBC, reconexión, rechazo y desfases temporales; capturar
trazas sanitizadas.

**Pruebas exigidas:** casos normales y negativos acordados con la contraparte, incluida
una activación física dentro de un entorno seguro de laboratorio.

**Criterio de salida:** informe firmado por la contraparte con versión, matriz, trazas y
desviaciones. Si se busca afirmar conformidad, iniciar además el proceso oficial aplicable.

## Hito 19 — Interoperabilidad OpenADR sin extensiones ambiguas

**Problema a resolver:** los descriptores `DER_FLEX_*` son válidos como extensión local,
pero un VTN genérico puede ignorarlos o rechazarlos.

**Trabajo:** acordar un perfil de interoperabilidad; mapear capacidades usando elementos
estándar cuando existan; versionar y documentar las extensiones inevitables; probar con
un VTN independiente y con la herramienta oficial correspondiente.

**Pruebas exigidas:** evento→reserva→activación→reporte, errores, idempotencia, negociación
de capacidades y comportamiento ante un descriptor desconocido.

**Criterio de salida:** un VTN externo completa el flujo sin cambios manuales. Una
afirmación de certificación solo se añade tras recibir el resultado oficial.

## Hito 20 — Puerta jurídica y operacional para datos reales

**Problema a resolver:** aunque el software sea técnicamente sólido, no existe todavía
base suficiente para procesar datos personales o controlar activos físicos.

**Trabajo:** identificar operador, responsables y encargados; realizar DPIA y análisis de
riesgo físico; definir consentimiento/base jurídica, retención, derechos, respuesta a
incidentes, rollback y parada segura; ejecutar un simulacro operativo.

**Pruebas exigidas:** tabletop de incidente, revocación de acceso, borrado/retención,
recuperación, pérdida de comunicaciones y orden de parada de emergencia.

**Criterio de salida:** responsables técnicos, jurídicos y operacionales firman un
`GO` limitado por alcance. Sin esas firmas, el resultado sigue siendo `NO-GO` para datos
o DER reales aunque todos los tests de software estén verdes.

## Orden recomendado y dependencias

```text
H8 procedencia ──> H9 contenedor ──> RELEASE EXPERIMENTAL CON TODOS LOS CLAIMS PASS
                         │
H10 orden ─> H11 física ─> H12 persistencia ─> H13 multiproceso ─> H14 entrega
                                                        │
                                      H15 identidad ─> H16 privacidad
                                                        │
                                      H17 carga/resiliencia
                                                        │
                              H18 S2 ───────┬─────── H19 OpenADR
                                            │
                                     H20 puerta real
```

## Secuencia de revisión propuesta

1. **Revisión R4, inmediata tras H9:** objetivo exclusivo de obtener `PASS` en T01,
   T02, P01, P02, PR01, C01, S01, PERF01, SEC01 y PKG01 para el release experimental.
2. **Revisión R5, tras H17:** auditoría de seguridad, concurrencia, privacidad,
   recuperación y carga sobre la arquitectura persistente.
3. **Revisión R6, tras H20:** decisión externa sobre piloto real; incluye informes S2,
   OpenADR, privacidad, seguridad y operación.

## Definición de terminado de la próxima revisión

Para **R4**, terminado significa: bundle y release firmados, CI público reproducible,
contenedor ejecutado por un tercero y los diez claims actuales en `PASS`, sin ampliar el
alcance a producción real.

Para considerar la revisión **completamente satisfactoria para un piloto**, además deben
estar cerrados H10–H20, no existir hallazgos críticos o altos abiertos, y cualquier
riesgo medio aceptado debe tener propietario, fecha, mitigación y señal operacional.
