-- Migration 026: Add grid layout mode + grid style to user_preferences,
-- widen video fit constraints to include 'contain'.
--
-- grid_layout_mode: user-selectable grid tiling strategy
--   - uniform:          standard CSS grid, empty cells accepted (default)
--   - last-row-stretch: CSS grid + last-row items span extra columns
--   - auto-fit:         JS picks column count 1-6 that minimizes waste
--   - masonry:          absolute positioning, pixel-perfect fill
--
-- grid_style: spaced (gaps + rounded) vs attached (NVR style, no gaps)
--   Migrated from localStorage to DB for cross-device persistence.

-- Add new columns
ALTER TABLE user_preferences
  ADD COLUMN IF NOT EXISTS grid_layout_mode VARCHAR(20) NOT NULL DEFAULT 'uniform',
  ADD COLUMN IF NOT EXISTS grid_style VARCHAR(10) NOT NULL DEFAULT 'attached';

-- Constraints for new columns
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'user_preferences_grid_layout_mode_check'
  ) THEN
    ALTER TABLE user_preferences ADD CONSTRAINT user_preferences_grid_layout_mode_check
      CHECK (grid_layout_mode IN ('auto-fit','masonry','last-row-stretch','uniform'));
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'user_preferences_grid_style_check'
  ) THEN
    ALTER TABLE user_preferences ADD CONSTRAINT user_preferences_grid_style_check
      CHECK (grid_style IN ('spaced','attached'));
  END IF;
END $$;

-- Widen video fit to include 'contain' (letterbox)
ALTER TABLE user_preferences DROP CONSTRAINT IF EXISTS user_preferences_default_video_fit_check;
ALTER TABLE user_preferences ADD CONSTRAINT user_preferences_default_video_fit_check
  CHECK (default_video_fit IN ('cover','contain','fill'));

-- Same for per-camera override in cameras table
ALTER TABLE cameras DROP CONSTRAINT IF EXISTS cameras_video_fit_mode_check;
ALTER TABLE cameras ADD CONSTRAINT cameras_video_fit_mode_check
  CHECK (video_fit_mode IS NULL OR video_fit_mode IN ('cover','contain','fill'));
