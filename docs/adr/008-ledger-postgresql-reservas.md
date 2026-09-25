# ADR 008 — Ledger PostgreSQL para reservas distribuidas

**Estado:** aceptada, 25 de septiembre de 2026.

## Contexto

El bloqueo `RLock` de `v0.1.0` solo coordinaba hilos de un proceso. Reiniciar el proceso
perdía reservas, claves idempotentes, activaciones y el historial de supresión pública;
dos réplicas tampoco compartían una autoridad de capacidad.

## Decisión

Se introduce una interfaz de unidad de trabajo con dos implementaciones. La variante en
memoria mantiene la experiencia local. La variante PostgreSQL conserva reservas,
asignaciones, claves idempotentes, activaciones, instrucciones y residuales públicos.

Cada creación de reserva abre una transacción y adquiere bloqueos asesores
`pg_advisory_xact_lock` derivados de la celda de producto y de la clave idempotente.
Todos los cálculos de capacidad y el `INSERT` se ejecutan dentro de esa transacción. El
orden lexicográfico de los bloqueos evita que dos peticiones que compartan más de una
clave los adquieran en orden inverso. Una restricción única sobre la clave idempotente
añade defensa en profundidad.

El esquema versión 1 se aplica de forma idempotente. La disponibilidad de PostgreSQL
forma parte de readiness cuando este backend está configurado.

## Consecuencias

- Dos procesos con la misma instantánea de ofertas no pueden vender dos veces una celda.
- Idempotencia, activaciones, caducidad y supresión sobreviven a reinicios del servicio.
- Una indisponibilidad de base de datos impide readiness y nuevas transacciones.
- Los bloqueos asesores dependen de PostgreSQL y obligan a que toda escritura de reserva
  atraviese esta unidad de trabajo.
- Las ofertas y el registro físico siguen en memoria. Este ADR cierra el ledger de
  reservas, no la recuperación completa del sistema ni el uso con DER reales.

## Evidencia exigida

- Carrera simultánea desde dos backends/conexiones distintos: una reserva confirmada y
  una rechazada cuando ambas agotarían la misma capacidad.
- Repetición idempotente, lectura, cancelación y activación desde una nueva instancia.
- Caducidad y supresión pública conservadas tras recrear el servicio.
- Pruebas unitarias con memoria y pruebas de integración con PostgreSQL real en CI.
