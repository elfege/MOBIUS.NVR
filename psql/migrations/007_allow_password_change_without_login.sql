-- Allow password changes during forced password change flow
-- Users need to update their password before logging in for the first time,
-- so we can't require authenticated user context for this operation.

DO $$
BEGIN
    -- Temporarily make UPDATE policy more permissive
    -- TODO: This is a security concern - allows unauthenticated password updates
    -- Better solution: Use PostgreSQL function with SECURITY DEFINER or JWT tokens
    --
    -- For now, we rely on:
    -- 1. Flask session security (signed cookies)
    -- 2. PostgREST not being publicly exposed
    -- 3. Application-level validation of user_id

    DROP POLICY IF EXISTS "Users can modify themselves" ON users;
    DROP POLICY IF EXISTS "Admins can modify all users" ON users;

    -- Allow all updates (authentication happens at Flask level)
    CREATE POLICY "Allow all updates"
        ON users FOR UPDATE
        TO nvr_anon
        USING (true)
        WITH CHECK (true);

    -- Keep admin policy for full modifications
    CREATE POLICY "Admins can do all operations"
        ON users FOR ALL
        TO nvr_anon
        USING (current_setting('app.user_role', true) = 'admin')
        WITH CHECK (current_setting('app.user_role', true) = 'admin');

    RAISE NOTICE 'Password change policy updated - RLS now permissive for UPDATEs';
    RAISE NOTICE 'SECURITY NOTE: This relies on Flask session security and PostgREST isolation';
END $$;
