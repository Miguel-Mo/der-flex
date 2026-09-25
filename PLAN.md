# Plan de ejecución — API abierta de agregación de flexibilidad DER

**Estado:** hitos 0–6 completados para distribución experimental offline
**Objetivo:** entregar una implementación open source, reproducible y demostrable que reciba flexibilidad de varios recursos/HEMS mediante S2, la normalice y agregue sin exponer hogares individuales, y permita consultar, reservar y activar esa flexibilidad mediante una API pública. Como integración exterior de referencia se añadirá un adaptador OpenADR 3.

## Progreso

| Hito | Estado |
|---|---|
| 0 — Descubrimiento y decisiones | Completado el 2026-09-12 |
| 1 — Rebanada vertical con batería | Completado el 2026-09-12 |
| 2 — Agregación heterogénea y privacidad | Completado el 2026-09-12 |
| 3 — Reserva, despacho y activación | Completado el 2026-09-12 |
| 4 — Adaptador OpenADR 3 | Completado el 2026-09-12 |
| 5 — Endurecimiento y experiencia de uso | Completado el 2026-09-12 |
| 6 — Publicación y validación externa | Completado en alcance offline el 2026-09-14 |
| 7 — Puerta hacia piloto real | Pendiente; requiere señal externa |

El plan detallado para cerrar los hallazgos de las revisiones adversariales y preparar
la siguiente evaluación está en
[`docs/plan-proxima-revision.md`](docs/plan-proxima-revision.md).

## 1. Decisiones de alcance

### Incluido en la primera versión pública

- Tres recursos simulados: batería doméstica, cargador de vehículo eléctrico y bomba de calor.
- Comunicación S2 entre cada Resource Manager (RM) simulado y un Customer Energy Manager (CEM) del agregador.
- Primer tipo de control: Power Envelope Based Control (PEBC).
- Normalización a un modelo común independiente de S2.
- Agregación temporal y geográfica con supresión por umbral mínimo de participantes.
- API REST versionada para consultar, reservar, cancelar y activar flexibilidad.
- Notificaciones webhook de cambios de estado de activaciones.
- Adaptador OpenADR 3 de referencia en el límite exterior.
- Persistencia exclusivamente en memoria para la demo; interfaces preparadas para sustituirla.
- OpenAPI, pruebas automatizadas, contenedores y demo reproducible sin hardware.

### Fuera de alcance

- Conexión a mercados eléctricos reales, pujas, precios, liquidación o facturación.
- Control directo mediante Matter, OCPP, EEBus u otros protocolos de dispositivo.
- Compatibilidad con todos los tipos de control S2.
- Certificación S2 u OpenADR y afirmaciones de conformidad certificada.
- Multiempresa, alta disponibilidad y seguridad de producción.
- Uso de datos personales o telemetría de hogares reales durante el MVP.
- GraphQL. Se elige REST para mantener una sola superficie contractual; podrá añadirse tras validar una necesidad real.

## 2. Principios de diseño

1. **Primero una rebanada vertical.** El primer hito debe recorrer simulador → S2 → normalización → agregado → consulta, aunque solo incluya una batería.
2. **Dominio independiente del protocolo.** Ningún tipo S2 u OpenADR cruza el límite del adaptador; el núcleo solo conoce ofertas, intervalos, reservas y activaciones.
3. **La unidad de agregación es un intervalo.** Los datos se expresan en UTC, con intervalos semiabiertos `[inicio, fin)` y resolución explícita.
4. **No sumar capacidades incompatibles.** La potencia flexible, la energía disponible, la dirección y la confianza se calculan por separado.
5. **Privacidad por diseño.** Los identificadores de recursos nunca aparecen en respuestas públicas; una celda que no cumpla la política se suprime completamente.
6. **Determinismo.** Una semilla fija debe producir la misma simulación y los mismos resultados de prueba.
7. **Estándares en los bordes.** S2 se usa hacia los recursos y OpenADR como adaptador externo, sin convertir el modelo interno en una copia de ninguno.

