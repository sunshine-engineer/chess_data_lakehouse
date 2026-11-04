-- Use warehouse_python_mp or warehouse_spark to inspect the other engines.
SELECT source_month, speed, count(*) AS games,
       round(avg(move_count)::numeric,2) AS mean_moves
FROM warehouse_python.fact_game
WHERE source_month = DATE '2013-01-01'
GROUP BY source_month,speed ORDER BY speed;

SELECT o.family,count(*) AS games
FROM warehouse_python.fact_game f
JOIN warehouse_python.dim_opening o USING(opening_sk)
WHERE f.source_month = DATE '2013-01-01'
GROUP BY o.family ORDER BY games DESC LIMIT 10;

-- Actual date can differ from archive month for games crossing a month boundary.
SELECT min(date_key),max(date_key),count(*)
FROM warehouse_python.fact_game WHERE source_month=DATE '2013-01-01';

EXPLAIN(ANALYZE,BUFFERS)
SELECT speed,count(*) FROM warehouse_python.fact_game
WHERE source_month=DATE '2013-01-01' GROUP BY speed;
