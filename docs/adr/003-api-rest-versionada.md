# ADR-003: API REST versionada

- Estado: aceptada
- Fecha: 2026-09-12

## Contexto

El MVP necesita una superficie pública pequeña, documentada y fácil de probar. Mantener REST y GraphQL a la vez duplicaría contratos sin una necesidad validada.

## Decisión

Exponer REST bajo `/api/v1`, generar OpenAPI desde FastAPI y usar `application/problem+json` para errores. Los comandos mutables requerirán `Idempotency-Key`. Las reservas tendrán versión/control atómico para impedir doble asignación.

GraphQL queda fuera de v0.1. Los modelos internos no importarán DTO de FastAPI ni tipos S2/OpenADR.

## Consecuencias

Hay una sola interfaz que probar y documentar. Cualquier cambio incompatible crea `/api/v2`; los cambios compatibles pueden ampliar v1.