## 3. Arquitectura objetivo

```text
┌────────────────────────── simulación ────────────────────────────┐
│ Batería RM       EVSE RM       Bomba de calor RM                 │
└─────────────── S2 JSON sobre WebSocket / S2 Connect ────────────┘
                              │
                              ▼
                    Adaptador S2 (rol CEM)
                              │
                  ofertas normalizadas internas
                              ▼
┌──────────────────── núcleo de agregación ───────────────────────┐
│ registro → validación → series temporales → privacidad          │
│                    → reservas → activaciones                     │
└─────────────────────────────────────────────────────────────────┘
                    │                         │
                    ▼                         ▼
              API REST v1             Adaptador OpenADR 3
                    │
                    └── webhooks de activación
```

Se implementará como **monolito modular** más tres procesos simuladores. Para el MVP es más fácil de ejecutar y depurar que un conjunto de microservicios, sin impedir separar adaptadores o almacenamiento después.

## 4. Contrato de dominio

El borrador original con un único `available_power_kw` es ambiguo: no distingue aumento/reducción de consumo, importación/exportación ni capacidad ya reservada. El contrato interno mínimo será una serie de intervalos como esta:

```json
{
  "schema_version": "1.0",
  "zone_id": "ES-MA-29700",
  "interval_start": "2026-09-12T18:00:00Z",
  "interval_end": "2026-09-12T18:15:00Z",
  "baseline_power_kw": 31.2,
  "upward_capacity_kw": 8.4,
  "downward_capacity_kw": 12.7,
  "upward_energy_kwh": 1.9,
  "downward_energy_kwh": 2.8,
  "participant_count": 37,
  "confidence": 0.82,
  "generated_at": "2026-09-12T17:55:00Z"
}
```

Convenciones que se fijarán en un ADR:

- `baseline_power_kw > 0`: importación neta prevista desde la red; `< 0`: exportación.
- `upward_capacity_kw`: capacidad para aumentar la inyección neta o reducir la importación.
- `downward_capacity_kw`: capacidad para reducir la inyección neta o aumentar la importación.
- Potencia en kW y energía en kWh en la API; los adaptadores convierten las unidades del protocolo.
- Intervalos por defecto de 15 minutos, configurables y alineados al reloj.
- `confidence` en `[0,1]`, calculada inicialmente mediante una regla documentada basada en frescura, disponibilidad y error histórico simulado; no será una probabilidad calibrada hasta disponer de datos reales.
- Los totales publicados representan capacidad residual: oferta válida menos reservas activas o confirmadas.

Entidades internas:

- `Resource`: identidad interna, tipo, zona, adaptador y estado de conexión.
- `FlexibilityOffer`: límites y energía de un recurso por intervalo, con versión y caducidad.
- `Aggregate`: suma conservadora por zona e intervalo tras aplicar privacidad.
- `Reservation`: capacidad retenida para uno o más intervalos.
- `Activation`: instrucción despachada y su resultado observado.

Estados de reserva: `pending → confirmed → activated → completed`; salidas alternativas `rejected`, `cancelled`, `expired` y `failed`. Toda transición será validada y auditable.

## 5. API pública v1

### Consulta

- `GET /api/v1/flexibility?zone_id=&from=&to=&resolution=`
- `GET /api/v1/flexibility/zones`
- `GET /health/live`
- `GET /health/ready`

### Reserva y activación

- `POST /api/v1/reservations`
- `GET /api/v1/reservations/{reservation_id}`
- `DELETE /api/v1/reservations/{reservation_id}`
- `POST /api/v1/reservations/{reservation_id}/activate`
- `GET /api/v1/activations/{activation_id}`

Los `POST` aceptarán `Idempotency-Key`. Las reservas usarán control de concurrencia atómico para impedir vender dos veces la misma capacidad. Los errores seguirán `application/problem+json`. OpenAPI será generado y validado en CI.

