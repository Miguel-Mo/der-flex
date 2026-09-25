# Hito 0 — Investigación técnica

**Fecha de corte:** 12 de septiembre de 2026
**Resultado:** completado; se puede iniciar la rebanada vertical del Hito 1.

## Versiones evaluadas

| Componente | Versión/commit evaluado | Decisión |
|---|---|---|
| Python | 3.13 | Versión del proyecto; `s2-python` declara `>=3.9,<3.14`. |
| `s2-python` | PyPI `0.9.1`; repositorio `v0.10.0`, commit `ea46bde1598ee9e73a1313bbdbc59722d6e047c6` | Fijar `0.9.1` en el primer incremento y abrir una actualización separada a `0.10.0` cuando esté publicada. |
| `s2-json` | commit `d58b2f027c7b40374e8d57aee72d1876ed9e0763` | Referencia contractual revisada. El antiguo repositorio `s2-ws-json` fue renombrado. |
| S2 Connect | commit `d434762573279e1761b35f1d527db1b9c99092cc` | Referencia para transporte, emparejamiento y autenticación; solo WebSocket local en v0.1. |
| WebSockets | `13.1` | Dependencia transitiva fijada por `s2-python[ws]`. |

## Hallazgos

1. S2 diferencia el modelo de mensajes (`s2-json`) de S2 Connect, que define descubrimiento, emparejamiento, autenticación y establecimiento del canal.
2. `s2-python` ofrece modelos Pydantic, parser, conexiones cliente WebSocket y manejadores de tipos de control. No ofrece una aplicación CEM completa lista para desplegar; el agregador debe implementar ese rol.
3. PEBC permite que el CEM solicite límites superior/inferior dentro de rangos autorizados por el RM. No ordena un punto de potencia exacto ni garantiza que toda potencia limitada aparezca como respuesta despachable.
4. Para cuantificar flexibilidad respecto a una referencia hacen falta tanto `PEBC.PowerConstraints` como `PowerForecast`. `PEBC.EnergyConstraint` añade límites medios intertemporales cuando el recurso los publica.
5. `consequence_type` distingue energía limitada que desaparece (`VANISH`) de energía diferida (`DEFER`). Este dato debe conservarse; sumar ambos comportamientos sin distinguirlos produciría agregados engañosos.
6. Las cantidades eléctricas S2 están expresadas en vatios. La API pública convertirá a kW/kWh.
7. La documentación usa consumo positivo. Se adopta importación positiva y exportación negativa, pendiente de pruebas de contrato con cada adaptador.
8. PEBC es adecuado para validar el flujo y ofrecer capacidad *best effort*. Una reserva firme necesitará criterios adicionales de elegibilidad y, probablemente, FRBC u otro modelo más expresivo para algunos recursos.
9. El esquema `ID` de `s2-json` acepta identificadores con un patrón alfanumérico, pero `s2-python 0.9.1` tipa varios campos como UUID. Los simuladores usarán UUID para satisfacer el perfil más estricto y se añadirá una prueba de regresión antes de actualizar la dependencia.

## Mapeo PEBC → dominio

| S2 | Uso interno | Regla inicial |
|---|---|---|
| `ResourceManagerDetails.resource_id` | clave privada del recurso | Nunca sale en la API agregada. |
| `PowerForecast.start_time/elements` | baseline por intervalo | `value_expected / 1000` convierte W a kW. Bandas PPR alimentan confianza. |
| `PEBC.PowerConstraints.valid_from/until` | validez de oferta | Una oferta caduca al terminar la validez o al recibir revocación/reemplazo. |
| `AllowedLimitRange` superior | límite superior seleccionable | Determina cuánto puede acotarse la importación/producción por arriba. |
| `AllowedLimitRange` inferior | límite inferior seleccionable | Determina cuánto puede acotarse la importación/producción por abajo. |
| `consequence_type` | categoría de flexibilidad | `VANISH` y `DEFER` se agregan por separado internamente. |
| `PEBC.EnergyConstraint` | restricción intertemporal | Recorta energía ofertable dentro de su ventana; no se reparte dos veces. |
| `PEBC.Instruction` | despacho best effort | Debe referenciar las restricciones vigentes y respetar todos sus rangos. |
| `InstructionStatusUpdate` y medidas | resultado observado | Confirma aceptación/ejecución; nunca se infiere éxito solo del `ReceptionStatus`. |

### Regla provisional de capacidad

Para cada intervalo y cantidad eléctrica se parte del pronóstico `p`. El adaptador calcula límites solicitables conservadores compatibles con las restricciones vigentes. La diferencia entre `p` y dichos límites es una **oportunidad de limitación**, no capacidad firme. La capa de reserva solo podrá publicarla con la clase de producto `BEST_EFFORT_PEBC` hasta que exista confirmación de disponibilidad y una política de entrega.

No se agregan fases eléctricas diferentes como si fueran equivalentes. En v0.1 todos los simuladores serán monofásicos y usarán `ELECTRIC.POWER.L1`.

## Sesión de referencia

El script `scripts/s2_pebc_smoke.py` levanta un servidor CEM local y un RM local sobre WebSocket. La secuencia validada es:

```text
RM  → CEM  Handshake
CEM → RM   Handshake
CEM → RM   HandshakeResponse
RM  → CEM  ResourceManagerDetails
CEM → RM   SelectControlType(PEBC)
RM  → CEM  PEBC.PowerConstraints
RM  → CEM  PowerForecast
CEM → RM   PEBC.Instruction
```

Cada mensaje con identificador recibe un `ReceptionStatus(OK)`. Importante: ese estado confirma recepción y validación, no ejecución de la instrucción.

## Decisión de salida

Se mantiene PEBC como primer tipo de control porque permite cerrar rápidamente un flujo completo y expone las dificultades esenciales de baseline, límites y activación. El producto v0.1 etiquetará las reservas como *best effort* y no afirmará capacidad firme. El siguiente tipo a evaluar será FRBC, pero solo tras completar la rebanada vertical.

## Fuentes

- https://docs.s2standard.org/docs/specs/
- https://docs.s2standard.org/s2-connect/1.0.0/discovery-pairing-authentication/
- https://docs.s2standard.org/model-reference/PEBC/PEBC.AllowedLimitRange/
- https://github.com/flexiblepower/s2-python
- https://github.com/flexiblepower/s2-json
- https://github.com/flexiblepower/s2-connect
