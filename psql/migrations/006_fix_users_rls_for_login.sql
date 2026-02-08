-- Fix RLS policies to allow login queries
-- The previous policies created a chicken-and-egg problem: we need to query users to login,
-- but RLS requires user_id/user_role which we don't have until after login.

DO $$
BEGIN
    -- Drop existing restrictive policies
    DROP POLICY IF EXISTS "Admins see all users" ON users;
    DROP POLICY IF EXISTS "Users see themselves" ON users;
    DROP POLICY IF EXISTS "Admins modify all users" ON users;
    DROP POLICY IF EXISTS "Users modify themselves" ON users;

    -- Create new permissive policy for authentication
    -- Allow reading username and password_hash for login (without requiring user context)
    CREATE POLICY "Allow authentication queries"
        ON users FOR SELECT
        TO nvr_anon
        USING (true);  -- Allow all reads for authentication

    -- Restrict modifications to authenticated users only
    CREATE POLICY "Admins can modify all users"
        ON users FOR ALL
        TO nvr_anon
        USING (current_setting('app.user_role', true) = 'admin')
        WITH CHECK (current_setting('app.user_role', true) = 'admin');

    CREATE POLICY "Users can modify themselves"
        ON users FOR UPDATE
        TO nvr_anon
        USING (id::text = current_setting('app.user_id', true))
        WITH CHECK (id::text = current_setting('app.user_id', true));

    RAISE NOTICE 'RLS policies updated to allow authentication queries';
END $$;
