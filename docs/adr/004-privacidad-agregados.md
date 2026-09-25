# ADR-004: privacidad de agregados

- Estado: aceptada para datos sintéticos; requiere revisión antes de datos reales
- Fecha: 2026-09-12

## Contexto

Las curvas energéticas pueden permitir singularización, vinculación o inferencia. La seudonimización sigue siendo tratamiento de datos personales y un umbral mínimo no demuestra por sí solo anonimización.

## Decisión

- La API pública nunca devuelve identificadores de recursos.
- Umbral inicial `k=10` de participantes distintos por zona e intervalo.
- Las celdas bajo umbral se suprimen por completo.
- Una celda publicada deja de mostrarse si cambia su oferta o capacidad residual.
- No se publica un intervalo adyacente cuya cohorte difiera de otra ya publicada.
- Zonas y resoluciones proceden de catálogos cerrados; no se permiten cohortes arbitrarias.
- Se prueban ataques de diferencia entre consultas adyacentes.
- Hasta una evaluación jurídica/técnica documentada, cualquier dato real se trata como personal.
- Antes de un piloto: DPIA, base jurídica, minimización, retención, control de acceso y análisis de singularización/vinculación/inferencia.

## Consecuencias

La disponibilidad pública puede disminuir tras cambios de oferta, cohorte o reserva.
La política es conservadora y vive en memoria en el MVP; no equivale a privacidad
diferencial ni debe escalarse a varios procesos sin estado compartido.
