# Modelo de amenazas y política de publicación de flexibilidad

**Estado:** política local implementada; revisión independiente pendiente junto con los
hitos 15 y 16.

## Activos y adversarios

Se protege la contribución y presencia de cada DER, sus cambios de estado y el volumen
o instante de reservas. Se supone un cliente autenticado que conoce información externa,
repite consultas, desplaza ventanas, compara intervalos vecinos, realiza reservas y usa
varias identidades del mismo tenant. También se considera el reinicio de la API y la
ejecución de varios procesos contra PostgreSQL.

No se considera anonimizado ningún dato real. Quedan fuera un proveedor de identidad
comprometido, la colusión entre tenants y operador, la observación directa de la red
eléctrica o un atacante con acceso a las tablas privadas.

## Política ejecutable

| Parámetro | Valor predeterminado | Efecto |
|---|---:|---|
| Cohorte mínima | 10 DER | Suprime zonas/celdas pequeñas |
| Cadencia y alineación | 15 minutos UTC | Impide ventanas arbitrarias |
| Ventana máxima | 24 horas | Acota composición y extracción |
| Consultas | 60 por tenant y cadencia | Varias identidades comparten presupuesto |
| Potencia | pasos de 1 kW | Capacidad se redondea hacia abajo |
| Energía | pasos de 0,25 kWh | Energía se redondea hacia abajo |
| Confianza | pasos de 0,05 | Reduce precisión auxiliar |
| Participantes | bloques de 5 hacia abajo | No publica el tamaño exacto |

Las zonas proceden del catálogo autorizado del token, nunca de la presencia instantánea
de ofertas. Una celda cambia a supresión permanente si cambia la cohorte, cualquier
oferta individual o la capacidad residual; el intervalo vecino también se protege. El
snapshot ya publicado permanece byte a byte estable hasta terminar su cadencia, de modo
que la desaparición se observa como pronto en el siguiente corte. El estado y el
presupuesto viven en PostgreSQL y son atómicos entre procesos. El backend
en memoria conserva alcance exclusivo de desarrollo.

## Ataques y respuesta esperada

- Ventanas solapadas devuelven las mismas celdas cuantizadas y el mismo instante de
  publicación dentro de la cadencia.
- Identidades adicionales no amplían el presupuesto porque la clave es el tenant.
- Una reserva, desconexión, actualización o reprovisionamiento posterior suprime la
  celda en vez de publicar el delta.
- Reiniciar o cambiar de proceso no recupera presupuesto ni olvida supresiones con
  PostgreSQL.
- Las zonas sin datos y las zonas bajo umbral son indistinguibles en el catálogo; su
  consulta devuelve una lista vacía de celdas.
- El conocimiento auxiliar puede confirmar que una celda agregada existe, pero no
  obtiene identificadores ni un delta después de su primera publicación.

## Utilidad perdida

Por celda, la capacidad publicada puede subestimar menos de 1 kW por dirección, la
energía menos de 0,25 kWh y la confianza menos de 0,05. El recuento subestima entre cero
y cuatro participantes. La supresión permanente puede causar una pérdida del 100 % de
una celda después de cualquier cambio; se prioriza confidencialidad sobre frescura.
El límite de consultas también puede provocar denegación de servicio dentro del tenant.

## Decisiones descartadas y riesgo residual

No se implementa privacidad diferencial en este hito. Para declarar un ε harían falta
una sensibilidad operativa acordada por DER, composición temporal, tratamiento de
reservas y una gobernanza del presupuesto; añadir ruido sin eso produciría una cifra
engañosa y podría sobrepublicar capacidad. Las zonas adaptativas también se descartan:
sus cambios revelarían densidad y permitirían nuevas diferencias espaciales.

El redondeo, `k=10`, la cadencia y la supresión no constituyen anonimización formal.
Persisten riesgos de vínculo con fuentes externas, inferencia por conocimiento previo,
abuso autorizado dentro de un tenant y canales laterales operativos. Datos reales exigen
DPIA, base jurídica, minimización/retención acordadas y revisión técnica independiente.

## Condiciones de salida

La implementación local debe superar pruebas de ventanas solapadas, identidades
múltiples, presupuesto concurrente, reservas, intervalos vecinos, reinicios y zonas
escasas. El hito solo se considerará validado externamente cuando un revisor independiente
reproduzca esas pruebas y documente el riesgo residual; esa revisión se hará junto con
la del hito 15, no mediante publicaciones parciales.
