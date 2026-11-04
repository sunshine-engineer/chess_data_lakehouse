-- Change python_current to spark_current for the Spark output.
SELECT source_month, speed, count(*) AS games,
       round(avg(move_count)::numeric, 2) AS average_moves
FROM python_current.fact_game
WHERE source_month = '2013-01'
GROUP BY source_month, speed;

SELECT o.family, count(*) AS games
FROM python_current.fact_game f
JOIN python_current.dim_opening o USING (opening_key)
WHERE f.source_month = '2013-01'
GROUP BY o.family ORDER BY games DESC LIMIT 10;

SELECT engine,status,report->>'elapsed_seconds' AS seconds,
       report->>'accepted_games' AS accepted_games
FROM public.pipeline_runs ORDER BY started_at;
