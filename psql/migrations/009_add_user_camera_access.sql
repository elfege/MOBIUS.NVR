-- User Camera Access Control
-- Stores which cameras each user is allowed to see.
-- If NO rows exist for a user, they can see ALL cameras (default behavior).
-- If rows exist, user can ONLY see cameras listed with allowed = true.
-- Admin users always see all cameras regardless of this table.

DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_tables WHERE tablename = 'user_camera_access') THEN
        CREATE TABLE user_camera_access (
            id BIGSERIAL PRIMARY KEY,
            user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            camera_serial VARCHAR(50) NOT NULL,
            allowed BOOLEAN DEFAULT true,
            created_at TIMESTAMPTZ DEFAULT NOW(),
            updated_at TIMESTAMPTZ DEFAULT NOW(),
            CONSTRAINT user_camera_access_unique UNIQUE(user_id, camera_serial)
        );

        CREATE INDEX idx_user_camera_access_user
            ON user_camera_access(user_id);

        CREATE TRIGGER update_user_camera_access_updated_at
            BEFORE UPDATE ON user_camera_access
            FOR EACH ROW
            EXECUTE FUNCTION update_updated_at_column();

        RAISE NOTICE 'user_camera_access table created';
    END IF;

    -- Enable RLS
    ALTER TABLE user_camera_access ENABLE ROW LEVEL SECURITY;

    -- Permissive policies (security enforced at Flask level)
    DROP POLICY IF EXISTS "Allow all access on user_camera_access" ON user_camera_access;
    CREATE POLICY "Allow all access on user_camera_access"
        ON user_camera_access FOR ALL
        TO nvr_anon
        USING (true)
        WITH CHECK (true);

    -- Grant permissions
    GRANT SELECT, INSERT, UPDATE, DELETE ON user_camera_access TO nvr_anon;
    GRANT USAGE, SELECT ON SEQUENCE user_camera_access_id_seq TO nvr_anon;

    RAISE NOTICE 'user_camera_access migration complete';
END $$;
