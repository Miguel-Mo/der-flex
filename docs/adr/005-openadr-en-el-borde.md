# ADR-005: OpenADR 3 como adaptador exterior

- Estado: aceptada
- Fecha: 2026-09-12

## Contexto

OpenADR 3 ofrece una API para programas, eventos, reportes y suscripciones. Copiar su modelo dentro del núcleo acoplaría la agregación a un estándar exterior y dificultaría otras integraciones.

## Decisión

Implementar OpenADR 3 como adaptador opcional después de cerrar consulta, reserva y activación internas. Usar su OpenAPI YAML normativo como contrato. Mapear programas/eventos a comandos internos y reportes a resultados agregados. No afirmar conformidad ni certificación.

## Consecuencias

La demo central funciona sin infraestructura OpenADR. El mapeo se prueba mediante fixtures/contratos y cualquier pérdida semántica queda documentada en el adaptador.
