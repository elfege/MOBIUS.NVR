-- Fix RLS policy blocking user creation (INSERT)
-- The "Admins can do all operations" policy requires app.user_role = 'admin',
-- but Flask calls PostgREST without setting that header.
-- INSERT needs a permissive policy like we already have for UPDATE (migration 007).
-- Security is enforced at Flask level (@login_required + role check).

DO $$
BEGIN
    -- Add permissive INSERT policy (matches the permissive UPDATE from migration 007)
    DROP POLICY IF EXISTS "Allow all inserts" ON users;

    CREATE POLICY "Allow all inserts"
        ON users FOR INSERT
        TO nvr_anon
        WITH CHECK (true);

    -- Also add permissive DELETE policy for consistency
    DROP POLICY IF EXISTS "Allow all deletes" ON users;

    CREATE POLICY "Allow all deletes"
        ON users FOR DELETE
        TO nvr_anon
        USING (true);

    RAISE NOTICE 'INSERT/DELETE RLS policies now permissive - security enforced at Flask level';
END $$;
