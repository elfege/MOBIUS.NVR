-- =============================================================================
-- USER AUTHENTICATION MIGRATION
-- =============================================================================
-- Adds user authentication, session management, and per-user camera preferences
-- Migration: 005_add_user_authentication.sql
-- Created: February 7, 2026
--
-- This migration adds:
-- 1. users table (bcrypt authentication, role-based access)
-- 2. user_sessions table (indefinite sessions until logout)
-- 3. user_camera_preferences table (per-user stream type preferences)
-- 4. RLS policies for multi-user data isolation
-- 5. Default admin account (username: admin, password: admin, must change on first login)

DO $$
BEGIN
    -- =========================================================================
    -- USERS TABLE
    -- =========================================================================
    -- Stores user credentials and role information
    IF NOT EXISTS (SELECT FROM pg_tables WHERE tablename = 'users') THEN
        CREATE TABLE users (
            -- Primary key
            id BIGSERIAL PRIMARY KEY,

            -- Credentials
            username VARCHAR(50) UNIQUE NOT NULL,
            password_hash VARCHAR(255) NOT NULL,  -- bcrypt hash

            -- Role-based access control
            role VARCHAR(10) NOT NULL CHECK (role IN ('admin', 'user')),

            -- Force password change on first login
            must_change_password BOOLEAN DEFAULT false,

            -- Timestamps
            created_at TIMESTAMPTZ DEFAULT NOW(),
            updated_at TIMESTAMPTZ DEFAULT NOW()
        );

        -- Auto-update updated_at trigger
        CREATE TRIGGER update_users_updated_at
            BEFORE UPDATE ON users
            FOR EACH ROW
            EXECUTE FUNCTION update_updated_at_column();

        -- Default admin account (password: 'admin', must change on first login)
        INSERT INTO users (username, password_hash, role, must_change_password)
        VALUES ('admin', '$2b$12$Ton4Soqs/mkZbpyOUaQI0.Zs19b0CvFvYQzymcExvd60zKce1ULrG', 'admin', true);

        RAISE NOTICE 'Users table created with default admin account';
    END IF;

    -- =========================================================================
    -- USER SESSIONS TABLE
    -- =========================================================================
    -- Tracks active user sessions (indefinite duration until logout)
    IF NOT EXISTS (SELECT FROM pg_tables WHERE tablename = 'user_sessions') THEN
        CREATE TABLE user_sessions (
            -- Primary key (UUID for session tokens)
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

            -- User reference
            user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,

            -- Session timing
            created_at TIMESTAMPTZ DEFAULT NOW(),
            last_activity TIMESTAMPTZ DEFAULT NOW(),
            expires_at TIMESTAMPTZ DEFAULT NULL,  -- NULL = indefinite (until logout)

            -- Client metadata
            ip_address INET,
            user_agent TEXT,

            -- Session status
            is_active BOOLEAN DEFAULT true
        );

        -- Index for fast session lookups (filters on is_active for performance)
        CREATE INDEX idx_user_sessions_lookup
            ON user_sessions(id, user_id, is_active)
            WHERE is_active = true;

        -- Auto-update last_activity trigger (reuses update_updated_at_column function)
        CREATE TRIGGER update_sessions_last_activity
            BEFORE UPDATE ON user_sessions
            FOR EACH ROW
            EXECUTE FUNCTION update_updated_at_column();

        RAISE NOTICE 'User sessions table created';
    END IF;

    -- =========================================================================
    -- USER CAMERA PREFERENCES TABLE (M2M)
    -- =========================================================================
    -- Stores per-user stream type preferences for each camera
    IF NOT EXISTS (SELECT FROM pg_tables WHERE tablename = 'user_camera_preferences') THEN
        CREATE TABLE user_camera_preferences (
            -- Primary key
            id BIGSERIAL PRIMARY KEY,

            -- User reference
            user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,

            -- Camera identification (using serial number, not display name)
            camera_serial VARCHAR(50) NOT NULL,

            -- Stream type preference
            preferred_stream_type VARCHAR(20) NOT NULL
                CHECK (preferred_stream_type IN ('MJPEG', 'HLS', 'LL_HLS', 'WEBRTC', 'NEOLINK', 'NEOLINK_LL_HLS')),

            -- Timestamps
            created_at TIMESTAMPTZ DEFAULT NOW(),
            updated_at TIMESTAMPTZ DEFAULT NOW(),

            -- Unique constraint: one preference per user/camera pair
            CONSTRAINT user_camera_unique UNIQUE(user_id, camera_serial)
        );

        -- Index for fast lookups by user
        CREATE INDEX idx_user_camera_prefs_user
            ON user_camera_preferences(user_id);

        -- Auto-update updated_at trigger
        CREATE TRIGGER update_user_camera_prefs_updated_at
            BEFORE UPDATE ON user_camera_preferences
            FOR EACH ROW
            EXECUTE FUNCTION update_updated_at_column();

        RAISE NOTICE 'User camera preferences table created';
    END IF;

    -- =========================================================================
    -- ROW LEVEL SECURITY (RLS) POLICIES
    -- =========================================================================
    -- Enforce data isolation between users at the database level

    -- Enable RLS on all user-related tables
    ALTER TABLE users ENABLE ROW LEVEL SECURITY;
    ALTER TABLE user_sessions ENABLE ROW LEVEL SECURITY;
    ALTER TABLE user_camera_preferences ENABLE ROW LEVEL SECURITY;

    -- Users table policies
    -- Policy 1: Admins can see all users
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
        WHERE tablename = 'users' AND policyname = 'Admins see all users'
    ) THEN
        CREATE POLICY "Admins see all users"
        ON users FOR SELECT TO nvr_anon
        USING (current_setting('app.user_role', true) = 'admin');
    END IF;

    -- Policy 2: Users can see only themselves
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
        WHERE tablename = 'users' AND policyname = 'Users see themselves'
    ) THEN
        CREATE POLICY "Users see themselves"
        ON users FOR SELECT TO nvr_anon
        USING (id::text = current_setting('app.user_id', true));
    END IF;

    -- Policy 3: Admins can insert/update/delete all users
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
        WHERE tablename = 'users' AND policyname = 'Admins modify all users'
    ) THEN
        CREATE POLICY "Admins modify all users"
        ON users FOR ALL TO nvr_anon
        USING (current_setting('app.user_role', true) = 'admin')
        WITH CHECK (current_setting('app.user_role', true) = 'admin');
    END IF;

    -- Policy 4: Users can update only themselves (password changes)
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
        WHERE tablename = 'users' AND policyname = 'Users modify themselves'
    ) THEN
        CREATE POLICY "Users modify themselves"
        ON users FOR UPDATE TO nvr_anon
        USING (id::text = current_setting('app.user_id', true))
        WITH CHECK (id::text = current_setting('app.user_id', true));
    END IF;

    -- User sessions policies
    -- Policy: Users see and manage only their own sessions
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
        WHERE tablename = 'user_sessions' AND policyname = 'Users see own sessions'
    ) THEN
        CREATE POLICY "Users see own sessions"
        ON user_sessions FOR ALL TO nvr_anon
        USING (user_id::text = current_setting('app.user_id', true));
    END IF;

    -- User camera preferences policies
    -- Policy: Users see and manage only their own preferences
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
        WHERE tablename = 'user_camera_preferences' AND policyname = 'Users see own preferences'
    ) THEN
        CREATE POLICY "Users see own preferences"
        ON user_camera_preferences FOR ALL TO nvr_anon
        USING (user_id::text = current_setting('app.user_id', true));
    END IF;

    -- =========================================================================
    -- PERMISSIONS
    -- =========================================================================
    -- Grant access to nvr_anon role (used by PostgREST and Flask app)

    GRANT SELECT, INSERT, UPDATE, DELETE ON users TO nvr_anon;
    GRANT SELECT, INSERT, UPDATE, DELETE ON user_sessions TO nvr_anon;
    GRANT SELECT, INSERT, UPDATE, DELETE ON user_camera_preferences TO nvr_anon;

    -- Grant sequence permissions for auto-increment IDs
    GRANT USAGE, SELECT ON SEQUENCE users_id_seq TO nvr_anon;
    GRANT USAGE, SELECT ON SEQUENCE user_camera_preferences_id_seq TO nvr_anon;

    RAISE NOTICE 'User authentication migration completed successfully';
    RAISE NOTICE 'Default admin account: username=admin, password=admin (must change on first login)';

END $$;
