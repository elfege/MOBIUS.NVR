-- Add 'viewer' role to the users table
-- Viewer: read-only access to streams, no settings, no recording, no camera management

-- Drop and recreate the CHECK constraint to include 'viewer'
ALTER TABLE users DROP CONSTRAINT IF EXISTS users_role_check;
ALTER TABLE users ADD CONSTRAINT users_role_check CHECK (role IN ('admin', 'user', 'viewer'));
