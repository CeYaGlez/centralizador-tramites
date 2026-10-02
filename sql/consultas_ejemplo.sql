-- 1) ¿Cuántas personas hicieron cada trámite?
SELECT tramite, tramites, personas_distintas
FROM v_personas_por_tramite
ORDER BY tramites DESC;

-- 2) ¿Qué día hubo más trámites y quién los atendió? (subquery)
SELECT fecha, encargado, COUNT(*) AS atendidos
FROM v_registro
WHERE fecha = (SELECT fecha FROM v_tramites_por_dia ORDER BY total DESC LIMIT 1)
GROUP BY fecha, encargado
ORDER BY atendidos DESC;

-- 3) Encargados con más de 100 trámites y su % de concluidos (HAVING)
SELECT encargado, COUNT(*) AS total,
       ROUND(100.0 * SUM(estatus = 'Concluido') / COUNT(*), 1) AS pct_concluido
FROM v_registro
GROUP BY encargado
HAVING COUNT(*) > 100
ORDER BY total DESC;

-- 4) Trámites por oficina y mes
SELECT oficina, mes, COUNT(*) AS total
FROM v_registro
GROUP BY oficina, mes
ORDER BY oficina, mes;

-- 5) Días con volumen por encima del promedio (subquery escalar)
SELECT fecha, total
FROM v_tramites_por_dia
WHERE total > (SELECT AVG(total) FROM v_tramites_por_dia)
ORDER BY total DESC
LIMIT 10;