El webhook enviará eventos `activation.accepted`, `activation.started`, `activation.completed` y `activation.failed`. En la demo tendrá reintentos acotados y secreto compartido opcional; autenticación fuerte, rotación de claves y entrega duradera quedan señaladas como requisitos de producción.

## 6. Política de privacidad del MVP

La demo solo usa datos sintéticos, pero se construye el control que necesitaría un piloto:

- Umbral `k` configurable, inicialmente `k = 10`, aplicado a participantes distintos por zona e intervalo.
- Si cualquier intervalo solicitado queda por debajo del umbral, ese intervalo se devuelve como `suppressed`; no se redondea ni se publica un total parcial.
- Prohibición de filtros arbitrarios que permitan ataques por diferencia.
- Zonas tomadas de una lista cerrada y con jerarquía controlada; no se aceptan coordenadas ni agrupaciones definidas por el cliente.
- Retención mínima de ofertas individuales y separación entre identidad del recurso y datos operativos.
- Registro de consultas y prueba automática contra ataques simples de resta entre respuestas.
- Presupuesto de evaluación antes de un piloto real: DPIA, base jurídica, retención, derechos de interesados, riesgo de singularización/vinculación/inferencia y necesidad de ruido estadístico o zonas adaptativas.

El umbral por sí solo **no permite afirmar anonimización RGPD**. Con datos reales, el sistema se tratará como procesamiento de datos personales hasta que una evaluación documentada demuestre lo contrario.

## 7. Plan por hitos y criterios de salida

### Hito 0 — Descubrimiento y decisiones irreversibles (3–5 días)

Trabajo:

- Congelar versiones evaluadas de `s2-python`, `s2-json` y S2 Connect.
- Ejecutar y capturar una sesión de referencia RM↔CEM.
- Mapear mensajes PEBC a `FlexibilityOffer`, incluyendo límites, pronóstico, selección y rechazo.
- Evaluar Python compatible, modelo de concurrencia y API real de la librería.
- Escribir ADR-001 (PEBC primero), ADR-002 (convenciones de signo/unidades), ADR-003 (REST), ADR-004 (privacidad) y ADR-005 (OpenADR en el borde).

Criterio de salida: un script mínimo intercambia mensajes S2 válidos y existen fixtures JSON validados contra el esquema oficial. Si la librería no cubre transporte, se implementa un adaptador WebSocket pequeño; no se bifurca el estándar.

### Hito 1 — Rebanada vertical con una batería (1 semana)

Trabajo:

- Crear el esqueleto del repositorio, configuración, tipado, lint y pruebas.
- Implementar una batería RM determinista: estado de carga, potencia de carga/descarga, eficiencia, reserva mínima y evolución temporal.
- Implementar el adaptador CEM S2 y la traducción PEBC.
- Guardar ofertas en memoria y exponer `GET /flexibility` para una sola zona.

Criterio de salida: un solo comando inicia la demo; una prueba end-to-end modifica el estado de la batería, recibe una oferta S2 y observa el agregado esperado por REST.

### Hito 2 — Agregación heterogénea y privacidad (1–2 semanas)

Trabajo:

- Añadir perfiles RM de EVSE y bomba de calor con restricciones distintas.
- Soportar varias instancias por perfil, desconexiones, ofertas caducadas y relojes desalineados.
- Implementar remuestreo a intervalos, agregación conservadora, política `k` y confianza.
- Añadir escenarios reproducibles: tarde normal, pico de demanda, vehículo que se marcha y pérdida de conectividad.

Criterio de salida: al menos 30 recursos simulados en tres zonas; los agregados coinciden con cálculos de referencia, una zona bajo umbral se suprime y una oferta caducada nunca se publica.

### Hito 3 — Reserva, despacho y activación (1–2 semanas)

Trabajo:

