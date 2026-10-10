-- A row lock serializes writers; compare against the version observed after
-- acquiring it. clock_timestamp() uses wall time rather than transaction time.
CREATE OR REPLACE FUNCTION public.set_song_updated_at() RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = GREATEST(clock_timestamp(), OLD.updated_at + INTERVAL '1 microsecond');
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER songs_set_updated_at ON public.songs;
CREATE TRIGGER songs_set_updated_at
    BEFORE UPDATE ON public.songs
    FOR EACH ROW EXECUTE FUNCTION public.set_song_updated_at();
