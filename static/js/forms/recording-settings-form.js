/**
 * Recording Settings Form Handler
 * Location: ~/0_MOBIUS.NVR/static/js/forms/recording-settings-form.js
 * Handles form generation, validation, and submission.
 * Organized into tabbed UI: General, Recording, Snapshots, Power, Credentials, Advanced.
 */

export class RecordingSettingsForm {
    constructor(controller) {
        this.controller = controller;
        this.currentCameraId = null;
        this.currentSettings = null;
        this.onSaveCallback = null;
        /** Full camera config object from /api/cameras/<id> for the Advanced tab */
        this.fullCameraConfig = null;
    }

    // =========================================================================
    //  HELP DEFINITIONS — (?) tooltip content per setting key
    // =========================================================================

    /**
     * Returns a plain-English explanation + dependency notes for a given
     * cameras.json key.  Used by the Advanced tab (?) icons.
     * @param {string} key - The setting key (e.g. "stream_type")
     * @returns {{title: string, description: string, dependencies: string|null}}
     */
    static helpFor(key) {
        const HELP = {
            // ── Top-level scalars ──
            serial:       { title: 'Serial Number', description: 'Unique hardware identifier burned into the camera. This is the primary key used everywhere in the system.', dependencies: 'Immutable — cannot be changed.' },
            name:         { title: 'Display Name', description: 'The friendly label shown in the UI grid, recordings list and alerts. Change it to whatever makes sense for your setup (e.g. "Front Porch", "Garage").', dependencies: null },
            type:         { title: 'Camera Brand / Type', description: 'Tells the NVR which protocol drivers to use (ONVIF, Baichuan, P2P, etc.). Must match the actual hardware vendor.', dependencies: 'Changing this may break streaming if the camera does not support the new brand\'s protocol.' },
            host:         { title: 'IP Address', description: 'The local network IP where the camera can be reached for RTSP, ONVIF, and HTTP control.', dependencies: 'Must be reachable from the NVR container network.' },
            mac:          { title: 'MAC Address', description: 'Hardware ethernet address. Used for Wake-on-LAN and network identification. Usually auto-discovered.', dependencies: null },
            packager_path:{ title: 'Packager Path', description: 'The MediaMTX path segment used when publishing this camera\'s LL-HLS stream. Usually matches the serial number.', dependencies: 'Must match the path configured in MediaMTX / the packager container.' },
            stream_type:  { title: 'Stream Type', description: 'Primary streaming protocol used in the UI player. Options: WEBRTC (lowest latency), LL_HLS (good balance), MJPEG (universal fallback), NEOLINK, GO2RTC.', dependencies: 'Determines which player is loaded and which recording source is used.' },
            rtsp_alias:   { title: 'RTSP Token Alias', description: 'If set, this alias is looked up in the credentials database instead of using the camera serial for the RTSP URL token. Useful when multiple cameras share the same credential entry.', dependencies: null },
            max_connections: { title: 'Max RTSP Connections', description: 'How many simultaneous RTSP connections the camera hardware supports. Budget cameras (Eufy, SV3C) typically support only 1. Set to 1 to ensure all consumers tap MediaMTX instead of connecting directly.', dependencies: 'If set to 1, MediaMTX becomes the single RTSP consumer and all other services tap it.' },
            onvif_port:   { title: 'ONVIF Port', description: 'TCP port for ONVIF service discovery and event subscriptions (motion detection). Common values: 80, 8000, 8080, 2020.', dependencies: 'Required if using ONVIF motion detection. Camera must have ONVIF enabled in its own settings.' },
            power_supply: { title: 'Power Supply Type', description: 'How the camera is powered. "hubitat" = smart plug controllable via Hubitat Elevation. "poe" = Power over Ethernet (managed switch). "none" = wall adapter or unknown.', dependencies: 'Auto power-cycle feature requires "hubitat" with a valid device ID.' },
            hidden:       { title: 'Hidden', description: 'When true, the camera tile is not shown in the main grid view. The camera still records and can be accessed via the API.', dependencies: null },
            ui_health_monitor: { title: 'UI Health Monitor', description: 'When true, the frontend periodically pings this camera\'s stream and shows ONLINE/OFFLINE badges. Disable for cameras that are intentionally offline or slow to respond.', dependencies: null },
            reversed_pan: { title: 'Reversed Pan', description: 'Inverts the horizontal (left/right) PTZ direction. Useful when the camera is mounted upside-down or mirrored.', dependencies: 'Only applies to cameras with PTZ capability.' },
            reversed_tilt:{ title: 'Reversed Tilt', description: 'Inverts the vertical (up/down) PTZ direction.', dependencies: 'Only applies to cameras with PTZ capability.' },
            notes:        { title: 'Notes', description: 'Free-text field for your own notes about this camera (location details, quirks, maintenance history). Not used by the system.', dependencies: null },
            power_supply_device_id: { title: 'Power Supply Device ID', description: 'The Hubitat device ID of the smart plug controlling this camera\'s power. Required for auto power-cycle.', dependencies: 'power_supply must be "hubitat" for this to have any effect.' },
            true_mjpeg:   { title: 'True MJPEG', description: 'When true, the camera natively outputs an MJPEG stream (not transcoded). This skips FFmpeg MJPEG conversion.', dependencies: 'Only relevant when stream_type is MJPEG.' },
            capabilities: { title: 'Capabilities', description: 'List of features this camera supports: "streaming", "ptz", "two_way_audio", "ONVIF". Used to enable/disable UI controls.', dependencies: 'If you remove "ptz", PTZ controls disappear from the tile.' },
            model:        { title: 'Model', description: 'Camera hardware model identifier (e.g. "T8416", "RLC-823A"). Informational.', dependencies: null },
            station:      { title: 'Station', description: 'For Eufy cameras, the base station serial that manages this camera. For other brands, usually matches the serial.', dependencies: 'Eufy P2P streaming requires a valid station.' },
            image_mirrored: { title: 'Image Mirrored', description: 'Indicates the camera\'s image is horizontally flipped. The UI can compensate with CSS transform if needed.', dependencies: null },

            // ── Nested objects (shown as collapsible JSON) ──
            ll_hls:       { title: 'LL-HLS Configuration', description: 'Low-Latency HLS packaging settings: video encoding (main & sub), audio transcoding, and MediaMTX publisher endpoint. Controls FFmpeg command-line arguments.', dependencies: 'stream_type should include LL_HLS or WEBRTC for this to be active.' },
            mjpeg_snap:   { title: 'MJPEG Snapshot Config', description: 'Settings for the MJPEG snapshot capture service: FPS, resolution, and timeout for both main and sub streams.', dependencies: 'Used when stream_type is MJPEG or for snapshot recording.' },
            neolink:      { title: 'Neolink Config', description: 'Configuration for cameras accessed via Neolink proxy (Reolink cameras without native RTSP).', dependencies: 'Only used when stream_type is NEOLINK or NEOLINK_LL_HLS.' },
            player_settings: { title: 'Player Settings', description: 'HLS.js player tuning parameters: latency targets, buffer sizes, worker settings. Affects playback smoothness vs. latency.', dependencies: 'Only applies to HLS/LL-HLS stream types.' },
            rtsp_input:   { title: 'RTSP Input Settings', description: 'FFmpeg input options for consuming the camera\'s RTSP feed: transport protocol, probe size, timeouts.', dependencies: 'Applied when FFmpeg reads from this camera.' },
            rtsp_output:  { title: 'RTSP Output Settings', description: 'FFmpeg output/encoding options for the HLS packager: codec, bitrate, GOP, segment settings.', dependencies: 'Controls the LL-HLS packaging pipeline.' },
            two_way_audio:{ title: 'Two-Way Audio', description: 'Audio talkback configuration: protocol (ONVIF/Baichuan/Eufy P2P), codec, sample rate, speaker volume, backchannel URL.', dependencies: 'Camera must have "two_way_audio" in capabilities.' },
            power_cycle_on_failure: { title: 'Power Cycle on Failure', description: 'Auto power-cycle settings: enable/disable and cooldown period between cycles.', dependencies: 'Requires power_supply = "hubitat" and a valid power_supply_device_id.' },
            rtsp:         { title: 'RTSP Endpoint', description: 'Direct RTSP connection details: host, port, path. Used for direct RTSP recording and as fallback.', dependencies: null },
        };
        return HELP[key] || { title: key, description: `Camera configuration field: "${key}". No detailed description available yet.`, dependencies: null };
    }

