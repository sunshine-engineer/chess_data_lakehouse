-- Run against the warehouse.sqlite selected by CURRENT.json.
SELECT source_month, speed, count(*) AS games,
       round(avg(move_count), 2) AS average_moves
FROM fact_game
WHERE source_month = '2013-01'
GROUP BY source_month, speed;

SELECT o.family, count(*) AS games,
       round(avg(CASE WHEN f.winner_color = 'white' THEN 1.0 ELSE 0 END), 3) AS white_win_rate
FROM fact_game AS f JOIN dim_opening AS o USING (opening_key)
WHERE f.source_month = '2013-01'
GROUP BY o.family ORDER BY games DESC LIMIT 10;
