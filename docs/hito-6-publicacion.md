# Hito 6 — Preparación de publicación

**Estado:** completado para distribución experimental offline el 25 de septiembre de 2026.

## Preparado localmente

- Versión del paquete y de OpenAPI alineadas en `0.1.0`.
- Licencia Apache 2.0 completa y declaración SPDX en `pyproject.toml`.
- Guías de contribución, conducta y seguridad.
- Changelog de la versión y guía de arquitectura con invariantes.
- CI para Python 3.13: Ruff, mypy, pytest, auditoría y construcción Docker.
- Imagen Docker reproducida localmente y demo comprobada en estado `healthy`.
- Wheel y sdist construidos dos veces con metadatos temporales normalizados y hashes
  idénticos entre ejecuciones.
- Contenido textual convertido a LF, incluidos `PKG-INFO` y `.cfg`; wheel regenerado
  con orden, permisos, timestamps, `create_system`, `METADATA` y `RECORD` deterministas.
  Las entradas ZIP se guardan con `ZIP_STORED` y
  el sdist usa bloques DEFLATE almacenados deterministas, por lo que el resultado no
  depende del compresor zlib de cada SO. Los modos TAR se fijan a `0755` para
  directorios y `0644` para el resto. Las regresiones cubren LF/CRLF, modos y gzip.
- Wheel reconstruido desde el sdist idéntico al wheel directo e importado desde una
  instalación temporal fuera del árbol fuente.
- Evidencia local en `build/local-verification/`: artefactos, `evidence.json`,
  `source-manifest.json`, `sbom.cdx.json`, `security-audit.json`, `container-report.json`,
  `offline-provenance.json`, `mutation-report.json`, `traceability-report.json` y
  `SHA256SUMS`.
- Matriz de trazabilidad JSON y Markdown con los diez claims originales, los controles
  locales adicionales y los límites de alcance, validada contra la colección real de
  pytest antes de construir los artefactos.
- Lock transitivo y wheelhouse incluidos en el sdist y en la evidencia. El verificador
  crea una venv nueva, fuerza `PIP_NO_INDEX=1`, exige todos los hashes, instala el wheel,
  ejecuta `pip check` e importa la aplicación desde `site-packages`.
- Cobertura offline declarada: CPython 3.13 sobre Windows AMD64 y manylinux x86-64.
- Bundle v4.6 autocontenido con entrada `START-HERE.md`, prompt adversarial, resultados
  esperados, fuente exacta, evidencia y un `verify_bundle.py` que solo usa Python estándar.
- TAR canónico directo y ZIP de transporte con solo tres entradas para evitar filtros
  selectivos observados al transferir el primer v4.
- SBOM CycloneDX con el cierre transitivo de producción, grafo `dependsOn`, PURLs,
  extras activados y hash SHA-256 del fichero `METADATA` instalado de cada componente.
  El hash se etiqueta como metadato local y no se confunde con el digest del wheel.
- Pruebas negativas que impiden introducir en el SBOM las herramientas exclusivas de
  desarrollo y comprueban que todas las referencias del grafo son resolubles.
- El sdist incluye las pruebas, scripts, fixtures y documentación necesarios para que
  un revisor trabaje sin acceso al sitio web.
- PKG01 ejecutado con un motor Docker Linux local: imagen AMD64, versión 0.1.0 desde
  `site-packages`, UID 10001, raíz de solo lectura, capacidades eliminadas,
  `no-new-privileges`, imagen base fijada por digest, readiness y `pip check` aprobados;
  la repetición independiente, la reconstrucción byte a byte de la imagen y la
  atestación firmada siguen pendientes. El digest identifica la imagen efectivamente
  ensayada, no una garantía de build Docker determinista.
- `evidence.json` mantiene el esquema oficial OpenADR como `NOT_RUN`.
- `security-audit.json` registra la fecha UTC, herramienta, servicio PyPI y hash del
  lock auditado; declara como no disponible el hash del snapshot remoto en vez de
  atribuirle una procedencia que `pip-audit` no puede demostrar.
- El extra `dev` instala también `setuptools==84.0.0`: una venv vacía puede ejecutar
  las construcciones `--no-isolation` del verificador sin depender de paquetes
  preinstalados accidentalmente en el entorno anfitrión.
- `requirements-runtime.constraints` fija durante el desarrollo el mismo cierre que
  `requirements-runtime.lock`, evitando que nuevas versiones transitivas alteren el
  SBOM o rompan la comparación con el wheelhouse.

La comprobación se repite con:

```powershell
.\.venv\Scripts\python.exe scripts\verify_release.py
```

Los hashes de la ejecución vigente se consultan en `evidence.json` y `SHA256SUMS`; no
se duplican aquí para evitar que la propia documentación altere el artefacto descrito.

## Mejoras externas opcionales, fuera del alcance offline

1. Crear o elegir el repositorio público y sustituir cualquier metadato de proyecto que
   dependa de su URL.
2. Ejecutar el CI en el proveedor remoto.
3. Crear el tag firmado `v0.1.0` y publicar la release con sus notas.
4. Agendar al menos una sesión de feedback con FAN/S2, LF Energy, una comunidad
   energética o un agregador.
5. Registrar y priorizar los hallazgos; congelar nuevas funciones durante dos semanas.

Estas acciones no bloquean la distribución experimental offline. Cambian sistemas
externos y requieren que la persona responsable elija repositorio, identidad y
destinatarios; no se ejecutan como parte del alcance local acordado.
