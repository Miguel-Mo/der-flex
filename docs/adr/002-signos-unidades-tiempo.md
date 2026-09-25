# ADR-002: signos, unidades e intervalos

- Estado: aceptada
- Fecha: 2026-09-12

## Contexto

Un campo de potencia sin convención explícita causa inversiones de despacho. S2 expresa potencia eléctrica en W y duraciones en ms; la API propuesta usa kW/kWh e intervalos horarios.

## Decisión

- Potencia de red positiva = importación/consumo; negativa = exportación/producción.
- El adaptador S2 convierte W a kW. La energía se integra y expresa en kWh.
- Fechas en UTC con ISO 8601 y sufijo `Z`.
- Intervalos semiabiertos `[inicio, fin)`, alineados al reloj, con resolución inicial de 15 minutos.
- `upward_capacity_kw` significa reducir importación o aumentar exportación.
- `downward_capacity_kw` significa aumentar importación o reducir exportación.
- Las fases se conservan durante ingestión. v0.1 solo agrega recursos `ELECTRIC.POWER.L1`; la agregación trifásica requiere otra decisión.

## Consecuencias

Todos los límites se convierten una sola vez en el adaptador. Las pruebas incluirán consumo, exportación, cero y cruces de intervalo.
