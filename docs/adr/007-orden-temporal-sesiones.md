# ADR 007 — Orden temporal mediante época y secuencia

**Estado:** aceptada, 12 de septiembre de 2026.

## Contexto

Una secuencia monotónica aislada no distingue el reinicio legítimo de un contador de
un replay antiguo. Tampoco basta para detectar observaciones con relojes desviados.

## Decisión

Cada oferta conserva una posición `(source_epoch, source_sequence)`. La época aumenta
al iniciar una sesión nueva y permite reiniciar la secuencia a cero. El almacén mantiene
la mayor posición observada por recurso, invalida su horizonte anterior al avanzar y
rechaza posiciones menores. Tras una desconexión exige una posición estrictamente
mayor antes de admitir de nuevo el recurso.

La ingestión conserva además `observed_at` y lo compara con `received_at`, que representa
el reloj local confiable. Por defecto se admiten observaciones de hasta 15 minutos de
antigüedad y dos minutos en el futuro. Los límites son configurables y sus fronteras son
inclusivas.

## Consecuencias

- Un contador reiniciado solo es válido dentro de una época posterior.
- Una actualización parcial no deja publicadas ofertas antiguas del mismo recurso.
- La capa que termina la sesión S2 debe asignar la época y el instante local de recepción;
  esos valores no deben aceptarse desde el cuerpo de un mensaje remoto.
- El simulador implementa `restart_session` únicamente para reproducir estos estados en
  demos y pruebas.
