-- Migration 033: link trusted_devices to host_settings.
--
-- Why:
--   trusted_devices is per-browser-instance (one row per device_token cookie
--   in localStorage). host_settings is per-physical-machine (one row per
--   Linux hostname / HOST_LABEL the host-agent reports under).
--   Cardinality: one machine -> many browsers (Chrome, Firefox, profiles,
--   different users on the same kiosk). The natural FK lives on
--   trusted_devices and points UP at host_settings.
--
--   Once populated, the kiosk page can auto-resolve "what machine am I on?"
--   the moment it loads — read the device_token cookie, look up the
--   trusted_devices row, read host_label from there. No more manual
--   binding step in the Performance settings tab.
--
-- Population path:
--   The host-agent POSTs /api/host/state from the kiosk's IP. The route
--   handler updates trusted_devices.host_label = body.host for any rows
--   where ip_address matches the agent's source IP and host_label IS NULL.
--   After one ping the binding is automatic for every browser session
--   on that machine. Manual override remains available from the UI.
--
-- ON DELETE SET NULL: removing a host_settings row should NOT cascade-
-- delete trusted_devices (those rows still represent real browsers we
-- want to keep tracking). The link just goes back to NULL.

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'trusted_devices' AND column_name = 'host_label'
    ) THEN
        ALTER TABLE trusted_devices
            ADD COLUMN host_label TEXT
                REFERENCES host_settings(host_label) ON DELETE SET NULL;

        CREATE INDEX IF NOT EXISTS idx_trusted_devices_host_label
            ON trusted_devices(host_label)
            WHERE host_label IS NOT NULL;

        COMMENT ON COLUMN trusted_devices.host_label IS
            'Optional FK to host_settings.host_label. Populated when the '
            'host-agent first pings from the same ip_address as this row. '
            'Used by the kiosk page to auto-resolve which machine it is on.';

        RAISE NOTICE 'Added trusted_devices.host_label FK to host_settings';
    ELSE
        RAISE NOTICE 'trusted_devices.host_label already exists, skipping';
    END IF;
END $$;
