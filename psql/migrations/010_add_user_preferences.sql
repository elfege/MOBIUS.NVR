-- User Display Preferences
-- Stores per-user camera visibility and quality preferences.
-- Replaces browser localStorage so preferences follow the user account,
-- not the browser session.

DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_tables WHERE tablename = 'user_preferences') THEN
        CREATE TABLE user_preferences (
            id BIGSERIAL PRIMARY KEY,
            user_id BIGINT NOT NULL UNIQUE REFERENCES users(id) ON DELETE CASCADE,
            hidden_cameras JSONB DEFAULT '[]'::jsonb,
            hd_cameras JSONB DEFAULT '[]'::jsonb,
            created_at TIMESTAMPTZ DEFAULT NOW(),
            updated_at TIMESTAMPTZ DEFAULT NOW()
        );

        CREATE INDEX idx_user_preferences_user
            ON user_preferences(user_id);

        CREATE TRIGGER update_user_preferences_updated_at
            BEFORE UPDATE ON user_preferences
            FOR EACH ROW
            EXECUTE FUNCTION update_updated_at_column();

        RAISE NOTICE 'user_preferences table created';
    END IF;

    -- Enable RLS
    ALTER TABLE user_preferences ENABLE ROW LEVEL SECURITY;

    -- Permissive policies (security enforced at Flask level)
    DROP POLICY IF EXISTS "Allow all access on user_preferences" ON user_preferences;
    CREATE POLICY "Allow all access on user_preferences"
        ON user_preferences FOR ALL
        TO nvr_anon
        USING (true)
        WITH CHECK (true);

    -- Grant permissions
    GRANT SELECT, INSERT, UPDATE, DELETE ON user_preferences TO nvr_anon;
    GRANT USAGE, SELECT ON SEQUENCE user_preferences_id_seq TO nvr_anon;

    RAISE NOTICE 'user_preferences migration complete';
END $$;
