-- The cumulative patient summary is generated in the background, so record
-- its progress and any error for the UI to show.
ALTER TABLE public.users
    ADD COLUMN patient_insights_status text,
    ADD COLUMN patient_insights_error text,
    ADD COLUMN patient_insights_updated_at timestamp with time zone;