- Implementar la máquina de estados e idempotencia.
- Asignar una reserva entre recursos mediante una estrategia determinista y conservadora.
- Traducir la activación a instrucciones PEBC admisibles.
- Recoger aceptación/rechazo y potencia simulada observada.
- Implementar webhook con reintento y trazabilidad por `correlation_id`.

Criterio de salida: dos reservas concurrentes no exceden la capacidad; una activación recorre API → recursos → resultado; los rechazos parciales ajustan el estado y la capacidad residual correctamente.

### Hito 4 — Adaptador OpenADR 3 (1 semana)

Trabajo:

- Definir el caso de uso exacto y su mapeo en un ADR: programa/evento exterior hacia reserva/activación interna y reporte agregado de vuelta.
- Generar o validar el cliente desde el OpenAPI normativo de OpenADR 3.
- Crear un VTN ficticio o fixtures contractuales; no afirmar certificación.
- Mantener OpenADR como módulo opcional para que la demo central no dependa de servicios externos.

Criterio de salida: un evento OpenADR de prueba produce una activación interna y un reporte agregado verificable, con pruebas contractuales de los payloads.

### Hito 5 — Endurecimiento del MVP y experiencia de uso (1 semana)

Trabajo:

- Pruebas unitarias, de contrato, integración, end-to-end y propiedades del agregador.
- Casos de fallo: reconexión, duplicados, mensajes fuera de orden, reserva expirada, webhook caído y cambio de hora.
- Límites de petición, validación estricta, secretos fuera del repositorio y análisis de dependencias.
- Métricas y logs estructurados sin identificadores públicos de recursos.
- Docker Compose, datos de ejemplo, colección HTTP/curl y diagrama de secuencia.

Criterio de salida: clon limpio + un comando + menos de cinco minutos hasta la primera consulta; suite verde; demo de 100 recursos durante una hora simulada sin doble reserva ni fuga de identificadores.

### Hito 6 — Publicación y validación externa (3–5 días)

Trabajo:

- Elegir Apache-2.0 para alinearse con los proyectos S2 empleados y aportar una concesión expresa de patentes.
- Completar README, LICENSE, CONTRIBUTING, CODE_OF_CONDUCT, SECURITY, changelog y guía de arquitectura.
- Publicar `v0.1.0` con artefactos reproducibles y limitaciones explícitas.
- Presentar una demo corta y un cuestionario concreto a FAN/S2, LF Energy, comunidades energéticas y posibles agregadores.

Criterio de salida: release pública instalable, al menos una sesión de feedback externo agendada y lista priorizada de problemas reales. Después se congela el desarrollo de funcionalidades durante dos semanas para escuchar.

### Hito 7 — Puerta hacia piloto real (solo con señal externa)

No forma parte de v0.1. Se abre únicamente si existe un socio con caso de uso, datos y responsable de decisión. Requerirá persistencia, autenticación/autorización, gestión de secretos, observabilidad, colas duraderas, modelo de consentimiento/base jurídica, DPIA, pruebas de carga y plan operativo.

## 8. Estructura inicial del repositorio

```text
.
├── src/der_flex/
│   ├── api/                 # REST, DTO y errores
│   ├── domain/              # entidades, reglas y puertos
│   ├── aggregation/         # intervalos, capacidad y privacidad
│   ├── reservations/        # asignación y máquina de estados
│   ├── adapters/
│   │   ├── s2/
│   │   ├── openadr/
│   │   ├── webhooks/
│   │   └── memory/
│   └── observability/
├── simulators/
│   ├── battery_rm/
│   ├── evse_rm/
│   └── heat_pump_rm/
├── tests/{unit,contract,integration,e2e}/
├── docs/adr/
├── examples/
├── compose.yaml
├── pyproject.toml
└── README.md
```

