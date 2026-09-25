# ADR-001: PEBC como primer tipo de control

- Estado: aceptada
- Fecha: 2026-09-12

## Contexto

El MVP necesita demostrar ingestión, normalización y activación antes de soportar todos los modelos S2. PEBC describe límites de potencia y es más pequeño que los modelos basados en modos de operación. Sin embargo, una instrucción PEBC limita al recurso: no garantiza un punto de operación ni una respuesta de mercado firme.

## Decisión

Implementar PEBC primero y clasificar toda flexibilidad derivada como `BEST_EFFORT_PEBC`. Exigir `PowerForecast` para publicar una cantidad relativa al baseline. Admitir `PEBC.EnergyConstraint` cuando exista y conservar `VANISH`/`DEFER` como categorías distintas. No afirmar firmeza ni ejecución a partir de `ReceptionStatus`.

## Consecuencias

- Se obtiene una rebanada vertical pequeña y verificable.
- Reservar significa retener una oportunidad técnica; activar significa solicitar un sobre admisible.
- El resultado debe confirmarse con `InstructionStatusUpdate` y medidas.
- FRBC será el siguiente candidato cuando el dominio y la API estén estabilizados.
