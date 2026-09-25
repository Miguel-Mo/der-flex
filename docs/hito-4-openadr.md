# Hito 4 — Adaptador OpenADR 3

**Estado:** completado el 12 de septiembre de 2026.

## Versión y contrato

El adaptador se fija a **OpenADR 3.1.0**, última versión marcada como liberada en la fecha de corte. La versión 3.1.1 aparece todavía como desarrollo y no se usa como contrato.

- Espejo de especificación revisado:
  `https://github.com/grid-coordination/openadr3-specification`, commit
  `e47c6da57aa470f81073fc214b4e92052bf6c474`.
- SHA-256 del blob Git con LF: `135BC74AF2F733338B55AFA95DF3CB3A1185867E8E41A7D16CDB2DBFAEB3B475`.
- SHA-256 de la copia de trabajo Windows con CRLF: `EF19311C994D89ABF96DC56032701ED8C5F25242B337019FC7DAEFB0F19F0D19`.
- Los fixtures de evento y reporte se validaron directamente contra los esquemas `event` y `reportRequest` del OpenAPI.

La OpenADR Alliance identifica el YAML OpenAPI como referencia normativa. Este proyecto implementa un subconjunto interoperable, pero no afirma certificación.

## Mapeo implementado

| OpenADR 3.1.0 | DER Flex |
|---|---|
| `event.targets` | Una zona pública autorizada. |
| `intervalPeriod` | Ventana de reserva y activación. |
| `DER_FLEX_UPWARD_KW` | Reserva ascendente en kW. |
| `DER_FLEX_DOWNWARD_KW` | Reserva descendente en kW. |
| Identidad de evento + intervalo | Clave idempotente. |
| Resultado de activación | `reportRequest` agregado sin recursos domésticos. |

Los dos payloads `DER_FLEX_*` son extensiones privadas permitidas por `valuesMap`. Se eligieron porque `DISPATCH_SETPOINT_RELATIVE` solo expresa una magnitud positiva y no codifica inequívocamente la dirección del producto de flexibilidad.

## Flujo probado

```text
Evento VTN → validación OpenADR → reserva atómica → activación PEBC
           → resultado agregado → OpenADR reportRequest
```

La repetición del mismo evento devuelve la misma reserva y activación. El reporte contiene únicamente el recurso lógico `aggregate:<zone_id>` y nunca identificadores de hogares.

## Verificación contractual

Con una copia autorizada de la especificación:

```powershell
.\.venv\Scripts\python.exe scripts\verify_openadr_contract.py C:\ruta\3.1.0\openadr3.yaml
```

La comprobación valida tanto `dispatch-event-3.1.0.json` como `activation-report-3.1.0.json` contra el OpenAPI normativo.

## Límites

- No se implementa un VTN ni toda la API OpenADR.
- No se implementan OAuth2, MQTT, descubrimiento ni gestión completa de VEN/recursos.
- Los payloads privados deberán acordarse con el operador del piloto o sustituirse por un perfil certificado aplicable.