Stack propuesto: Python, FastAPI, Pydantic, `s2-python`, WebSockets, pytest, Hypothesis, Ruff y mypy. La versión exacta de Python y el gestor de entorno se fijan en el Hito 0 después de probar compatibilidad. CI ejecutará formato, lint, tipos, tests, validación OpenAPI, análisis de dependencias y construcción del contenedor.

## 9. Estrategia de pruebas

- **Unidad:** conversión de signos/unidades, solapamiento de intervalos, energía, confianza y estados.
- **Propiedades:** el agregado nunca supera la suma admisible, la capacidad residual nunca es negativa y el orden de llegada no cambia el resultado final versionado.
- **Contrato:** mensajes S2 contra `s2-json`, OpenAPI público y payloads OpenADR.
- **Integración:** WebSocket, reconexión, almacenamiento en memoria y webhook.
- **End-to-end:** escenarios completos con reloj virtual y semillas fijas.
- **Privacidad:** umbral, consultas limítrofes, diferencias entre ventanas y ausencia de identificadores.
- **Rendimiento:** objetivo inicial documentado, no contractual: 1.000 recursos simulados, 96 intervalos futuros y p95 de consulta inferior a 500 ms en un portátil de desarrollo.

## 10. Riesgos y mitigación

| Riesgo | Mitigación / decisión |
|---|---|
| Semántica S2 mal traducida | Fixtures oficiales, ADR de mapeo y revisión con la comunidad S2 antes de ampliar tipos de control. |
| PEBC insuficiente para ciertos recursos | Es válido para la primera rebanada; FRBC será el siguiente candidato para baterías térmicas/energéticas solo tras validar el núcleo. |
| Suma de flexibilidad físicamente imposible | Agregación conservadora por dirección, energía e intervalo; simulaciones con restricciones intertemporales. |
| Doble reserva | Operación atómica, idempotencia y capacidad residual como invariante probado. |
| Confundir agregación con anonimización | Supresión más evaluación de singularización, vinculación e inferencia; asesoría/DPIA antes de datos reales. |
| Acoplamiento prematuro a OpenADR | Adaptador opcional en el Hito 4; el contrato de dominio sigue independiente. |
| Demo técnicamente buena sin usuarios | Entrevistas y demostración tempranas; puerta explícita antes de invertir en producción. |
| Dependencias o estándar en evolución | Versiones fijadas, revisión del ecosistema antes del release y pruebas contractuales. |

## 11. Orden del backlog después de v0.1

Solo se prioriza con evidencia de usuarios:

1. Persistencia PostgreSQL/Timescale y recuperación tras reinicio.
2. Autenticación OAuth2/OIDC, autorización por ámbito y firma robusta de webhooks.
3. FRBC para representar almacenamiento con mayor fidelidad.
4. Planificación/optimización multiintervalo y calibración real de confianza.
5. Adaptadores adicionales (OCPP, EEBus o APIs de fabricantes), cada uno justificado por un piloto.
6. Despliegue de referencia, alta disponibilidad, SLO y operaciones.
7. Evaluación de certificación formal S2/OpenADR.

## 12. Definición global de terminado para v0.1

El objetivo se considera alcanzado cuando una persona ajena al proyecto puede clonar el repositorio, iniciar todos los componentes con un único comando, consultar flexibilidad anonimizada de varios recursos simulados, reservarla y activarla, observar la respuesta de los recursos por S2 y repetir el mismo flujo desde un evento OpenADR de prueba. Todo debe estar documentado, cubierto por pruebas automáticas y sin exponer datos individuales en la API.

## 13. Fuentes técnicas de partida

- Documentación S2: https://docs.s2standard.org/
- S2 JSON: https://github.com/flexiblepower/s2-json
- S2 Python: https://github.com/flexiblepower/s2-python
- Repositorios FAN: https://github.com/flexiblepower
- OpenADR 3: https://www.openadr.org/openadr-3-0
- EDPB, anonimización y seudonimización: https://www.edpb.europa.eu/topics/ai-and-technology/anonymisation-pseudonymisation_en
