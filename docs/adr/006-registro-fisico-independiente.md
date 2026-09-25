# ADR 006 — Registro físico independiente de S2

**Estado:** aceptada, 12 de septiembre de 2026.

## Contexto

Las restricciones PEBC son declaraciones de un recurso conectado y no bastan para
establecer su capacidad física. Aceptar los límites físicos como argumentos libres del
mismo flujo de ingestión mantendría una única frontera de confianza.

## Decisión

Cada `resource_id` debe existir en un registro aprovisionado por el operador antes de
normalizar mensajes S2. El registro conserva zona, tipo, potencia, energía, rampa,
estado habilitado y una versión monotónica. El adaptador consulta el registro y no
acepta envolventes físicas proporcionadas por el mensaje ni por su llamador.

La utilidad `build_simulator_registry` existe exclusivamente para demos y pruebas. Un
despliegue real deberá alimentar la misma interfaz desde administración autenticada y
almacenamiento durable.

## Consecuencias

- Recursos desconocidos, deshabilitados o presentados en otra zona se rechazan antes
  de procesar su flexibilidad.
- Un mensaje PEBC puede reducir la envolvente aprovisionada, nunca ampliarla.
- Reutilizar una versión con contenido distinto o registrar una versión anterior es un
  error explícito.
- El registro actual sigue siendo local y en memoria; persistencia y autorización se
  mantienen como hitos posteriores.