    // =========================================================================
    //  FORM GENERATION — tabbed layout
    // =========================================================================

    /**
     * Generate form HTML with tabbed navigation.
     * @param {string} cameraId - Camera serial
     * @param {Object} settings - Recording settings
     * @param {Array} cameraCapabilities - Camera capabilities
     * @param {string} cameraType - Camera brand
     * @param {string} streamType - Primary stream type
     * @param {string} cameraName - Display name
     * @param {string|null} videoFitMode - Per-camera video fit override
     * @returns {string} - Complete form HTML
     */
    generateForm(cameraId, settings, cameraCapabilities = [], cameraType = null, streamType = null, cameraName = '', videoFitMode = null) {
        this.currentCameraId = cameraId;
        this.currentSettings = settings;
        this.currentCameraName = cameraName;

        const hasOnvif = cameraCapabilities.includes('ONVIF');
        const isReolink = cameraType && cameraType.toLowerCase() === 'reolink';
        const resolvedRecordingSource = this._resolveRecordingSource(streamType);
        this.resolvedRecordingSource = resolvedRecordingSource;

        const escapedName = (cameraName || '').replace(/"/g, '&quot;').replace(/</g, '&lt;').replace(/>/g, '&gt;');

        return `
            <form id="recording-settings-form" class="recording-settings-form">

                <!-- Tab Navigation -->
                <div class="settings-tabs">
                    <button type="button" class="settings-tab-btn active" data-tab="general">
                        <i class="fas fa-info-circle"></i> General
                    </button>
                    <button type="button" class="settings-tab-btn" data-tab="recording">
                        <i class="fas fa-video"></i> Recording
                    </button>
                    <button type="button" class="settings-tab-btn" data-tab="snapshots">
                        <i class="fas fa-camera"></i> Snapshots
                    </button>
                    <button type="button" class="settings-tab-btn" data-tab="power">
                        <i class="fas fa-power-off"></i> Power
                    </button>
                    <button type="button" class="settings-tab-btn" data-tab="credentials">
                        <i class="fas fa-key"></i> Credentials
                    </button>
                    <button type="button" class="settings-tab-btn" data-tab="advanced">
                        <i class="fas fa-cogs"></i> Advanced
                    </button>
                </div>

                <!-- ============ TAB: General ============ -->
                <div class="settings-tab-panel active" data-tab-panel="general">

                    <!-- Camera Info -->
                    <div class="recording-form-section">
                        <h4><i class="fas fa-tag"></i> Camera Info</h4>
                        <div class="recording-form-group">
                            <label for="camera-name">Display Name</label>
                            <div style="display: flex; gap: 8px; align-items: center;">
                                <input type="text"
                                       id="camera-name"
                                       name="camera_name"
                                       value="${escapedName}"
                                       maxlength="255"
                                       placeholder="Enter camera name"
                                       style="flex: 1;">
                                <button type="button" id="rename-camera-btn"
                                        class="recording-btn recording-btn-secondary"
                                        style="white-space: nowrap; padding: 8px 14px;">
                                    <i class="fas fa-pencil-alt"></i> Rename
                                </button>
                            </div>
                            <span class="form-description">Name shown in UI, saved to database and config</span>
                            <div id="rename-status" style="display: none; margin-top: 6px;"></div>
                        </div>
                        <div class="recording-form-group">
                            <label>Serial Number</label>
                            <input type="text" value="${cameraId}" disabled
                                   style="opacity: 0.6; cursor: not-allowed;">
                        </div>
                    </div>

                    <!-- Display Settings -->
                    <div class="recording-form-section">
                        <h4><i class="fas fa-tv"></i> Display Settings</h4>
                        <div class="recording-form-group">
                            <label>Video Fit Mode (this camera)</label>
                            <div style="display: flex; align-items: center; gap: 12px; margin-top: 6px;">
                                <label class="setting-toggle" style="margin: 0;">
                                    <input type="checkbox" id="video-fit-camera-toggle"
                                           ${videoFitMode === 'fill' ? 'checked' : ''}>
                                    <span class="toggle-slider"></span>
                                </label>
                                <span id="video-fit-camera-label" style="color: #ccc; font-size: 13px;">
                                    ${videoFitMode === 'fill' ? 'Fill (stretch, no crop)' : 'Cover (crop edges, no deform)'}
                                </span>
                            </div>
                            <span class="form-description" style="margin-top: 8px; display: block;">
                                <strong>Off (Cover):</strong> Fills tile, crops edges — no image deformation.<br>
                                <strong>On (Fill):</strong> Stretches to fit — no crop, may deform if camera aspect differs.<br>
                                Set to blank to use your account default (see Settings panel).
                            </span>
                            <button type="button" id="clear-video-fit-btn"
                                    class="recording-btn recording-btn-secondary"
                                    style="margin-top: 8px; font-size: 12px; padding: 5px 10px;">
                                <i class="fas fa-times-circle"></i> Clear override (use default)
                            </button>
                            <div id="video-fit-status" style="display: none; margin-top: 6px;"></div>
                        </div>
                    </div>
                </div>

                <!-- ============ TAB: Recording ============ -->
                <div class="settings-tab-panel" data-tab-panel="recording">

                    <!-- Motion Recording -->
                    <div class="recording-form-section">
                        <h4><i class="fas fa-running"></i> Motion Recording</h4>

                        <div class="recording-form-group">
                            <div class="recording-checkbox-wrapper">
                                <input type="checkbox"
                                       id="motion-enabled"
                                       name="motion_enabled"
                                       ${settings.motion_recording?.enabled ? 'checked' : ''}>
                                <label for="motion-enabled">Enable Motion Recording</label>
                            </div>
                            <span class="form-description">Record automatically when motion is detected</span>
                        </div>

                        <div class="recording-form-row">
                            <div class="recording-form-group">
                                <label for="detection-method">Detection Method</label>
                                <select id="detection-method"
                                        name="detection_method"
                                        ${!hasOnvif && settings.motion_recording?.detection_method === 'onvif' ? 'disabled' : ''}>
                                    <option value="onvif" ${settings.motion_recording?.detection_method === 'onvif' ? 'selected' : ''} ${!hasOnvif ? 'disabled' : ''}>
                                        ONVIF Events ${!hasOnvif ? '(Not Supported)' : ''}
                                    </option>
                                    <option value="baichuan" ${settings.motion_recording?.detection_method === 'baichuan' ? 'selected' : ''} ${!isReolink ? 'disabled' : ''}>
                                        Baichuan (Reolink Native) ${!isReolink ? '(Reolink Only)' : ''}
                                    </option>
                                    <option value="ffmpeg" ${settings.motion_recording?.detection_method === 'ffmpeg' ? 'selected' : ''}>
                                        FFmpeg Video Analysis
                                    </option>
                                    <option value="none" ${settings.motion_recording?.detection_method === 'none' ? 'selected' : ''}>
                                        Disabled
                                    </option>
                                </select>
                                ${!hasOnvif ? '<span class="form-description" style="color: #e74c3c;">Camera does not support ONVIF</span>' : ''}
                                ${isReolink ? '<span class="form-description" style="color: #27ae60;">Baichuan protocol available for real-time motion events</span>' : ''}
                            </div>

                            <div class="recording-form-group">
                                <label for="recording-source">Recording Source</label>
                                <select id="recording-source" name="recording_source" disabled>
                                    <option value="${resolvedRecordingSource.value}" selected>
                                        ${resolvedRecordingSource.label}
                                    </option>
                                </select>
                                <span class="form-description">${resolvedRecordingSource.description}</span>
                            </div>
                        </div>

                        <div class="recording-form-row">
                            <div class="recording-form-group">
                                <label for="segment-duration">Segment Duration (seconds)</label>
                                <input type="number"
                                       id="segment-duration"
                                       name="segment_duration"
                                       min="10"
                                       max="3600"
                                       value="${settings.motion_recording?.segment_duration_sec || 30}">
                                <span class="form-description">Length of each recording segment</span>
                            </div>

                            <div class="recording-form-group">
                                <label for="max-age">Max Age (days)</label>
                                <input type="number"
                                       id="max-age"
                                       name="max_age"
                                       min="1"
                                       max="365"
                                       value="${settings.motion_recording?.max_age_days || 7}">
                                <span class="form-description">Delete recordings older than this</span>
                            </div>
                        </div>

                        <!-- Pre-Buffer Enable Toggle -->
                        <div class="recording-form-group">
                            <div class="recording-checkbox-wrapper">
                                <input type="checkbox"
                                       id="pre-buffer-enabled"
                                       name="pre_buffer_enabled"
                                       ${settings.motion_recording?.pre_buffer_enabled ? 'checked' : ''}>
                                <label for="pre-buffer-enabled">Enable Pre-Buffer Recording</label>
                            </div>
                            <span class="form-description">Continuously buffer video to capture footage before motion events. Uses additional disk I/O and CPU.</span>
                        </div>

                        <div class="recording-form-row">
                            <div class="recording-form-group">
                                <label for="pre-buffer">Pre-Buffer (seconds)</label>
                                <input type="number"
                                       id="pre-buffer"
                                       name="pre_buffer"
                                       min="0"
                                       max="60"
                                       value="${settings.motion_recording?.pre_buffer_sec || 5}"
                                       ${!settings.motion_recording?.pre_buffer_enabled ? 'disabled' : ''}>
                                <span class="form-description">Record before motion event</span>
                            </div>

                            <div class="recording-form-group">
                                <label for="post-buffer">Post-Buffer (seconds)</label>
                                <input type="number"
                                       id="post-buffer"
                                       name="post_buffer"
                                       min="0"
                                       max="300"
                                       value="${settings.motion_recording?.post_buffer_sec || 10}">
                                <span class="form-description">Record after motion ends</span>
                            </div>
                        </div>

                        <div class="recording-form-group">
                            <label for="quality">Quality</label>
                            <select id="quality" name="quality">
                                <option value="main" ${settings.motion_recording?.quality === 'main' ? 'selected' : ''}>
                                    Main Stream (High Quality)
                                </option>
                                <option value="sub" ${settings.motion_recording?.quality === 'sub' ? 'selected' : ''}>
                                    Sub Stream (Lower Quality, Less Space)
                                </option>
                            </select>
                        </div>
                    </div>

                    <!-- Continuous Recording -->
                    <div class="recording-form-section">
                        <h4><i class="fas fa-circle"></i> Continuous Recording</h4>

                        <div class="recording-form-group">
                            <div class="recording-checkbox-wrapper">
                                <input type="checkbox"
                                       id="continuous-enabled"
                                       name="continuous_enabled"
                                       ${settings.continuous_recording?.enabled ? 'checked' : ''}>
                                <label for="continuous-enabled">Enable 24/7 Recording</label>
                            </div>
                            <span class="form-description">Record continuously, independent of motion</span>
                        </div>

                        <div class="recording-form-row">
                            <div class="recording-form-group">
                                <label for="continuous-segment">Segment Duration (seconds)</label>
                                <input type="number"
                                       id="continuous-segment"
                                       name="continuous_segment"
                                       min="60"
                                       max="7200"
                                       value="${settings.continuous_recording?.segment_duration_sec || 3600}">
                                <span class="form-description">Length of each recording file</span>
                            </div>

                            <div class="recording-form-group">
                                <label for="continuous-max-age">Max Age (days)</label>
                                <input type="number"
                                       id="continuous-max-age"
                                       name="continuous_max_age"
                                       min="1"
                                       max="90"
                                       value="${settings.continuous_recording?.max_age_days || 3}">
                                <span class="form-description">Delete recordings older than this</span>
                            </div>
                        </div>

                        <div class="recording-form-group">
                            <label for="continuous-quality">Quality</label>
                            <select id="continuous-quality" name="continuous_quality">
                                <option value="main" ${settings.continuous_recording?.quality === 'main' ? 'selected' : ''}>
                                    Main Stream
                                </option>
                                <option value="sub" ${settings.continuous_recording?.quality === 'sub' ? 'selected' : ''}>
                                    Sub Stream (Recommended for 24/7)
                                </option>
                            </select>
                        </div>
                    </div>
                </div>

                <!-- ============ TAB: Snapshots ============ -->
                <div class="settings-tab-panel" data-tab-panel="snapshots">
                    <div class="recording-form-section">
                        <h4><i class="fas fa-camera"></i> Snapshots</h4>

                        <div class="recording-form-group">
                            <div class="recording-checkbox-wrapper">
                                <input type="checkbox"
                                       id="snapshots-enabled"
                                       name="snapshots_enabled"
                                       ${settings.snapshots?.enabled ? 'checked' : ''}>
                                <label for="snapshots-enabled">Enable Periodic Snapshots</label>
                            </div>
                            <span class="form-description">Capture still images at intervals</span>
                        </div>

                        <div class="recording-form-row">
                            <div class="recording-form-group">
                                <label for="snapshot-interval">Interval (seconds)</label>
                                <input type="number"
                                       id="snapshot-interval"
                                       name="snapshot_interval"
                                       min="1"
                                       max="3600"
                                       value="${settings.snapshots?.interval_sec || 300}">
                                <span class="form-description">Time between snapshots</span>
                            </div>

                            <div class="recording-form-group">
                                <label for="snapshot-max-age">Max Age (days)</label>
                                <input type="number"
                                       id="snapshot-max-age"
                                       name="snapshot_max_age"
                                       min="1"
                                       max="365"
                                       value="${settings.snapshots?.max_age_days || 14}">
                                <span class="form-description">Delete snapshots older than this</span>
                            </div>
                        </div>

                        <div class="recording-form-group">
                            <label for="snapshot-quality">JPEG Quality (0-100)</label>
                            <input type="number"
                                   id="snapshot-quality"
                                   name="snapshot_quality"
                                   min="1"
                                   max="100"
                                   value="${settings.snapshots?.quality || 85}">
                            <span class="form-description">Higher = better quality, larger files</span>
                        </div>
                    </div>
                </div>

                <!-- ============ TAB: Power ============ -->
                <div class="settings-tab-panel" data-tab-panel="power">
                    ${this._generatePowerCycleSection(cameraId)}
                </div>

                <!-- ============ TAB: Credentials ============ -->
                <div class="settings-tab-panel" data-tab-panel="credentials">
                    ${this._generateCredentialsSection(cameraId, cameraType)}
                </div>

                <!-- ============ TAB: Advanced ============ -->
                <div class="settings-tab-panel" data-tab-panel="advanced">
                    <div class="recording-form-section">
                        <h4><i class="fas fa-cogs"></i> Advanced Configuration</h4>
                        <div class="recording-alert recording-alert-warning" style="margin-bottom: 16px;">
                            <i class="fas fa-exclamation-triangle"></i>
                            <span>These settings map directly to <code>cameras.json</code>. Incorrect values
                            can break streaming or recording. Hover or tap the <strong>(?)</strong> icon
                            next to each field for an explanation.</span>
                        </div>
                        <div id="advanced-fields-loading" style="text-align: center; padding: 20px;">
                            <span class="recording-loading" style="width: 24px; height: 24px; border-width: 3px;"></span>
                            <span style="margin-left: 10px; color: #999;">Loading camera configuration...</span>
                        </div>
                        <div id="advanced-fields-container" style="display: none;"></div>
                        <div id="advanced-save-status" style="display: none; margin-top: 10px;"></div>
                        <div id="advanced-actions" style="display: none; margin-top: 16px; text-align: right;">
                            <button type="button" id="save-advanced-btn"
                                    class="recording-btn recording-btn-primary">
                                <i class="fas fa-save"></i> Save Advanced Settings
                            </button>
                        </div>
                    </div>
                </div>

                <!-- Form Actions (visible on all tabs except Advanced which has its own save) -->
                <div class="recording-form-actions" id="main-form-actions">
                    <button type="button" class="recording-btn recording-btn-secondary" id="cancel-settings-btn">
                        <i class="fas fa-times"></i> Cancel
                    </button>
                    <button type="submit" class="recording-btn recording-btn-primary" id="save-settings-btn">
                        <i class="fas fa-save"></i> Save Settings
                    </button>
                </div>
            </form>
        `;
    }

    // =========================================================================
    //  CREDENTIALS SECTION
    // =========================================================================

    /**
     * Generate the Credentials tab HTML.
     * Loads current credential status asynchronously after render.
     */
    _generateCredentialsSection(cameraId, cameraType) {
        this._credentialsCameraId = cameraId;
        this._credentialsCameraType = cameraType;

        const brandLabel = (cameraType || 'unknown').charAt(0).toUpperCase() + (cameraType || 'unknown').slice(1);

        return `
            <div class="recording-form-section">
                <h4><i class="fas fa-key"></i> Camera Credentials</h4>
                <span class="form-description" style="display: block; margin-bottom: 16px;">
                    Credentials used for RTSP, ONVIF, and API access to this camera.
                    You can store credentials per-camera (override) or per-brand (shared by all ${brandLabel} cameras).
                </span>

                <div id="credentials-loading" style="text-align: center; padding: 16px;">
                    <span class="recording-loading" style="width: 20px; height: 20px; border-width: 2px;"></span>
                    Loading credential status...
                </div>

                <div id="credentials-content" style="display: none;">
                    <div id="credentials-current-status" style="margin-bottom: 16px;"></div>

                    <div class="credential-scope-selector">
                        <label>
                            <input type="radio" name="credential_scope" value="camera" checked>
                            Per-Camera (this camera only)
                        </label>
                        <label>
                            <input type="radio" name="credential_scope" value="brand">
                            Brand-Level (all ${brandLabel} cameras)
                        </label>
                    </div>

                    <div class="recording-form-group">
                        <label for="cred-username">Username</label>
                        <input type="text"
                               id="cred-username"
                               name="cred_username"
                               placeholder="Enter username"
                               autocomplete="off"
                               style="background: #2c2c2c; border: 1px solid #444; border-radius: 6px; color: #fff; padding: 10px 12px; width: 100%; font-size: 14px;">
                    </div>

                    <div class="recording-form-group">
                        <label for="cred-password">Password</label>
                        <div style="position: relative;">
                            <input type="password"
                                   id="cred-password"
                                   name="cred_password"
                                   placeholder="Enter password"
                                   autocomplete="new-password"
                                   style="background: #2c2c2c; border: 1px solid #444; border-radius: 6px; color: #fff; padding: 10px 12px; width: 100%; font-size: 14px; padding-right: 40px;">
                            <button type="button" id="toggle-password-btn"
                                    style="position: absolute; right: 10px; top: 50%; transform: translateY(-50%); background: none; border: none; color: #888; cursor: pointer; font-size: 16px;">
                                <i class="fas fa-eye"></i>
                            </button>
                        </div>
                    </div>

                    <div style="display: flex; gap: 10px; align-items: center;">
                        <button type="button" id="save-credentials-btn"
                                class="recording-btn recording-btn-primary">
                            <i class="fas fa-save"></i> Save Credentials
                        </button>
                        <button type="button" id="delete-credentials-btn"
                                class="recording-btn recording-btn-secondary"
                                style="display: none;">
                            <i class="fas fa-trash"></i> Delete Per-Camera
                        </button>
                    </div>
                    <div id="credentials-save-status" style="display: none; margin-top: 10px;"></div>
                </div>
            </div>
        `;
    }

    // =========================================================================
    //  POWER CYCLE SECTION (unchanged logic, extracted)
    // =========================================================================

    /**
     * Generate Power Cycle section HTML
     */
    _generatePowerCycleSection(cameraId) {
        this._powerCycleCameraId = cameraId;

        return `
            <div class="recording-form-section" id="power-cycle-section">
                <h4><i class="fas fa-power-off"></i> Power Management</h4>

                <div class="recording-alert recording-alert-warning" style="margin-bottom: 15px;">
                    <i class="fas fa-exclamation-triangle"></i>
                    <span><strong>Warning:</strong> When enabled, this camera will be automatically
                    power-cycled when it goes OFFLINE (3+ consecutive failures). Use with caution.</span>
                </div>

                <div id="power-cycle-loading" style="text-align: center; padding: 10px;">
                    <span class="recording-loading" style="width: 20px; height: 20px; border-width: 2px;"></span>
                    Loading power settings...
                </div>

                <div id="power-cycle-content" style="display: none;">
                    <div class="recording-form-group">
                        <div class="recording-checkbox-wrapper">
                            <input type="checkbox"
                                   id="power-cycle-enabled"
                                   name="power_cycle_enabled">
                            <label for="power-cycle-enabled">Enable Auto Power-Cycle on Failure</label>
                        </div>
                        <span class="form-description">
                            Automatically power-cycle camera via smart plug when OFFLINE
                        </span>
                    </div>

                    <div class="recording-form-row">
                        <div class="recording-form-group">
                            <label for="power-cycle-cooldown">Cooldown Period (hours)</label>
                            <input type="number"
                                   id="power-cycle-cooldown"
                                   name="power_cycle_cooldown_hours"
                                   min="1"
                                   max="168"
                                   value="24">
                            <span class="form-description">Minimum time between auto power-cycles</span>
                        </div>

                        <div class="recording-form-group">
                            <label for="power-supply-type">Power Supply Type</label>
                            <select id="power-supply-type" name="power_supply_type" disabled>
                                <option value="">Loading...</option>
                            </select>
                            <span class="form-description" id="power-supply-note"></span>
                        </div>
                    </div>

                    <div id="power-supply-not-configured" class="recording-alert recording-alert-info" style="display: none;">
                        <i class="fas fa-info-circle"></i>
                        <span>Auto power-cycle requires <code>power_supply: hubitat</code> and a device ID configured.</span>
                    </div>
                </div>
            </div>
        `;
    }

    // =========================================================================
    //  ADVANCED TAB — dynamic field renderer
    // =========================================================================

    /**
     * Render the Advanced tab fields from full camera config.
     * Called asynchronously after the form is injected into the DOM.
     * @param {Object} config - Full camera config from /api/cameras/<id>
     */
    renderAdvancedFields(config) {
        this.fullCameraConfig = config;
        const $container = $('#advanced-fields-container');
        const SKIP_KEYS = ['id', 'camera_id'];  // Redundant keys to hide

        let html = '';
        const sortedKeys = Object.keys(config).sort((a, b) => {
            // Scalars first, then objects
            const aIsObj = typeof config[a] === 'object' && config[a] !== null;
            const bIsObj = typeof config[b] === 'object' && config[b] !== null;
            if (aIsObj !== bIsObj) return aIsObj ? 1 : -1;
            return a.localeCompare(b);
        });

        for (const key of sortedKeys) {
            if (SKIP_KEYS.includes(key)) continue;
            const value = config[key];
            const help = RecordingSettingsForm.helpFor(key);
            const isImmutable = ['serial', 'camera_id', 'id'].includes(key);
            const helpBtn = `<button type="button" class="setting-help-btn" data-help-key="${key}" title="What is this?">?</button>`;

            if (value !== null && typeof value === 'object' && !Array.isArray(value)) {
                // Collapsible JSON editor for nested objects
                const jsonStr = JSON.stringify(value, null, 2);
                const escapedJson = jsonStr.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
                html += `
                    <div class="advanced-setting-group">
                        <div class="advanced-setting-label">
                            <button type="button" class="advanced-section-toggle" data-toggle-key="${key}">
                                <i class="fas fa-chevron-right"></i>
                            </button>
                            <span class="key-name">${key}</span> ${helpBtn}
                            <span style="color: #666; font-size: 11px; margin-left: auto;">{object}</span>
                        </div>
                        <div class="advanced-nested-content" data-nested-key="${key}" style="display: none;">
                            <textarea class="advanced-setting-input json-editor"
                                      data-adv-key="${key}"
                                      data-adv-type="json"
                                      rows="8">${jsonStr}</textarea>
                        </div>
                    </div>
                `;
            } else if (Array.isArray(value)) {
                // Array — render as JSON
                const jsonStr = JSON.stringify(value, null, 2);
                html += `
                    <div class="advanced-setting-group">
                        <div class="advanced-setting-label">
                            <button type="button" class="advanced-section-toggle" data-toggle-key="${key}">
                                <i class="fas fa-chevron-right"></i>
                            </button>
                            <span class="key-name">${key}</span> ${helpBtn}
                            <span style="color: #666; font-size: 11px; margin-left: auto;">[array]</span>
                        </div>
                        <div class="advanced-nested-content" data-nested-key="${key}" style="display: none;">
                            <textarea class="advanced-setting-input json-editor"
                                      data-adv-key="${key}"
                                      data-adv-type="json"
                                      rows="4">${jsonStr}</textarea>
                        </div>
                    </div>
                `;
            } else if (typeof value === 'boolean') {
                html += `
                    <div class="advanced-setting-group">
                        <div class="advanced-setting-label">
                            <span class="key-name">${key}</span> ${helpBtn}
                        </div>
                        <label style="display: flex; align-items: center; gap: 8px; cursor: ${isImmutable ? 'not-allowed' : 'pointer'};">
                            <input type="checkbox"
                                   data-adv-key="${key}"
                                   data-adv-type="boolean"
                                   ${value ? 'checked' : ''}
                                   ${isImmutable ? 'disabled' : ''}
                                   style="width: 18px; height: 18px;">
                            <span style="color: #aaa; font-size: 13px;">${value ? 'true' : 'false'}</span>
                        </label>
                    </div>
                `;
            } else if (typeof value === 'number') {
                html += `
                    <div class="advanced-setting-group">
                        <div class="advanced-setting-label">
                            <span class="key-name">${key}</span> ${helpBtn}
                        </div>
                        <input type="number"
                               class="advanced-setting-input"
                               data-adv-key="${key}"
                               data-adv-type="number"
                               value="${value}"
                               ${isImmutable ? 'disabled' : ''}>
                    </div>
                `;
            } else {
                // String or null
                const displayVal = value === null ? '' : String(value);
                const escapedVal = displayVal.replace(/"/g, '&quot;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
                html += `
                    <div class="advanced-setting-group">
                        <div class="advanced-setting-label">
                            <span class="key-name">${key}</span> ${helpBtn}
                            ${value === null ? '<span style="color: #666; font-size: 11px; margin-left: 4px;">null</span>' : ''}
                        </div>
                        <input type="text"
                               class="advanced-setting-input"
                               data-adv-key="${key}"
                               data-adv-type="string"
                               value="${escapedVal}"
                               placeholder="${value === null ? 'null' : ''}"
                               ${isImmutable ? 'disabled' : ''}>
                    </div>
                `;
            }
        }

        $container.html(html);
        $('#advanced-fields-loading').hide();
        $container.show();
        $('#advanced-actions').show();
    }

    // =========================================================================
    //  DATA EXTRACTION & VALIDATION (unchanged)
    // =========================================================================

    /**
     * Extract form data from recording/snapshot tabs
     * @returns {Object} - Structured settings object
     */
    extractFormData() {
        const form = $('#recording-settings-form');

        return {
            motion_recording: {
                enabled: form.find('#motion-enabled').is(':checked'),
                detection_method: form.find('#detection-method').val(),
                recording_source: this.resolvedRecordingSource?.value || 'mediamtx',
                segment_duration_sec: parseInt(form.find('#segment-duration').val(), 10),
                pre_buffer_enabled: form.find('#pre-buffer-enabled').is(':checked'),
                pre_buffer_sec: parseInt(form.find('#pre-buffer').val(), 10),
                post_buffer_sec: parseInt(form.find('#post-buffer').val(), 10),
                max_age_days: parseInt(form.find('#max-age').val(), 10),
                quality: form.find('#quality').val()
            },
            continuous_recording: {
                enabled: form.find('#continuous-enabled').is(':checked'),
                segment_duration_sec: parseInt(form.find('#continuous-segment').val(), 10),
                max_age_days: parseInt(form.find('#continuous-max-age').val(), 10),
                quality: form.find('#continuous-quality').val()
            },
            snapshots: {
                enabled: form.find('#snapshots-enabled').is(':checked'),
                interval_sec: parseInt(form.find('#snapshot-interval').val(), 10),
                max_age_days: parseInt(form.find('#snapshot-max-age').val(), 10),
                quality: parseInt(form.find('#snapshot-quality').val(), 10)
            }
        };
    }

    /**
     * Validate form data
     * @param {Object} data - Form data
     * @returns {Object} - {valid: boolean, errors: Array}
     */
    validateFormData(data) {
        const errors = [];

        if (data.motion_recording.segment_duration_sec < 10 || data.motion_recording.segment_duration_sec > 3600) {
            errors.push('Motion segment duration must be between 10-3600 seconds');
        }

        if (data.motion_recording.max_age_days < 1 || data.motion_recording.max_age_days > 365) {
            errors.push('Motion max age must be between 1-365 days');
        }

        if (data.continuous_recording.segment_duration_sec < 60 || data.continuous_recording.segment_duration_sec > 7200) {
            errors.push('Continuous segment duration must be between 60-7200 seconds');
        }

        if (data.snapshots.quality < 1 || data.snapshots.quality > 100) {
            errors.push('Snapshot quality must be between 1-100');
        }

        return {
            valid: errors.length === 0,
            errors: errors
        };
    }

    // =========================================================================
    //  EVENT ATTACHMENT
    // =========================================================================

    /**
     * Attach all form events: tabs, form submission, rename, video fit,
     * power cycle, credentials, advanced.
     * @param {Function} onSave - Callback when form is saved
     * @param {Function} onCancel - Callback when cancelled
     */
    attachEvents(onSave, onCancel) {
        this.onSaveCallback = onSave;
        const self = this;

        const form = $('#recording-settings-form');

        // ── Tab switching ──
        form.on('click', '.settings-tab-btn', function(e) {
            e.preventDefault();
            const tabId = $(this).data('tab');
            // Update buttons
            form.find('.settings-tab-btn').removeClass('active');
            $(this).addClass('active');
            // Update panels
            form.find('.settings-tab-panel').removeClass('active');
            form.find(`.settings-tab-panel[data-tab-panel="${tabId}"]`).addClass('active');
            // Show/hide main form actions (hide on advanced & credentials tabs)
            if (tabId === 'advanced' || tabId === 'credentials') {
                $('#main-form-actions').hide();
            } else {
                $('#main-form-actions').show();
            }
        });

        // ── Form submission (Recording + Snapshots) ──
        form.on('submit', async (e) => {
            e.preventDefault();
            await this.handleSubmit();
        });

        // ── Cancel ──
        $('#cancel-settings-btn').on('click', () => {
            if (onCancel) onCancel();
        });

        // ── Pre-buffer toggle ──
        $('#pre-buffer-enabled').on('change', function() {
            $('#pre-buffer').prop('disabled', !$(this).is(':checked'));
        });

        // ── Camera rename ──
        $('#rename-camera-btn').on('click', async () => {
            await this.handleRename();
        });
        $('#camera-name').on('keydown', async (e) => {
            if (e.key === 'Enter') {
                e.preventDefault();
                await this.handleRename();
            }
        });

        // ── Video fit toggle ──
        $('#video-fit-camera-toggle').on('change', async function() {
            const isFill = $(this).is(':checked');
            const fit = isFill ? 'fill' : 'cover';
            $('#video-fit-camera-label').text(isFill ? 'Fill (stretch, no crop)' : 'Cover (crop edges, no deform)');
            await self._saveVideoFit(fit);
        });
        $('#clear-video-fit-btn').on('click', async () => {
            await self._saveVideoFit(null);
            $('#video-fit-camera-toggle').prop('checked', false);
            $('#video-fit-camera-label').text('Cover (crop edges, no deform)  — using account default');
        });

        // ── Power cycle settings (async load) ──
        if (this._powerCycleCameraId) {
            this.loadPowerCycleSettings(this._powerCycleCameraId);
        }

        // ── Credentials (async load) ──
        if (this._credentialsCameraId) {
            this._loadCredentials(this._credentialsCameraId);
        }
        // Password visibility toggle
        $('#toggle-password-btn').on('click', function() {
            const $input = $('#cred-password');
            const isPassword = $input.attr('type') === 'password';
            $input.attr('type', isPassword ? 'text' : 'password');
            $(this).find('i').toggleClass('fa-eye fa-eye-slash');
        });
        // Save credentials
        $('#save-credentials-btn').on('click', async () => {
            await self._saveCredentials();
        });
        // Delete per-camera credentials
        $('#delete-credentials-btn').on('click', async () => {
            await self._deleteCredentials();
        });

        // ── Advanced tab (async load of full config) ──
        this._loadAdvancedConfig();

        // Help (?) buttons — delegated
        $(document).on('click', '.setting-help-btn', function(e) {
            e.preventDefault();
            e.stopPropagation();
            const key = $(this).data('help-key');
            self._showHelpModal(key);
        });

        // Advanced: collapsible sections
        form.on('click', '.advanced-section-toggle', function(e) {
            e.preventDefault();
            const key = $(this).data('toggle-key');
            const $content = $(`[data-nested-key="${key}"]`);
            const $icon = $(this).find('i');
            $content.slideToggle(200);
            $icon.toggleClass('fa-chevron-right fa-chevron-down');
        });

        // Advanced: boolean checkboxes update label
        form.on('change', '[data-adv-type="boolean"]', function() {
            $(this).siblings('span').text($(this).is(':checked') ? 'true' : 'false');
        });

        // Advanced: save button
        $('#save-advanced-btn').on('click', async () => {
            await self._saveAdvancedSettings();
        });
    }

    // =========================================================================
    //  FORM SUBMISSION (recording + snapshot + power)
    // =========================================================================

    /**
     * Handle main form submission (recording, continuous, snapshots, power)
     */
    async handleSubmit() {
        const submitBtn = $('#save-settings-btn');
        const originalHtml = submitBtn.html();

        try {
            const formData = this.extractFormData();
            const validation = this.validateFormData(formData);

            if (!validation.valid) {
                this.showAlert('error', validation.errors.join('<br>'));
                return;
            }

            submitBtn.prop('disabled', true).html('<span class="recording-loading"></span> Saving...');

            await this.controller.updateSettings(this.currentCameraId, formData);
            await this._savePowerCycleSettings(this.currentCameraId);

            this.showAlert('success', 'Settings saved successfully');

            if (this.onSaveCallback) {
                setTimeout(() => this.onSaveCallback(this.currentCameraId, formData), 1000);
            }

        } catch (error) {
            console.error('Form submission error:', error);
            this.showAlert('error', error.message);
            submitBtn.prop('disabled', false).html(originalHtml);
        }
    }

    // =========================================================================
    //  CAMERA RENAME
    // =========================================================================

    /**
     * Handle camera rename via dedicated API endpoint.
     */
    async handleRename() {
        const newName = $('#camera-name').val().trim();
        const $btn = $('#rename-camera-btn');
        const $status = $('#rename-status');
        const originalBtnHtml = $btn.html();

        if (!newName) {
            $status.show().html(
                '<span style="color: #e74c3c;"><i class="fas fa-exclamation-circle"></i> Name cannot be empty</span>'
            );
            return;
        }

        if (newName === this.currentCameraName) {
            $status.show().html(
                '<span style="color: #f39c12;"><i class="fas fa-info-circle"></i> Name unchanged</span>'
            );
            setTimeout(() => $status.fadeOut(300), 2000);
            return;
        }

        try {
            $btn.prop('disabled', true).html('<span class="recording-loading" style="width: 14px; height: 14px; border-width: 2px;"></span>');
            $status.hide();

            const response = await axios.put(`/api/camera/${this.currentCameraId}/name`, {
                name: newName
            });

            if (response.data.success) {
                this.currentCameraName = newName;
                $('#modal-camera-name').text(newName);
                const $streamItem = $(`.stream-item[data-camera-serial="${this.currentCameraId}"]`);
                $streamItem.data('camera-name', newName);
                $streamItem.attr('data-camera-name', newName);
                $streamItem.find('.camera-name').text(newName);

                $status.show().html(
                    `<span style="color: #27ae60;"><i class="fas fa-check-circle"></i> Renamed to "${newName}"</span>`
                );
                setTimeout(() => $status.fadeOut(300), 3000);
            }
        } catch (error) {
            const msg = error.response?.data?.error || error.message;
            $status.show().html(
                `<span style="color: #e74c3c;"><i class="fas fa-exclamation-circle"></i> ${msg}</span>`
            );
            console.error('Rename failed:', error);
        } finally {
            $btn.prop('disabled', false).html(originalBtnHtml);
        }
    }

    // =========================================================================
    //  POWER CYCLE
    // =========================================================================

    /**
     * Save power cycle settings to API
     */
    async _savePowerCycleSettings(cameraId) {
        if ($('#power-cycle-enabled').prop('disabled')) {
            return;
        }

        const powerCycleConfig = {
            enabled: $('#power-cycle-enabled').is(':checked'),
            cooldown_hours: parseInt($('#power-cycle-cooldown').val(), 10) || 24
        };

        try {
            await axios.post(`/api/cameras/${cameraId}/power_supply`, {
                power_cycle_on_failure: powerCycleConfig
            });
            console.log(`Power cycle settings saved for ${cameraId}:`, powerCycleConfig);
        } catch (error) {
            console.error('Failed to save power cycle settings:', error);
        }
    }

    /**
     * Load power cycle settings from API
     */
    async loadPowerCycleSettings(cameraId) {
        try {
            const response = await axios.get(`/api/cameras/${cameraId}/power_supply`);
            const data = response.data;

            $('#power-cycle-loading').hide();
            $('#power-cycle-content').show();

            const powerCycleConfig = data.power_cycle_on_failure || {};
            $('#power-cycle-enabled').prop('checked', powerCycleConfig.enabled || false);
            $('#power-cycle-cooldown').val(powerCycleConfig.cooldown_hours || 24);

            const powerSupplyTypes = data.power_supply_types || ['hubitat', 'poe', 'none'];
            const currentPowerSupply = data.power_supply || 'none';
            const $select = $('#power-supply-type');
            $select.empty();
            powerSupplyTypes.forEach(type => {
                $select.append(`<option value="${type}" ${type === currentPowerSupply ? 'selected' : ''}>${type}</option>`);
            });
            $select.prop('disabled', false);

            if (currentPowerSupply !== 'hubitat' || !data.power_supply_device_id) {
                $('#power-supply-not-configured').show();
                $('#power-cycle-enabled').prop('disabled', true);
                $('#power-cycle-cooldown').prop('disabled', true);
                if (!data.power_supply_device_id && currentPowerSupply === 'hubitat') {
                    $('#power-supply-note').text('Device ID not configured - use /api endpoint to set');
                } else {
                    $('#power-supply-note').text(`Currently: ${currentPowerSupply}`);
                }
            } else {
                $('#power-supply-note').text(`Device ID: ${data.power_supply_device_id}`);
            }

        } catch (error) {
            console.error('Failed to load power cycle settings:', error);
            $('#power-cycle-loading').html(
                '<span style="color: #e74c3c;">Failed to load power settings</span>'
            );
        }
    }

    // =========================================================================
    //  VIDEO FIT
    // =========================================================================

    /**
     * Save per-camera video fit mode override.
     */
    async _saveVideoFit(fit) {
        const $status = $('#video-fit-status');
        $status.show().html('<span style="color: #aaa;"><i class="fas fa-spinner fa-spin"></i> Saving...</span>');
        try {
            const resp = await fetch(`/api/camera/${this.currentCameraId}/display`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ video_fit_mode: fit })
            });
            if (resp.ok) {
                $status.html('<span style="color: #4caf50;"><i class="fas fa-check"></i> Saved</span>');
                const $tile = $(`.stream-item[data-camera-serial="${this.currentCameraId}"]`);
                const effectiveFit = fit || window.VIDEO_FIT_DEFAULT || 'cover';
                $tile.attr('data-video-fit', fit || '');
                $tile.find('.stream-video').css('object-fit', effectiveFit);
            } else {
                $status.html('<span style="color: #f44336;"><i class="fas fa-exclamation-circle"></i> Failed to save</span>');
            }
        } catch (e) {
            $status.html('<span style="color: #f44336;"><i class="fas fa-exclamation-circle"></i> Error: ' + e.message + '</span>');
        }
        setTimeout(() => $status.fadeOut(), 2500);
    }

    // =========================================================================
    //  CREDENTIALS
    // =========================================================================

    /**
     * Load credential status for the current camera.
     */
    async _loadCredentials(cameraId) {
        try {
            const response = await axios.get(`/api/camera/${cameraId}/credentials`);
            const data = response.data;

            $('#credentials-loading').hide();
            $('#credentials-content').show();

            const $status = $('#credentials-current-status');
            if (data.has_credentials) {
                const scopeLabel = data.scope === 'brand' ? 'brand-level' : 'per-camera';
                $status.html(`
                    <div class="recording-alert recording-alert-success" style="margin-bottom: 0;">
                        <i class="fas fa-check-circle"></i>
                        <span>Credentials found (${scopeLabel}). Username: <strong>${data.username}</strong></span>
                    </div>
                `);
                // Pre-fill username
                $('#cred-username').val(data.username);
                // Show delete button for per-camera credentials
                if (data.source === 'db' && data.scope !== 'brand') {
                    $('#delete-credentials-btn').show();
                }
            } else {
                $status.html(`
                    <div class="recording-alert recording-alert-warning" style="margin-bottom: 0;">
                        <i class="fas fa-exclamation-triangle"></i>
                        <span>No credentials configured for this camera.</span>
                    </div>
                `);
            }
        } catch (error) {
            console.error('Failed to load credentials:', error);
            $('#credentials-loading').html(
                '<span style="color: #e74c3c;">Failed to load credential status</span>'
            );
        }
    }

    /**
     * Save credentials via API.
     */
    async _saveCredentials() {
        const username = $('#cred-username').val().trim();
        const password = $('#cred-password').val();
        const scope = $('input[name="credential_scope"]:checked').val();
        const $status = $('#credentials-save-status');

        if (!username || !password) {
            $status.show().html(
                '<span style="color: #e74c3c;"><i class="fas fa-exclamation-circle"></i> Username and password are required</span>'
            );
            return;
        }

        const $btn = $('#save-credentials-btn');
        const originalHtml = $btn.html();
        $btn.prop('disabled', true).html('<span class="recording-loading" style="width: 14px; height: 14px; border-width: 2px;"></span> Saving...');

        try {
            const response = await axios.put(`/api/camera/${this.currentCameraId}/credentials`, {
                username,
                password,
                scope
            });

            if (response.data.success) {
                $status.show().html(
                    '<span style="color: #27ae60;"><i class="fas fa-check-circle"></i> Credentials saved successfully</span>'
                );
                // Clear password field after save
                $('#cred-password').val('');
                // Reload status
                await this._loadCredentials(this.currentCameraId);
            } else {
                throw new Error(response.data.error || 'Unknown error');
            }
        } catch (error) {
            const msg = error.response?.data?.error || error.message;
            $status.show().html(
                `<span style="color: #e74c3c;"><i class="fas fa-exclamation-circle"></i> ${msg}</span>`
            );
        } finally {
            $btn.prop('disabled', false).html(originalHtml);
            setTimeout(() => $status.fadeOut(300), 4000);
        }
    }

    /**
     * Delete per-camera credentials.
     */
    async _deleteCredentials() {
        if (!confirm('Delete per-camera credentials? Brand-level credentials will still apply.')) {
            return;
        }

        try {
            await axios.delete(`/api/camera/${this.currentCameraId}/credentials`);
            await this._loadCredentials(this.currentCameraId);
            $('#credentials-save-status').show().html(
                '<span style="color: #27ae60;"><i class="fas fa-check-circle"></i> Per-camera credentials deleted</span>'
            );
            setTimeout(() => $('#credentials-save-status').fadeOut(300), 3000);
        } catch (error) {
            console.error('Failed to delete credentials:', error);
        }
    }

    // =========================================================================
    //  ADVANCED CONFIG
    // =========================================================================

    /**
     * Load full camera config for Advanced tab.
     */
    async _loadAdvancedConfig() {
        try {
            const response = await axios.get(`/api/cameras/${this.currentCameraId}`);
            this.renderAdvancedFields(response.data);
        } catch (error) {
            console.error('Failed to load advanced config:', error);
            $('#advanced-fields-loading').html(
                '<span style="color: #e74c3c;">Failed to load camera configuration</span>'
            );
        }
    }

    /**
     * Collect edited values from the Advanced tab and PUT to the server.
     */
    async _saveAdvancedSettings() {
        const $btn = $('#save-advanced-btn');
        const $status = $('#advanced-save-status');
        const originalHtml = $btn.html();

        $btn.prop('disabled', true).html('<span class="recording-loading" style="width: 14px; height: 14px; border-width: 2px;"></span> Saving...');

        try {
            const updates = {};

            // Collect all advanced inputs
            const self = this;
            $('[data-adv-key]').each(function(_idx, el) {
                const $el = $(el);
                const key = $el.data('adv-key');
                const type = $el.data('adv-type');

                // Skip immutable keys
                if (['serial', 'camera_id', 'id'].includes(key)) return;

                let newValue;
                if (type === 'json') {
                    try {
                        newValue = JSON.parse($el.val());
                    } catch (e) {
                        throw new Error(`Invalid JSON for "${key}": ${e.message}`);
                    }
                } else if (type === 'boolean') {
                    newValue = $el.is(':checked');
                } else if (type === 'number') {
                    newValue = parseFloat($el.val());
                    if (isNaN(newValue)) newValue = 0;
                } else {
                    // string — treat empty as null if original was null
                    const val = $el.val();
                    const origVal = self.fullCameraConfig ? self.fullCameraConfig[key] : undefined;
                    newValue = (val === '' && origVal === null) ? null : val;
                }

                // Only include if value changed
                const originalJson = JSON.stringify(self.fullCameraConfig?.[key]);
                const updatedJson = JSON.stringify(newValue);
                if (originalJson !== updatedJson) {
                    updates[key] = newValue;
                }
            });

            if (Object.keys(updates).length === 0) {
                $status.show().html(
                    '<span style="color: #f39c12;"><i class="fas fa-info-circle"></i> No changes detected</span>'
                );
                setTimeout(() => $status.fadeOut(300), 3000);
                return;
            }

            const response = await axios.put(`/api/camera/${this.currentCameraId}/settings`, updates);

            if (response.data.success) {
                $status.show().html(
                    `<span style="color: #27ae60;"><i class="fas fa-check-circle"></i> Updated: ${response.data.updated.join(', ')}</span>`
                );
                // Update local cache
                for (const [k, v] of Object.entries(updates)) {
                    if (this.fullCameraConfig) this.fullCameraConfig[k] = v;
                }
            } else {
                throw new Error(response.data.error || 'Save failed');
            }
        } catch (error) {
            const msg = error.response?.data?.error || error.message;
            $status.show().html(
                `<span style="color: #e74c3c;"><i class="fas fa-exclamation-circle"></i> ${msg}</span>`
            );
        } finally {
            $btn.prop('disabled', false).html(originalHtml);
            setTimeout(() => $status.fadeOut(300), 5000);
        }
    }

    // =========================================================================
    //  HELP MODAL
    // =========================================================================

    /**
     * Show a (?) help modal for a given setting key.
     */
    _showHelpModal(key) {
        const help = RecordingSettingsForm.helpFor(key);

        // Remove any existing help overlay
        $('.setting-help-overlay').remove();

        const depsHtml = help.dependencies
            ? `<div class="help-deps"><i class="fas fa-link"></i> <strong>Dependencies:</strong> ${help.dependencies}</div>`
            : '';

        const $overlay = $(`
            <div class="setting-help-overlay">
                <div class="setting-help-content">
                    <h4><i class="fas fa-question-circle"></i> ${help.title}</h4>
                    <p>${help.description}</p>
                    ${depsHtml}
                    <button type="button" class="help-close-btn">Got it</button>
                </div>
            </div>
        `);

        $('body').append($overlay);

        // Close on button click, overlay click, or Escape
        $overlay.on('click', '.help-close-btn', () => $overlay.remove());
        $overlay.on('click', (e) => {
            if ($(e.target).hasClass('setting-help-overlay')) $overlay.remove();
        });
        $(document).one('keydown.helpModal', (e) => {
            if (e.key === 'Escape') $overlay.remove();
        });
    }

    // =========================================================================
    //  UTILITIES
    // =========================================================================

    /**
     * Show alert message
     */
    showAlert(type, message) {
        const alertHtml = `
            <div class="recording-alert recording-alert-${type}">
                <i class="fas fa-${type === 'error' ? 'exclamation-circle' : type === 'success' ? 'check-circle' : 'info-circle'}"></i>
                <span>${message}</span>
            </div>
        `;

        $('.recording-modal-body .recording-alert').remove();
        $('.recording-modal-body').prepend(alertHtml);

        if (type === 'success') {
            setTimeout(() => {
                $('.recording-modal-body .recording-alert').fadeOut(300, function() {
                    $(this).remove();
                });
            }, 3000);
        }
    }

    /**
     * Resolve recording source based on stream type
     */
    _resolveRecordingSource(streamType) {
        const type = (streamType || '').toUpperCase();

        if (['LL_HLS', 'HLS', 'NEOLINK_LL_HLS'].includes(type)) {
            return {
                value: 'mediamtx',
                label: 'MediaMTX Tap',
                description: 'Records from MediaMTX RTSP output (no extra camera connection)'
            };
        } else if (type === 'MJPEG') {
            return {
                value: 'mjpeg_service',
                label: 'MJPEG Capture Service',
                description: 'Records from MJPEG capture buffer (not yet implemented)'
            };
        } else {
            return {
                value: 'rtsp',
                label: 'Direct RTSP',
                description: 'Records directly from camera RTSP stream'
            };
        }
    }
}
