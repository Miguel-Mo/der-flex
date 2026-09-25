# Hito 2 — Agregación heterogénea y privacidad

**Estado:** completado el 12 de septiembre de 2026.

## Entregado

- 36 recursos deterministas: cuatro baterías, cuatro EVSE y cuatro bombas de calor en cada una de tres zonas.
- Normalización PEBC común para los tres perfiles.
- Remuestreo conservador de pronósticos arbitrarios a intervalos alineados de 15 minutos.
- Agregación por zona, intervalo, clase de producto y consecuencia.
- Confianza ponderada por capacidad; para el remuestreo se usa la confianza mínima de los segmentos que cubren el intervalo.
- Umbral de publicación `k=10` en la demo.
- Supresión de grupos bajo umbral, ofertas caducadas y zonas insuficientes.
- Retirada inmediata de ofertas al desconectar un recurso.
- Escenarios reproducibles: tarde normal, pico de demanda, salida de vehículos y pérdida de conectividad.

## Regla conservadora de remuestreo

Un intervalo de 15 minutos solo se publica cuando existe cobertura completa y continua. El baseline es la media temporal ponderada. La potencia flexible publicada es el mínimo disponible durante todos los segmentos del intervalo; la energía se integra por solape. Los huecos parciales se descartan.

Esta regla sacrifica volumen publicado para evitar anunciar capacidad que no está disponible durante todo el producto temporal.

## Privacidad implementada

La unidad contada es un `resource_id` distinto, no el número de mensajes. Cada agregado requiere al menos diez participantes. La lista de zonas también oculta zonas activas que no llegan al umbral. Los identificadores privados no forman parte del modelo de respuesta.

Esto es una salvaguarda técnica para el simulador, no una declaración de anonimización RGPD.

## Pruebas de salida

- Una flota de 36 recursos contiene los tres perfiles en cada zona.
- Cada zona normal publica 12 participantes.
- Desconectar tres recursos de una zona hace que deje de publicarse.
- Las ofertas caducadas no aparecen.
- Un pronóstico que comienza cinco minutos tarde se realinea sin rellenar huecos ni sobreestimar potencia.
- Los cuatro escenarios producen siempre la misma composición.
