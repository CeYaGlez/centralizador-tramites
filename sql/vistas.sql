-- Vistas pensadas para que quien no sabe SQL consulte desde la app o desde Excel/Power BI.

CREATE VIEW v_registro AS
SELECT r.folio,
       r.fecha,
       strftime('%Y-%m', r.fecha) AS mes,
       t.nombre  AS tramite,
       e.nombre  AS encargado,
       o.nombre  AS oficina,
       r.ciudadano_hash,
       r.estatus
FROM registro r
JOIN tramite_tipo t ON t.id = r.tramite_id
JOIN encargado    e ON e.id = r.encargado_id
JOIN oficina      o ON o.id = r.oficina_id;

CREATE VIEW v_tramites_por_dia AS
SELECT fecha, COUNT(*) AS total
FROM registro
GROUP BY fecha;

CREATE VIEW v_tramites_por_tipo_mes AS
SELECT mes, tramite, COUNT(*) AS total
FROM v_registro
GROUP BY mes, tramite;

CREATE VIEW v_carga_por_encargado AS
SELECT encargado,
       oficina,
       COUNT(*) AS total,
       SUM(CASE WHEN estatus = 'Concluido' THEN 1 ELSE 0 END) AS concluidos,
       ROUND(100.0 * SUM(CASE WHEN estatus = 'Concluido' THEN 1 ELSE 0 END) / COUNT(*), 1) AS pct_concluido
FROM v_registro
GROUP BY encargado, oficina;

CREATE VIEW v_personas_por_tramite AS
SELECT tramite,
       COUNT(*)                       AS tramites,
       COUNT(DISTINCT ciudadano_hash) AS personas_distintas
FROM v_registro
GROUP BY tramite;
