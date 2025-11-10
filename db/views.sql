-- This view now correctly points to the output of the feature gold batch job,
-- making its features available for downstream tasks like labeling.
CREATE OR REPLACE VIEW v_features_asof AS
SELECT *
FROM features_gold_serving;
