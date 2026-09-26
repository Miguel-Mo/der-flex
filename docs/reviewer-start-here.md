# DER Flex 0.2.0 — inicio de la revisión v7.0

Este expediente está diseñado para revisarse sin acceder al sitio web del proyecto.
No confíe inicialmente en `evidence.json`: verifique primero los bytes recibidos.

La distribución preferida es el `.tar.gz` canónico. El ZIP de transporte contiene
solo ese TAR y dos ficheros de texto para impedir que plataformas de subida filtren
selectivamente `.py`, wheels o directorios anidados.

## Orden recomendado

1. Si recibió el ZIP de transporte, extraiga el `.tar.gz`, compruebe el hash indicado
   en `TAR-SHA256.txt` y después extraiga el TAR conservando su directorio raíz.
2. Desde la raíz del TAR ejecute `python verify_bundle.py`. Solo necesita Python 3 y
   no usa red.
3. Lea `BUNDLE-MANIFEST.json`, `TRACEABILITY.md`, `EXPECTED_RESULTS.md` y
   `REVIEW_PROMPT.md`.
4. Para una prueba de runtime sin red use Python 3.13 y los wheels de
   `source/vendor/wheelhouse` junto con `source/requirements-runtime.lock`.
5. Para repetir pytest, Ruff, mypy, auditoría, fuzzing, mutaciones y construcción,
   cree una venv vacía, instale `source[dev]` aplicando
   `source/requirements-runtime.constraints` y ejecute
   `python source/scripts/verify_release.py` desde `source/`. El extra de desarrollo
   incluye expresamente el backend fijado que usa la construcción `--no-isolation`.
6. Para cerrar el hallazgo de reproducibilidad, reconstruya preferiblemente en un
   segundo SO y compare los hashes normalizados. La revisión del algoritmo determinista
   no sustituye esa ejecución externa.
7. Si Docker está disponible, ejecute `python source/scripts/verify_container.py`
   desde `source/` después de `verify_release.py`; el script construye una imagen Linux,
   prueba versión, procedencia desde `site-packages`, readiness y `pip check`.
8. Para OUT01 proporcione PostgreSQL real mediante `DER_FLEX_TEST_DATABASE_URL` y
   ejecute `python -m pytest -q source/tests/test_postgres_outbox.py` desde la raíz
   extraída. Sin esa ejecución, OUT01 debe permanecer `NOT_VERIFIABLE`.
9. Para AUTH01 ejecute `tests/test_oidc_security.py` y
   `tests/test_api_authorization.py`; pruebe también la matriz completa con PostgreSQL.
   No sustituya firma, emisor, audiencia, tiempo, tenant y rotación por inspección
   narrativa ni confunda claves RSA locales con certificación de un IdP operativo.
10. Para PRIV02 ejecute `tests/test_privacy_publication.py` y las pruebas PostgreSQL de
    presupuesto/snapshot. Compare ventanas solapadas e identidades múltiples antes y
    después de cambios, reservas y reinicios. Lea el modelo de amenazas: no se reclama
    anonimización formal ni privacidad diferencial.

## Comprobación offline mínima

En PowerShell, desde el directorio extraído:

```powershell
py -3.13 -m venv .review-venv
.\.review-venv\Scripts\python.exe -m pip install --no-index --no-deps `
  --require-hashes --find-links source\vendor\wheelhouse `
  -r source\requirements-runtime.lock
.\.review-venv\Scripts\python.exe -m pip install --no-index --no-deps `
  evidence\der_flex-0.2.0-py3-none-any.whl
.\.review-venv\Scripts\python.exe -m pip check
```

En Linux x86-64 sustituya el ejecutable por `.review-venv/bin/python`.

## Alcance honesto

El objetivo verificable es una publicación experimental. Docker, firma/tag públicos,
esquema oficial OpenADR, laboratorios externos y certificación de un IdP operativo no
se declaran completados. Autenticación OIDC, ámbitos y aislamiento multi-tenant sí se
declaran implementados localmente bajo AUTH01. El uso con datos o DER reales continúa
en `NO-GO` por los hitos posteriores todavía abiertos.
