# Respuesta a la revisión externa — 12 de septiembre de 2026

Dos revisiones adversariales ejecutadas fuera del desarrollo original detectaron varios
puntos reproducibles. Esta nota separa los hallazgos aceptados de los que no pudieron
reproducirse y deja los comandos de verificación.

## Hallazgos aceptados y corregidos

- La prueba vertical importaba `httpx`, aunque la dependencia declarada es `httpx2`.
  El import ahora coincide con el paquete instalado.
- `pytest 8.4.2` estaba afectado por `GHSA-6w46-j5rx-g56g`; se actualizó a 9.0.3.
- La prueba de cambio horario dependía de la base horaria del sistema. `tzdata==2026.4`
  se declara ahora como dependencia de desarrollo para instalaciones mínimas.
- `pip-audit .` cubre la resolución de producción, pero no todo el entorno de desarrollo.
  El CI ejecuta además `pip-audit --local --skip-editable`.
- El umbral `k=10` aislado permitía ataques simples por diferencias. Después de publicar
  una celda, cualquier cambio de cohorte o valor la suprime durante la vida del proceso.
- La capacidad residual pública se vuelve a comparar con su primera publicación. Una
  reserva, cancelación o expiración que la cambie suprime la celda en vez de revelar el
  volumen reservado; una cohorte diferente en un intervalo adyacente se suprime también.
- El benchmark ampliado atraviesa ahora la API HTTP/ASGI completa.

## Hallazgo no reproducido

El supuesto desajuste del hash OpenADR no se reproduce con la procedencia declarada:

- repositorio: `https://github.com/grid-coordination/openadr3-specification.git`;
- commit: `e47c6da57aa470f81073fc214b4e92052bf6c474`;
- fichero: `3.1.0/openadr3.yaml`;
- SHA-256 obtenido: `EF19311C994D89ABF96DC56032701ED8C5F25242B337019FC7DAEFB0F19F0D19`.

Ese valor corresponde a la copia de trabajo con CRLF. El blob Git normativo con LF es
`135BC74AF2F733338B55AFA95DF3CB3A1185867E8E41A7D16CDB2DBFAEB3B475`.
Ambos identifican el mismo texto con finales de línea distintos; el proyecto documenta
los dos para que la comprobación sea reproducible en Windows, Linux y macOS.

El hash coincide con el fijado en el proyecto. Una descarga distinta, una conversión de
finales de línea o la revisión de otro artefacto puede explicar el resultado externo.

## Evidencia posterior a la corrección

```powershell
python -m pip install -e ".[dev]"
python -m ruff check .
python -m mypy src scripts
python -m pytest
python -m pip_audit . --progress-spinner off
python -m pip_audit --local --skip-editable --progress-spinner off
python scripts/benchmark_demo.py
```

Resultado histórico de aquella corrección: Ruff limpio; mypy limpio en 28 archivos;
34 pruebas superadas;
auditorías de producción y desarrollo sin vulnerabilidades conocidas; 1.008 recursos,
96.768 ofertas y p95 HTTP de 147,292 ms.

## Límites que permanecen

La supresión vive en memoria y el bloqueo cubre un único proceso. Una implantación con
múltiples workers requiere almacenamiento compartido y transacciones. Tampoco hay aún
autenticación, autorización, persistencia durable, privacidad diferencial ni certificación
OpenADR Alliance; la validación de esquema no equivale a certificación.

## Revisión v4.1 posterior

La revisión independiente v4.1 concluyó `GO` para publicación experimental y `NO-GO`
para DER/datos reales. Señaló dos matices medios: wheel diferente entre Windows y Linux
por LF/CRLF en metadata, y mypy limitado entonces a `src/` y `scripts/`. El siguiente
corte normaliza texto, metadata, RECORD y atributos, fija LF en checkout y amplía mypy
a `tests/`. La reconstrucción efectiva en otro SO sigue siendo externa; la regresión
local cubre ambos finales de línea.

Una revisión posterior identificó que incluso con contenidos descomprimidos idénticos,
la salida DEFLATE de zlib podía variar entre Windows y Linux. El empaquetado canónico
ya no depende de esa salida: los wheels y ZIP usan `ZIP_STORED`, y los `tar.gz` se
construyen con bloques DEFLATE almacenados emitidos directamente. Una prueba comprueba
su round-trip y otra exige que todas las entradas del wheel permanezcan almacenadas.
La reconstrucción real en un segundo SO continúa siendo una verificación externa
pendiente, no una prueba que se dé por realizada localmente.

El hallazgo informativo sobre SEC01 se cubre con `security-audit.json`, que conserva
fecha UTC, versión de la herramienta, servicio PyPI y hash del lock. La fuente remota
no proporciona un hash de snapshot de su base y el informe lo declara expresamente.

## Revisión cross-OS de v4.1 y respuesta v4.2

Una reconstrucción independiente real en Linux confirmó que la eliminación de zlib
resolvía la causa anterior, pero descubrió dos diferencias residuales: Python escribía
`ZipInfo.create_system=0` en Windows y `3` en Unix, y `PKG-INFO`/`setup.cfg` conservaban
los finales de línea nativos. El auditor demostró que cambiar únicamente `create_system`
hacía coincidir el wheel byte a byte.

El corte v4.2 fija `create_system=0` en el wheel y en el ZIP de transporte, e incorpora
`PKG-INFO` y `.cfg` al conjunto de texto normalizado. Las pruebas construyen entradas
deliberadamente divergentes Windows/Unix y exigen hashes finales idénticos. Falta que
un tercero repita la construcción de v4.2 en Linux para cerrar empíricamente el claim;
el proyecto no presenta la regresión local como sustituto de esa comprobación.

## Revisión Linux de v4.2 y respuesta v4.3

La reconstrucción real de v4.2 en Linux hizo coincidir el wheel exactamente con el
hash de Windows, cerrando `create_system`, `ZIP_STORED` y la normalización textual.
El sdist conservó una única divergencia: los modos TAR `0666`/`0777` producidos en
Windows frente a `0644`/`0755` en Linux. El auditor igualó los modos y obtuvo hashes
idénticos, aislando el campo como única causa restante.

V4.3 asigna modos canónicos durante `normalize_sdist`: `0755` a directorios y `0644`
a cualquier otra entrada. La regresión crea TAR de entrada con permisos diferentes y
exige tanto igualdad de hash como modos finales exactos. El wheel queda registrado
como reproducible cross-OS mediante ejecución externa; falta repetir únicamente el
sdist v4.3 en Linux para cerrar el último punto empírico.

La revisión externa de v4.3 obtuvo en Linux exactamente los hashes de referencia del
wheel y del sdist; no comunicó hallazgos nuevos y declaró la reproducibilidad global
`RESOLVED`. Posteriormente PKG01 se ejecutó localmente con Docker Linux: la imagen
arrancó DER Flex 0.1.0 desde `site-packages`, respondió `ready` y pasó `pip check`.
V4.4 incorpora un verificador y un informe estructurado para que un tercero pueda
repetir esa prueba; la ejecución local no se presenta como atestación independiente.
