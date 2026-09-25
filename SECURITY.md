# Política de seguridad

## Versiones compatibles

Hasta la primera release solo se mantiene la rama principal. Este repositorio es una
demostración y no debe usarse para controlar activos reales ni procesar datos personales
sin el trabajo de producción descrito en el plan.

## Informar de una vulnerabilidad

No publiques detalles explotables en una incidencia abierta. Usa el aviso privado de
seguridad del proveedor donde esté alojado el repositorio. Si ese canal todavía no está
disponible, contacta de forma privada con el responsable del repositorio antes de
divulgar el hallazgo.

Incluye versión o commit, impacto, pasos de reproducción, prueba de concepto mínima y
posibles mitigaciones. No accedas a datos ajenos, no interrumpas servicios y no realices
ingeniería social.

El objetivo inicial es acusar recibo en tres días laborables, evaluar severidad en siete
y coordinar una corrección y divulgación. Estos plazos no son un SLA contractual.

## Límites conocidos

- Persistencia en memoria, sin recuperación tras reinicio.
- Sin autenticación, autorización ni aislamiento multiempresa.
- Webhooks con secreto compartido opcional, sin cola duradera.
- Los callbacks exigen HTTPS, bloquean IP no globales y no siguen redirecciones, pero
  un despliegue debe añadir resolución DNS controlada y protección contra *DNS rebinding*.
- Sin certificación S2 u OpenADR.
- El umbral `k` no demuestra anonimización RGPD.
- El bloqueo de doble reserva solo cubre un proceso; varias réplicas requieren una base
  de datos transaccional o un mecanismo de exclusión distribuido.
- Las celdas cambian a estado suprimido si varía una oferta, la cohorte, una cohorte
  adyacente o la capacidad residual publicada. Es una mitigación en memoria, no
  privacidad diferencial; siguen existiendo riesgos de vínculo e inferencia.
