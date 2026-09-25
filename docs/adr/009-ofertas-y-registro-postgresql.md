# ADR 009 — Ofertas y registro físico durables

**Estado:** aceptada, 25 de septiembre de 2026.

## Contexto

El ledger de reservas sobrevivía a reinicios, pero la autoridad que lo alimentaba no:
el registro físico, las ofertas normalizadas, la posición temporal de cada fuente y las
cohortes publicadas seguían en memoria. Reiniciar podía olvidar una desconexión o volver
a publicar una celda ya suprimida.

## Decisión

Se implementan `PostgresResourceRegistry` y `PostgresOfferStore` detrás de las mismas
interfaces usadas por el núcleo. El esquema versión 2 conserva la envolvente física y
su versión, ofertas, posición `(source_epoch, source_sequence)`, estado de desconexión,
cohortes y supresión permanente.

Las escrituras del almacén de ofertas se serializan y adquieren los mismos bloqueos de
producto que el ledger de reservas. Así, una actualización o retirada de oferta no
puede intercalarse con la decisión transaccional de reservar ese producto. Las
restricciones de base de datos vuelven a imponer intervalos, magnitudes, tipos y límites
físicos incluso frente a una escritura que eluda los modelos Pydantic.

Una versión nueva del registro físico revoca dentro de la misma transacción todas las
ofertas anteriores del recurso, suprime las celdas ya publicadas y marca la fuente como
desconectada. El RM debe avanzar su posición antes de volver a ofrecer capacidad bajo la
nueva envolvente.

## Consecuencias

- Reiniciar la API no pierde recursos, ofertas, posiciones ni decisiones de privacidad.
- Una sesión desconectada debe avanzar su posición también después de un reinicio.
- Versiones de aprovisionamiento repetidas o regresivas conservan la semántica anterior.
- La serialización global de escrituras de oferta prima corrección sobre throughput en
  este incremento; podrá particionarse por zona/producto tras medir contención.
- La entrega de activaciones y webhooks aún necesita una outbox transaccional.

## Evidencia exigida

- Consultas idénticas desde una instancia nueva con el mismo instante de generación.
- Rechazo de posiciones antiguas, contenido conflictivo y reprovisionamiento regresivo.
- Supresión y desconexión conservadas después de recrear el almacén.
- Coordinación con reservas y restricciones de integridad ejecutadas en PostgreSQL real.
