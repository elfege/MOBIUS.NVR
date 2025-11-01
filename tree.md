# /home/elfege/0_NVR/

## 0_MAINTENANCE_SCRIPTS/
- diagnose_ffmpeg.sh*
- make_self_signed_tls.sh*
- monitor.sh*
- NEOlink_integration.sh*
- test_office_cam.sh*
- test.sh*

## Root Files
- app.py
- bootstrap.json
- debug_cookies.txt
- deploy.sh*
- eufy_bridge.py
- eufy_bridge.sh*
- eufy_bridge_watchdog.py
- get_token.sh*
- package.json
- package-lock.json
- persistent.json
- PTZ_Discovery.py
- ptz_pan_right.py
- ptz_server.py
- README.md
- requirements.txt
- start_nvr_standalone.sh*
- start.sh*
- stop_nvr_standalone.sh*
- stop.sh*
- tree.txt
- update_mediamtx_paths.sh*

## config/
- cameras_good_config_Oct_13_2025.json
- cameras.json
- cameras_json_notes.json
- eufy_bridge.json
- ffmpeg_names_map.py
- reolink.json
- unifi_protect.json

### config/backups/
- cameras_good_config_Oct_13_2025.json

#### config/backups/backup/
- cameras_good_config_Oct_13_2025.json

## DOCS/
- aspect_ratios.md
- ffmpeg_settings.md
- OCT_2025_Architecture_Refactoring_Migration.md
- README_Docker_Deployment_Guide.md
- README_project_history.md
- README_UniFi_Protect_Implementation_Checklist.md

### DOCS/API/
- analysis_01.md

### DOCS/DOCS/API/
- analysis_01.md

## low_level_handlers/
- cleanup_handler.py
- process_reaper.py

## services/
- amcrest_mjpeg_capture_service.py
- app_restart_handler.py
- camera_base.py
- camera_repository.py
- eufy_service.py
- mjpeg_capture_service.py
- reolink_mjpeg_capture_service.py
- unifi_mjpeg_capture_service.py
- unifi_protect_service.py
- unifi_service_resource_monitor.py

### services/credentials/
- amcrest_credential_provider.py
- credential_provider.py
- eufy_credential_provider.py
- reolink_credential_provider.py
- unifi_credential_provider.py

### services/ptz/
- amcrest_ptz_handler.py
- ptz_validator.py

## static/

### static/css/
- main.css

#### static/css/base/
- reset.css

#### static/css/components/
- buttons.css
- fullscreen.css
- fullscreen-mjpeg.css
- fullscreen-ptz.css
- grid-container.css
- grid-modes.css
- header.css
- ptz-controls.css
- reset.css
- responsive.css
- settings-overlay.css
- stream-controls.css
- stream-item.css
- stream-overlay.css

### static/js/

#### static/js/controllers/
- ptz-controller.js

#### static/js/settings/
- fullscreen-handler.js
- settings-manager.js
- settings-ui.js

#### static/js/streaming/
- flv-stream.js
- health.js
- hls-stream.js
- hls-stream-legacy.js
- mjpeg-stream.js
- mjpeg-stream-legacy.js
- stream.js
- stream-legacy.js
- stream_refresh.js

#### static/js/utils/
- loading-manager.js
- logger.js
- old_ios_checker.js

## streaming/
- ffmpeg_params.py
- stream_handler.py
- stream_manager.py

### streaming/handlers/
- amcrest_stream_handler.py
- eufy_stream_handler.py
- reolink_stream_handler.py
- unifi_stream_handler.py

## templates/
- error.html

## TESTS/
- test_ffmpeg_parameters.py
- test_ffmpeg.py
- test_HLS_MULTIPLE_CONFIG.sh*
- test_hls_segmentation.py
- test_hls.sh*
- test_LL-HLS_in_console.js
- test_ll_hls.sh*
- test_LL_HLS.sh*
- test_SIMPLE_HLS.sh*

---

**Summary:** 26 directories, 113 files

**Key Directories for ONVIF Implementation:**
- services/ptz/ - Current PTZ handlers
- services/credentials/ - Authentication providers
- static/js/controllers/ - Frontend PTZ controls