# Contribuir a DER Flex

Gracias por ayudar a mejorar el proyecto. Antes de proponer una funcionalidad amplia,
abre una incidencia que describa el caso de uso, el protocolo implicado y cómo se
preservará la privacidad de los participantes.

## Entorno local

Se requiere Python 3.13 y Docker es opcional.

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m mypy src scripts
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m pip_audit . --progress-spinner off
```

## Criterios para cambios

- Conserva el núcleo de dominio independiente de S2 y OpenADR.
- No expongas `resource_id`, telemetría doméstica ni filtros que permitan ataques por
  diferencia en contratos públicos.
- Usa UTC e intervalos semiabiertos `[inicio, fin)`.
- Añade pruebas que fallen antes del cambio y cubran el comportamiento nuevo.
- Actualiza los ADR cuando cambie una decisión arquitectónica.
- No afirmes certificación ni conformidad formal sin la evaluación correspondiente.

Las contribuciones se ofrecen bajo Apache License 2.0. Al enviar un cambio confirmas
que tienes derecho a licenciarlo en esos términos.
