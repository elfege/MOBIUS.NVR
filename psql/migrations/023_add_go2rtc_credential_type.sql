-- Migration 023: Add 'go2rtc' to credential_type CHECK constraint
--
-- Allows per-camera go2rtc credentials to be stored in camera_credentials
-- as (serial, 'go2rtc') — separate from the generic camera RTSP credential
-- and from global service-level credentials.
--
-- UI sets these via PUT /api/camera/<serial>/credentials with scope='go2rtc'.
-- generate_go2rtc_config.py resolves them per-camera at startup via
-- ${go2rtc_username} / ${go2rtc_password} placeholders in go2rtc_source.

ALTER TABLE camera_credentials
    DROP CONSTRAINT IF EXISTS camera_credentials_credential_type_check;

ALTER TABLE camera_credentials
    ADD CONSTRAINT camera_credentials_credential_type_check
    CHECK (credential_type IN ('camera', 'service', 'go2rtc'));
