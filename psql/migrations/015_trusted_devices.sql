-- =============================================================================
-- TRUSTED DEVICES MIGRATION
-- =============================================================================
-- Adds a trusted_devices table for device identification and admin management.
--
-- Each browser client gets a unique device_token (UUID) stored in localStorage
-- and an httpOnly cookie. The server updates last_seen/ip on each heartbeat.
-- Admin users can mark devices as trusted and view all connected clients.
--
-- Migration: 015_trusted_devices.sql
-- Created: March 09, 2026
--
-- Run with:
--   docker exec -i nvr-db psql -U nvr_api -d nvr < psql/migrations/015_trusted_devices.sql

DO $$
BEGIN
    -- Create the trusted_devices table if it doesn't exist
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.tables
        WHERE table_name = 'trusted_devices'
    ) THEN
        CREATE TABLE trusted_devices (
            id SERIAL PRIMARY KEY,
            device_token UUID NOT NULL UNIQUE DEFAULT gen_random_uuid(),
            user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
            device_name TEXT DEFAULT '',
            ip_address TEXT NOT NULL DEFAULT '',
            user_agent TEXT NOT NULL DEFAULT '',
            is_trusted BOOLEAN NOT NULL DEFAULT FALSE,
            first_seen TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
            last_seen TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
        );

        -- Index for quick lookup by token (used on every heartbeat)
        CREATE INDEX idx_trusted_devices_token ON trusted_devices(device_token);

        -- Index for admin queries filtering by last_seen
        CREATE INDEX idx_trusted_devices_last_seen ON trusted_devices(last_seen);

        -- RLS policy (permissive — security enforced at Flask level)
        ALTER TABLE trusted_devices ENABLE ROW LEVEL SECURITY;
        CREATE POLICY trusted_devices_all ON trusted_devices
            FOR ALL TO nvr_anon USING (true) WITH CHECK (true);

        RAISE NOTICE 'Created trusted_devices table with indexes and RLS';
    END IF;

    RAISE NOTICE 'Trusted devices migration (015) completed successfully';
END $$;
