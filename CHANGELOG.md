# Changelog

Los cambios notables se documentan aquí siguiendo Keep a Changelog y versionado
semántico.

## [0.1.0] - 2026-09-25

### Added

- Simuladores deterministas de batería, cargador VE y bomba de calor.
- Flujo S2/PEBC validado y normalización conservadora a intervalos de 15 minutos.
- Agregación por zona con supresión `k=10` y API REST v1.
- Reservas atómicas e idempotentes, activaciones PEBC y webhooks firmados.
- Adaptador OpenADR 3.1.0 con fixtures contractuales.
- Demo Docker de 108 DER durante una hora, métricas y logs estructurados.
- Pruebas de escala hasta 1.008 recursos y 96 intervalos.
- Preparación comunitaria y automatización de CI para la primera publicación.
- Supresión permanente de una celda publicada si cambia su cohorte o una oferta
  individual, para impedir ataques simples por diferencia entre consultas.
- Supresión de una celda pública si cambia su capacidad residual por reservas, y de
  intervalos adyacentes con cohortes diferentes.
- Secuencia monotónica por oferta, con rechazo explícito de mensajes antiguos y de
  contenido conflictivo que reutilice la misma versión.
- Intersección obligatoria de restricciones S2 con una envolvente física aprovisionada
  de potencia, energía y velocidad de rampa.
- Presupuestos energéticos conservadores a lo largo de todo el horizonte y pronósticos
  sintéticos que respetan los límites operativos de SoC.
- Registro físico versionado e independiente de S2, con rechazo de recursos
  desconocidos, deshabilitados, en otra zona o aprovisionados conflictivamente.
- Pruebas de invariantes para orden de llegada, límites físicos aleatorios y ráfagas
  concurrentes de reservas sin sobreasignación.
- Cien trayectorias multiintervalo aleatorias y reservas distribuidas que demuestran
  límites acumulados de energía y rampa, con tolerancia numérica explícita.
- Posición temporal por época y secuencia, invalidación del horizonte obsoleto, política
  de reconexión y ventanas explícitas para observación frente a recepción.
- Rechazo uniforme de `NaN`, infinitos y magnitudes fuera de los techos operativos en
  registro físico, reservas, OpenADR y modelos públicos.
- Verificador local de release con wheel y sdist reproducibles, reconstrucción desde
  sdist, instalación aislada, manifiesto SHA-256, evidencia JSON y SBOM CycloneDX.
- Sdist autocontenido con código, pruebas, scripts, documentación y fixtures de
  auditoría; su composición mínima se comprueba antes de declarar `PASS`.
- Backend de construcción fijado a `setuptools==84.0.0` y frontend a `build==1.6.1`.
- Backend de construcción incluido también en el extra `dev`, con una regresión que
  impide volver a romper la verificación `--no-isolation` desde una venv vacía.
- Constraints de desarrollo sincronizadas con el lock runtime para impedir deriva de
  dependencias transitivas durante una verificación limpia.
- SBOM CycloneDX transitivo con grafo de dependencias, PURLs, extras solicitados y
  hashes SHA-256 de los metadatos instalados, excluyendo dependencias solo de desarrollo.
- Campañas de fuzzing deterministas sobre JSON estructurado y malformado, campos
  numéricos S2 y duraciones OpenADR, con 1.205 entradas reproducibles por ejecución.
- Puerta de mutación autocontenida con seis defectos dirigidos en privacidad, orden
  temporal, doble venta, límites físicos y OpenADR, más informe JSON independiente.
- Matriz de trazabilidad humana y procesable para los diez claims originales y cinco
  controles añadidos, con validación automática de rutas, tests, estados y evidencias.
- Wheelhouse offline para CPython 3.13 en Windows y Linux x86-64, con cierre transitivo
  fijado, hashes obligatorios y prueba de instalación en una venv recién creada.
- Bundle v4.1 determinista para revisión externa con fuente exacta, evidencia, wheelhouse,
  instrucciones, prompt adversarial y verificador autónomo basado en la biblioteca estándar.
- ZIP de transporte mínimo que encapsula el TAR canónico para resistir filtros de
  ingestión que eliminan selectivamente código o árboles anidados.
- Normalización reproducible de texto, `METADATA`, `RECORD`, permisos, timestamps y
  orden de wheel/sdist, más política LF de checkout y regresión LF frente a CRLF.
- Contenedores canónicos independientes de zlib: wheel y ZIP con `ZIP_STORED`, y
  `tar.gz` con bloques DEFLATE almacenados emitidos de forma determinista.
- Cabecera ZIP `create_system` fijada para evitar la divergencia Windows/Unix y
  normalización LF ampliada a `PKG-INFO` y `.cfg` del sdist.
- Modos de las entradas TAR canonizados a `0755` para directorios y `0644` para el
  resto, eliminando la última divergencia de sdist aislada por la revisión Linux.
- Verificador PKG01 reproducible que construye y ejecuta la imagen Docker Linux,
  comprueba instalación 0.1.0 desde `site-packages`, readiness y `pip check`, y genera
  evidencia JSON enlazada desde el expediente.
- Imagen final multi-stage sin árbol fuente, usuario 10001, raíz de solo lectura,
  capacidades eliminadas, `no-new-privileges` y base fijada por digest; instalación
  runtime offline con lock.
- Procedencia offline content-addressed que enlaza fuente, artefactos e imagen y declara
  explícitamente la ausencia de firma de identidad o timestamp confiable.
- Exclusión explícita de `*.egg-info` generado tanto del manifiesto de fuentes como del
  contexto Docker, evitando que residuos de builds anteriores alteren la procedencia.
- Evidencia SEC01 fechada con versión de `pip-audit`, servicio consultado, hash del
  lock y declaración explícita de que el servicio remoto no expone hash de snapshot.
- Cobertura mypy ampliada a todos los archivos Python, incluidos todos los tests
  (51 en el corte v4.6).

### Fixed

- Instalación limpia de las pruebas: uso consistente de `httpx2` y dependencia
  explícita de `tzdata` en plataformas sin base horaria del sistema.
- Actualización de pytest a 9.0.3 por `GHSA-6w46-j5rx-g56g`.
- Generación de flotas grandes sin estados iniciales por encima de la potencia física
  de cargadores o bombas de calor.
- Respuestas 422 saneadas para que un `NaN` rechazado no provoque un error al serializar
  el propio detalle de validación ni refleje el valor recibido.
- Errores de validación propios del parser S2 traducidos a errores uniformes de entrada.
- Duraciones OpenADR acotadas entre un segundo y un día antes de construir el intervalo.
