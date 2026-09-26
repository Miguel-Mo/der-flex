# ADR-004: privacidad de agregados

- Estado: endurecida localmente; revisión independiente pendiente antes de datos reales
- Fecha: 2026-09-12

## Contexto

Las curvas energéticas pueden permitir singularización, vinculación o inferencia. La seudonimización sigue siendo tratamiento de datos personales y un umbral mínimo no demuestra por sí solo anonimización.

## Decisión

- La API pública nunca devuelve identificadores de recursos.
- Umbral absoluto `k=10` de participantes distintos por zona e intervalo; la política
  pública rechaza cualquier configuración inferior aunque el almacén permita pruebas
  internas con cohortes menores.
- Las celdas bajo umbral se suprimen por completo.
- Una celda publicada deja de mostrarse si cambia su oferta o capacidad residual.
- No se publica un intervalo adyacente cuya cohorte difiera de otra ya publicada.
- Zonas y resoluciones proceden de catálogos cerrados; no se permiten cohortes arbitrarias.
- Ventanas y `generated_at` se fijan a cadencias UTC de 15 minutos y la consulta máxima
  es de 24 horas.
- El presupuesto de 60 consultas por cadencia se comparte por tenant y persiste en
  PostgreSQL, por lo que nuevas identidades o procesos no lo amplían.
- Potencia, energía, confianza y número de participantes se cuantizan antes de salir.
- Se prueban ataques de diferencia entre consultas adyacentes.
- Hasta una evaluación jurídica/técnica documentada, cualquier dato real se trata como personal.
- Antes de un piloto: DPIA, base jurídica, minimización, retención, control de acceso y análisis de singularización/vinculación/inferencia.

## Consecuencias

La disponibilidad pública puede disminuir tras cambios de oferta, cohorte o reserva.
La política es conservadora y usa estado compartido con PostgreSQL; el backend en memoria
es solo de desarrollo. No equivale a privacidad diferencial. El modelo de atacante,
parámetros, pérdida de utilidad y riesgos residuales están en
`docs/privacy-threat-model.md`.
