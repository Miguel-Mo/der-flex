# Resultados esperados, no resultados confiables

Estos valores sirven para detectar divergencias. El auditor debe reproducirlos cuando
su entorno lo permita.

- `verify_bundle.py`: PASS; todos los hashes y archivos coinciden.
- pytest: 101 pruebas superadas y un aviso upstream Starlette/AnyIO.
- Ruff: sin incidencias.
- mypy: sin incidencias en los 51 archivos Python de `src/`, `scripts/` y `tests/`.
- S2: 16 mensajes wire validados.
- fuzzing: 1.205 entradas deterministas por ejecución.
- mutación dirigida: 6/6 mutantes eliminados, 100 %.
- trazabilidad: 17 claims y 77 referencias válidas.
- SBOM: 15 componentes transitivos y 22 relaciones.
- wheelhouse: 17 wheels, 15 componentes, hashes coincidentes.
- instalación offline: `pip check` limpio, versión 0.1.0 y 14 rutas.
- wheel y sdist: dos construcciones idénticas; wheel reconstruido desde sdist idéntico.
- texto —incluidos `PKG-INFO` y `.cfg`—, `METADATA`, `RECORD`, timestamps, orden,
  permisos y `ZipInfo.create_system` normalizados; las pruebas enfrentan
  deliberadamente entradas LF/CRLF y cabeceras ZIP Windows/Unix.
- modos TAR canónicos: `0755` para directorios y `0644` para archivos y demás entradas.
- los ZIP canónicos usan entradas `ZIP_STORED`; el `tar.gz` usa bloques DEFLATE
  almacenados emitidos de forma determinista, sin depender del resultado de zlib.
- `security-audit.json`: fecha UTC, versión de `pip-audit`, servicio consultado, hash
  del lock y resultado. El hash de la base remota queda explícitamente como no disponible.
- `container-report.json`: imagen Linux AMD64, DER Flex 0.1.0 importado desde
  `site-packages`, UID 10001, raíz de solo lectura, capacidades eliminadas,
  `no-new-privileges`, base Python fijada por digest, readiness aprobada y `pip check` limpio.
- `offline-provenance.json`: artefactos, imagen y materiales enlazados por contenido;
  `signed=false`, ausencia de identidad pública y ausencia de una garantía de
  reconstrucción Docker byte a byte declaradas expresamente.

Los hashes concretos se leen de `evidence/SHA256SUMS`; no se duplican aquí para evitar
una dependencia circular al regenerar el expediente.

Resultados externos deliberadamente no esperados: esquema oficial OpenADR,
certificación S2/OpenADR, CI/tag/firma públicos, persistencia, multiproceso,
autenticación multi-tenant y validación jurídica u operacional con DER reales.
La repetición independiente de Docker continúa fuera del alcance local.
