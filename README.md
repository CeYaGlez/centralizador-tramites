# 📋 Centralizador de trámites

Prototipo que toma **varios Excel desordenados** de trámites (qué trámite, qué día, quién lo atendió, en qué
estatus), los **limpia y centraliza en una sola base de datos**, y permite **consultarlos fácilmente** desde una
interfaz web sin saber SQL.

> ⚠️ Todos los datos son **sintéticos**. Los exceles se generan con `src/generar_exceles.py` imitando los
> problemas típicos de la vida real. No se usó información de ninguna dependencia.

## El problema

Cada oficina lleva su propio Excel, con su propio formato:

| Problema real | Ejemplo en los datos de prueba |
|---|---|
| Encabezados distintos y en filas distintas | `Folio` / `No. Folio` / `FOLIO`; encabezado en la fila 4 bajo un título |
| Fechas en 5 formatos | fecha real, `04/03/2026`, `12-abr-2026`, serial de Excel, o partida en Día/Mes/Año |
| El mismo trámite escrito de muchas formas | `Acta de nacimiento`, `ACTA NACIMIENTO.`, `Acta de nacimeinto` |
| Nombres de encargados inconsistentes | `Ana López Ruiz`, `ANA LOPEZ`, `A. López` |
| Folios duplicados entre archivos | "reimpresiones" y filas repetidas |
| Basura | filas vacías, fila de TOTAL, fecha del año 2062, celdas en blanco |

## La solución

```
 exceles sucios ──► ETL (Python) ──► SQLite (esquema 3FN) ──► vistas SQL ──► app Streamlit
 (data/raw)        limpia, valida,    catálogos + registro      consultas      filtros, gráficas,
                   audita             + auditoría               reutilizables  estadística, descarga
```

1. **`src/generar_exceles.py`**: crea 4 archivos sucios con datos falsos (1,591 trámites "verdaderos" de base).
2. **`src/etl.py`**: detecta el encabezado, normaliza columnas, fechas, folios, trámites, encargados y estatus,
   deduplica y carga. **Nada se descarta en silencio**: cada fila rechazada queda en la tabla `rechazado` con su motivo.
3. **`sql/`**: esquema normalizado con llaves, `CHECK` e índices; vistas (`v_registro`, `v_tramites_por_dia`, …) y
   5 consultas de ejemplo (JOIN, GROUP BY, HAVING, subqueries).
4. **`src/estadistica.py`**: estadística descriptiva y detección de días atípicos con IQR.
5. **`app.py`**: interfaz con filtros, KPIs, gráficas, descarga a CSV/Excel y pestaña de calidad de datos.
6. **`tests/`**: 40 pruebas con pytest (limpiadores + integración de punta a punta).

## Cómo correrlo

```bash
pip install -r requirements.txt
python -m src.generar_exceles     # crea data/raw/*.xlsx sucios (solo para la demo)
python -m src.etl                 # limpia y crea data/tramites.db
python -m src.estadistica         # resumen y días atípicos en terminal
streamlit run app.py              # interfaz web
pytest                            # pruebas
```

Con datos reales: copiar los `.xlsx` a `data/raw/` y correr `python -m src.etl`. La carga es **idempotente**
(reconstruye la base completa en cada corrida).

## Resultado con los datos de prueba

- 1,633 filas leídas → **1,591 cargadas** (exactamente los trámites verdaderos) y 42 rechazadas con motivo
  (40 duplicados, 1 fecha vacía, 1 fecha en 2062).
- El método IQR detecta un **pico de 68 trámites el 27-feb-2026** (promedio diario: 12.5). El sistema explica que
  60 son pago de predial en la oficina Centro, consistente con el cierre del descuento por pronto pago que se simuló.

## Decisiones de diseño (y por qué)

- **Duplicados:** se conserva la versión más completa; si difieren en fecha o trámite se avisa para revisión humana.
- **Fechas ambiguas:** se interpretan día/mes/año (México). Es una suposición; hay que confirmarla con cada oficina.
- **Encargado:** se reconoce por inicial + primer apellido. Si no coincide, queda `Sin asignar` pero el trámite **sí se cuenta**.
- **Privacidad:** el nombre del ciudadano nunca llega a la base, solo un hash con sal. Permite contar personas distintas sin exponerlas.
- **IQR en lugar de desviación estándar:** el volumen diario es muy sesgado; un solo pico distorsionaría la desviación.
- **Consultas parametrizadas:** los filtros de la app nunca concatenan texto del usuario en el SQL.

## Limitaciones (lo que NO hace todavía)

- Es un prototipo con datos sintéticos; las reglas de negocio (catálogo de trámites, encargados, estatus) son supuestas y
  deben validarse con quien usa los exceles.
- SQLite es de un solo escritor y sin usuarios: para uso multiusuario habría que migrar a PostgreSQL y agregar autenticación y roles.
- La sal de seudonimización viene por defecto para la demo; en producción debe ir en una variable de entorno (`TRAMITES_SAL`) y protegerse.
- No hay despliegue en servidor/nube ni respaldos automáticos.
- La carga es completa, no incremental.

## Siguientes pasos

1. Validar el catálogo de trámites y encargados con el equipo real.
2. Cargas incrementales y bitácora de ejecuciones.
3. Migrar a PostgreSQL, autenticación por roles y despliegue en un servidor Linux.
4. Conectar un dashboard de BI (Power BI/Metabase) a las mismas vistas.
