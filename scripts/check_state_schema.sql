BEGIN READ ONLY;

SELECT column_name, data_type, is_nullable, column_default
FROM information_schema.columns
WHERE table_schema = 'public' AND table_name = 'planner_state'
ORDER BY ordinal_position;

SELECT
    id,
    jsonb_typeof(to_jsonb(p)->'payload') AS payload_type,
    CASE WHEN jsonb_typeof(to_jsonb(p)->'payload') = 'object'
         THEN ARRAY(SELECT jsonb_object_keys(to_jsonb(p)->'payload'))
    END AS payload_keys,
    to_jsonb(p) ? 'data' AS has_legacy_data_column,
    jsonb_typeof(to_jsonb(p)->'affirmations_history') AS history_type,
    CASE WHEN jsonb_typeof(to_jsonb(p)->'affirmations_history') = 'array'
         THEN jsonb_array_length(to_jsonb(p)->'affirmations_history')
    END AS history_entries
FROM public.planner_state AS p
WHERE id = 1;

COMMIT;
