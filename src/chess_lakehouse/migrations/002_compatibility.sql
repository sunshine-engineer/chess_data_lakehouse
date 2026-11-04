-- Publish legacy query interfaces only when a new historical month commits.
CREATE OR REPLACE FUNCTION control.refresh_compatibility(engine TEXT)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE target TEXT; current_schema_name TEXT; specification RECORD;
BEGIN
 IF engine NOT IN ('python','python_mp','spark') THEN RAISE EXCEPTION 'invalid engine'; END IF;
 target := 'warehouse_' || engine;
 current_schema_name := engine || '_current';
 EXECUTE format('CREATE SCHEMA IF NOT EXISTS %I',current_schema_name);
 FOR specification IN SELECT * FROM (VALUES
 ('fact_game', 'SELECT f.game_id,f.date_key,encode(w.player_hash,''hex'') AS white_player_key,encode(b.player_hash,''hex'') AS black_player_key,encode(o.opening_hash,''hex'') AS opening_key,t.control_code AS time_control_key,date_trunc(''month'',f.date_key)::date AS game_month,to_char(f.source_month,''YYYY-MM'') AS source_month,f.white_rating,f.black_rating,f.average_rating,f.rating_difference,f.white_rating_band,f.black_rating_band,f.result,f.winner_color,f.ply_count,f.move_count,f.termination,f.speed,f.ingested_at FROM @schema@.fact_game f JOIN @schema@.dim_player w ON w.player_sk=f.white_player_sk JOIN @schema@.dim_player b ON b.player_sk=f.black_player_sk JOIN @schema@.dim_opening o ON o.opening_sk=f.opening_sk JOIN @schema@.dim_time_control t ON t.time_control_sk=f.time_control_sk'),
 ('dim_date', 'SELECT date_key,year::integer,month::integer,quarter::integer,weekday::integer FROM @schema@.dim_date'),
 ('dim_player', 'SELECT encode(player_hash,''hex'') AS player_key,latest_title,last_observed_date FROM @schema@.dim_player'),
 ('dim_opening', 'SELECT encode(opening_hash,''hex'') AS opening_key,eco,family,variation FROM @schema@.dim_opening'),
 ('dim_time_control', 'SELECT control_code AS time_control_key,initial_seconds::integer,increment_seconds::integer,speed FROM @schema@.dim_time_control'),
 ('rejected_game', 'SELECT record_number::integer,to_char(source_month,''YYYY-MM'') AS source_month,game_id,''parse_or_validate''::text AS failure_stage,reason,ingested_at FROM @schema@.rejected_game')
 ) AS definitions(name,query)
 LOOP
   EXECUTE format('CREATE OR REPLACE VIEW %I.%I AS %s',current_schema_name,specification.name,
                  replace(specification.query,'@schema@',quote_ident(target)));
 END LOOP;
 EXECUTE format('GRANT USAGE ON SCHEMA %I TO chess_reader',current_schema_name);
 EXECUTE format('GRANT SELECT ON ALL TABLES IN SCHEMA %I TO chess_reader',current_schema_name);
END $$;
REVOKE ALL ON FUNCTION control.refresh_compatibility(TEXT) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.refresh_compatibility(TEXT) TO chess_loader;
