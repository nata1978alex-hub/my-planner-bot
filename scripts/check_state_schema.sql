BEGIN READ ONLY;

SELECT column_name, data_type, is_nullable, column_default
FROM information_schema.columns
WHERE table_schema = 'public' AND table_name = 'planner_state'
ORDER BY ordinal_position;

-- to_jsonb(p) permits inspecting a column name even if that column is absent.
-- Both SQL NULL and JSON null in a present column appear as JSON null here.
SELECT
    id,
    to_jsonb(p) ? 'data' AS data_column_exists,
    jsonb_typeof(to_jsonb(p)->'data') AS data_value_type,
    CASE WHEN jsonb_typeof(to_jsonb(p)->'data') = 'object'
         THEN ARRAY(SELECT jsonb_object_keys(to_jsonb(p)->'data'))
    END AS data_keys,
    to_jsonb(p) ? 'payload' AS payload_column_exists,
    jsonb_typeof(to_jsonb(p)->'payload') AS payload_value_type,
    CASE WHEN jsonb_typeof(to_jsonb(p)->'payload') = 'object'
         THEN ARRAY(SELECT jsonb_object_keys(to_jsonb(p)->'payload'))
    END AS payload_keys,
    jsonb_typeof(to_jsonb(p)->'affirmations_history') AS history_type
FROM public.planner_state AS p
WHERE id = 1;

COMMIT;

-- Only if the first result confirms that BOTH columns exist:
-- BEGIN READ ONLY;
-- SELECT id,
--        data IS NULL AS data_sql_null,
--        jsonb_typeof(data::jsonb) AS data_json_type,
--        payload IS NULL AS payload_sql_null,
--        jsonb_typeof(payload::jsonb) AS payload_json_type
-- FROM public.planner_state WHERE id = 1;
-- COMMIT;
