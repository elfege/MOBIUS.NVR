/**
 * SETTINGS UI MODULE (ES6 + jQuery)
 * Handles UI rendering and event handling for settings panel
 */

import { fullscreenHandler } from '../settings/fullscreen-handler.js';
import { storageStatus } from '../settings/storage-status.js';

/**
 * Detect if current device is a portable/mobile device
 * Used to hide certain settings that don't apply to mobile
 */
function isPortableDevice() {
    return /iPad|iPhone|iPod|Android/i.test(navigator.userAgent) ||
        (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);
}

export class SettingsUI {
    constructor() {
        this.$overlay = null;
        this.$content = null;
        this.$closeBtn = null;
        this.$headerActions = null;  // Save/Cancel bar in header
        this.$saveBtn = null;
        this.$cancelBtn = null;

        // Memoized nvr_settings rows — cleared on save or panel close so next
        // open always reflects the real DB state.
        this._advancedCache = null;
        // Pending edits: { key → newValue } — populated as user types
        this._advancedPending = {};
    }

    init() {
        console.log('[SettingsUI] Initializing...');

        this.$overlay       = $('#global-settings-overlay');
        this.$content       = $('.global-settings-content');
        this.$closeBtn      = $('#global-settings-close');
        this.$headerActions = $('#global-settings-header-actions');
        this.$saveBtn       = $('#global-settings-save');
        this.$cancelBtn     = $('#global-settings-cancel');

        this.setupEventListeners();
        this.render();
    }

    setupEventListeners() {
        // ── Panel visibility ──────────────────────────────────────────────

        this.$closeBtn.on('click', () => this.hide());

        this.$overlay.on('click', (e) => {
            if ($(e.target).is(this.$overlay)) this.hide();
        });

        $(document).on('keydown', (e) => {
            if (e.key === 'Escape' && this.$overlay.hasClass('active')) this.hide();
        });

        // ── Header Save / Cancel (Advanced tab) ──────────────────────────

        this.$saveBtn.on('click', async () => {
            const activeTab = this.$content.find('.settings-tab-btn.active').data('tab');
            if (activeTab === 'streaming') {
                const val = $('#global-hub-select').val();
                await this.saveGlobalHubSetting(val === '' ? null : val);
            } else {
                await this._saveAllPending();
            }
        });

        this.$cancelBtn.on('click', () => {
            this._advancedCache = null;   // force re-fetch from DB
            this._advancedPending = {};
            // Re-render the Advanced tab with fresh DB data
            this.loadAdvancedSettings();
        });

        // ── Tab switching ─────────────────────────────────────────────────

        this.$content.on('click', '.settings-tab-btn', (e) => {
            const tab = $(e.currentTarget).data('tab');
            this.$content.find('.settings-tab-btn').removeClass('active');
            this.$content.find('.settings-tab-panel').removeClass('active');
            $(e.currentTarget).addClass('active');
            this.$content.find(`.settings-tab-panel[data-tab-panel="${tab}"]`).addClass('active');
            if (tab === 'network') this.loadNetworkSettings();
        });

        // ── Fullscreen ────────────────────────────────────────────────────

        this.$content.on('click', '#fullscreen-btn', () => {
            fullscreenHandler.toggleFullscreen();
        });

        this.$content.on('change', '#auto-fullscreen-toggle', (e) => {
            const enabled = $(e.currentTarget).is(':checked');
            fullscreenHandler.setAutoFullscreenEnabled(enabled);
            this.updateDelayInputState(enabled);
        });

        this.$content.on('change', '#auto-fullscreen-delay', (e) => {
            fullscreenHandler.setAutoFullscreenDelay(parseInt($(e.currentTarget).val()) || 3);
        });

        this.$content.on('keyup', '#auto-fullscreen-delay', (e) => {
            const $input = $(e.currentTarget);
            const v = parseInt($input.val());
            if (v < 1) $input.val(1);
            if (v > 60) $input.val(60);
        });

        // ── View ──────────────────────────────────────────────────────────

        this.$content.on('change', '#grid-style-select', (e) => {
            fullscreenHandler.setGridStyle($(e.currentTarget).val());
        });

        // Global video fit mode (cover vs fill) — persisted to DB preferences
        this.$content.on('change', '#video-fit-toggle', (e) => {
            const isFill = $(e.currentTarget).is(':checked');
            const fit = isFill ? 'fill' : 'cover';
            window.VIDEO_FIT_DEFAULT = fit;
            fetch('/api/my-preferences', {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ default_video_fit: fit })
            }).catch(err => console.warn('[SettingsUI] Failed to save video fit preference:', err));
            // Apply immediately to all tiles without a per-camera override
            $('.stream-item').each((_, item) => {
                const $item = $(item);
                if (!$item.data('video-fit')) {
                    $item.find('.stream-video').css('object-fit', fit);
                }
            });
        });

        this.$content.on('change', '#quiet-status-toggle', (e) => {
            localStorage.setItem('quietStatusMessages', $(e.currentTarget).is(':checked') ? 'true' : 'false');
        });

        // Navmap size slider — saved to localStorage, read by _showFullscreenMap()
        this.$content.on('input', '#navmap-size-slider', (e) => {
            const pct = parseInt($(e.currentTarget).val());
            $('#navmap-size-value').text(`${pct}%`);
            localStorage.setItem('navMapSizePercent', String(pct));
        });

        // ── Streaming ─────────────────────────────────────────────────────

        // Global streaming hub override — persisted to nvr_settings table via PostgREST
        this.$content.on('change', '#global-hub-select', (e) => {
            const val = $(e.currentTarget).val();
            // Empty string means "per-camera" (null in DB)
            this.saveGlobalHubSetting(val === '' ? null : val);
        });

        this.$content.on('change', '#fullscreen-stream-type-toggle', (e) => {
            localStorage.setItem('fullscreenStreamType', $(e.currentTarget).is(':checked') ? 'webrtc' : 'hls');
            window.location.reload();
        });

        this.$content.on('change', '#force-mjpeg-toggle', (e) => {
            const enabled = $(e.currentTarget).is(':checked');
            localStorage.setItem('forceMJPEG', enabled ? 'true' : 'false');
            window.location.href = enabled ? '/streams?forceMJPEG=true' : '/streams';
        });

        this.$content.on('change', '#grid-snapshots-toggle', (e) => {
            localStorage.setItem('gridSnapshotsOnly', $(e.currentTarget).is(':checked') ? 'true' : 'false');
            window.location.reload();
        });

        this.$content.on('change', '#force-webrtc-grid-toggle', (e) => {
            const enabled = $(e.currentTarget).is(':checked');
            if (enabled) {
                const confirmed = confirm(
                    '⚠️ EXPERIMENTAL FEATURE ⚠️\n\n' +
                    'Force WebRTC in Grid Mode may cause:\n' +
                    '• Black screens or frozen video\n' +
                    '• Safari video decode limits (~4-8 streams)\n' +
                    '• High battery drain\n' +
                    '• Requires DTLS to be enabled on server\n\n' +
                    'Are you sure?'
                );
                if (!confirmed) {
                    $(e.currentTarget).prop('checked', false);
                    return;
                }
            }
            localStorage.setItem('forceWebRTCGrid', enabled ? 'true' : 'false');
            window.location.reload();
        });

        // ── Audio ─────────────────────────────────────────────────────────

        this.$content.on('click', '#mute-all-btn', () => this.setAllCamerasAudio(false));
        this.$content.on('click', '#unmute-all-btn', () => this.setAllCamerasAudio(true));
    }

    /**
     * Set audio state for all cameras
     * @param {boolean} enabled - true to unmute, false to mute
     */
    setAllCamerasAudio(enabled) {
        const $streamItems = $('.stream-item');

        $streamItems.each((_, item) => {
            const $item = $(item);
            const $video = $item.find('.stream-video');
            const $button = $item.find('.stream-audio-btn');
            const videoEl = $video[0];
            const cameraId = $item.data('camera-serial');

            if (!videoEl || videoEl.tagName !== 'VIDEO') return;

            // Set muted state (muted = !enabled)
            videoEl.muted = !enabled;

            // Update button icon and state
            const $icon = $button.find('i');
            if (enabled) {
                $icon.removeClass('fa-volume-mute').addClass('fa-volume-up');
                $button.addClass('audio-active');
            } else {
                $icon.removeClass('fa-volume-up').addClass('fa-volume-mute');
                $button.removeClass('audio-active');
            }

            // Save preference
            try {
                const prefs = JSON.parse(localStorage.getItem('cameraAudioPreferences') || '{}');
                prefs[cameraId] = enabled;
                localStorage.setItem('cameraAudioPreferences', JSON.stringify(prefs));
            } catch (e) {
                console.warn('[Audio] Failed to save preference:', e);
            }
        });

        console.log(`[SettingsUI] ${enabled ? 'Unmuted' : 'Muted'} all cameras`);
    }

    show() {
        console.log('[SettingsUI] Showing settings panel');
        this.$overlay.addClass('active');
        this.render();
        // Load async DB value after render so the select is in the DOM
        this.loadGlobalHubSetting();
    }

    hide() {
        console.log('[SettingsUI] Hiding settings panel');
        this.$overlay.removeClass('active');

        // Clear Advanced tab memoization so next open reflects live DB state
        this._advancedCache = null;
        this._advancedPending = {};

        // Stop storage status auto-refresh when panel is hidden
        storageStatus.stopAutoRefresh();
    }

    toggle() {
        if (this.$overlay.hasClass('active')) {
            this.hide();
        } else {
            this.show();
        }
    }

    render() {
        console.log('[SettingsUI] Rendering settings...');

        const settings = fullscreenHandler.getSettings();
        const mobile = isPortableDevice();

        const html = `
        <!-- ── Tab bar ─────────────────────────────────────────────── -->
        <div class="settings-tabs">
            <button class="settings-tab-btn active" data-tab="view">
                <i class="fas fa-desktop"></i> View
            </button>
            <button class="settings-tab-btn" data-tab="fullscreen">
                <i class="fas fa-expand"></i> Fullscreen
            </button>
            <button class="settings-tab-btn" data-tab="streaming">
                <i class="fas fa-broadcast-tower"></i> Streaming
            </button>
            <button class="settings-tab-btn" data-tab="audio">
                <i class="fas fa-volume-up"></i> Audio
            </button>
            ${window.USER_ROLE === 'admin' ? '<button class="settings-tab-btn" data-tab="storage"><i class="fas fa-hdd"></i> Storage</button>' : ''}
            ${window.USER_ROLE === 'admin' ? '<button class="settings-tab-btn" data-tab="network"><i class="fas fa-network-wired"></i> Network</button>' : ''}
        </div>

        <!-- ── View tab ────────────────────────────────────────────── -->
        <div class="settings-tab-panel active" data-tab-panel="view">

            <div class="setting-row">
                <div class="setting-top">
                    <div class="setting-label"><i class="fas fa-th"></i> Grid Layout Style</div>
                    <div class="setting-control">
                        <select id="grid-style-select" class="setting-select">
                            <option value="spaced" ${settings.gridStyle === 'spaced' ? 'selected' : ''}>Spaced &amp; Rounded</option>
                            <option value="attached" ${settings.gridStyle === 'attached' ? 'selected' : ''}>Attached (NVR Style)</option>
                        </select>
                    </div>
                </div>
                <div class="setting-description">
                    <strong>Spaced &amp; Rounded:</strong> Modern look with gaps and rounded corners.<br>
                    <strong>Attached:</strong> Professional NVR appearance with no gaps — saves screen space.
                </div>
            </div>

            <div class="setting-row">
                <div class="setting-top">
                    <div class="setting-label"><i class="fas fa-expand-arrows-alt"></i> Video Fit Mode</div>
                    <div class="setting-control">
                        <label class="setting-toggle">
                            <input type="checkbox" id="video-fit-toggle" ${this.isVideoFitFill() ? 'checked' : ''}>
                            <span class="toggle-slider"></span>
                        </label>
                    </div>
                </div>
                <div class="setting-description">
                    <strong>Off (Cover):</strong> Fills tile, crops edges — no image deformation, slight edge loss.<br>
                    <strong>On (Fill):</strong> Stretches to fit tile exactly — no cropping, image may deform if camera aspect differs.<br>
                    This is your default. Individual cameras can override it in their settings.
                </div>
            </div>

            <div class="setting-row">
                <div class="setting-top">
                    <div class="setting-label"><i class="fas fa-comment-slash"></i> Quiet Status Messages</div>
                    <div class="setting-control">
                        <label class="setting-toggle">
                            <input type="checkbox" id="quiet-status-toggle" ${this.isQuietStatusEnabled() ? 'checked' : ''}>
                            <span class="toggle-slider"></span>
                        </label>
                    </div>
                </div>
                <div class="setting-description">
                    Hide verbose status messages like "Refreshing…", "Degraded", "Recovered".<br>
                    Only show important statuses: Starting, Connecting, Live, Failed.
                </div>
            </div>

            <div class="setting-row">
                <div class="setting-top">
                    <div class="setting-label"><i class="fas fa-map"></i> Camera Grid Map Size</div>
                    <div class="setting-control" style="display:flex;align-items:center;gap:10px;min-width:180px;">
                        <input type="range" id="navmap-size-slider" min="10" max="60" step="1"
                               value="${parseInt(localStorage.getItem('navMapSizePercent') || '20')}"
                               style="flex:1;cursor:pointer;">
                        <span id="navmap-size-value" style="min-width:36px;text-align:right;font-size:0.85em;opacity:0.8;">
                            ${parseInt(localStorage.getItem('navMapSizePercent') || '20')}%
                        </span>
                    </div>
                </div>
                <div class="setting-description">
                    Size of the minimap overlay shown when navigating cameras in fullscreen. Default: 20%.
                </div>
            </div>

        </div>

        <!-- ── Fullscreen tab ──────────────────────────────────────── -->
        <div class="settings-tab-panel" data-tab-panel="fullscreen">

            <div class="setting-row">
                <div class="setting-top">
                    <div class="setting-label"><i class="fas fa-expand"></i> Fullscreen Mode</div>
                    <div class="setting-control">
                        <button id="fullscreen-btn" class="setting-btn setting-btn-primary">
                            <i class="fas fa-expand-arrows-alt"></i> Toggle Fullscreen
                        </button>
                    </div>
                </div>
                <div class="setting-description">Enter or exit fullscreen mode. You can also press F11.</div>
            </div>

            <div class="setting-row">
                <div class="setting-top">
                    <div class="setting-label"><i class="fas fa-magic"></i> Auto-Fullscreen</div>
                    <div class="setting-control">
                        <label class="setting-toggle">
                            <input type="checkbox" id="auto-fullscreen-toggle" ${settings.autoFullscreenEnabled ? 'checked' : ''}>
                            <span class="toggle-slider"></span>
                        </label>
                    </div>
                </div>
                <div class="setting-description">
                    Automatically enter fullscreen when page loads and after exiting fullscreen.
                    <strong>Note:</strong> You must click anywhere on the page first (browser security).
                </div>
                <div class="setting-input-group ${settings.autoFullscreenEnabled ? '' : 'disabled'}" id="delay-input-group">
                    <label for="auto-fullscreen-delay" class="setting-input-label">
                        <i class="fas fa-clock"></i> Enter fullscreen after
                    </label>
                    <input type="number" id="auto-fullscreen-delay" class="setting-input"
                           min="1" max="60" value="${settings.autoFullscreenDelay}"
                           ${settings.autoFullscreenEnabled ? '' : 'disabled'}>
                    <span class="setting-input-label">seconds</span>
                </div>
            </div>

        </div>

        <!-- ── Streaming tab ───────────────────────────────────────── -->
        <div class="settings-tab-panel" data-tab-panel="streaming">

            <div class="setting-row">
                <div class="setting-top">
                    <div class="setting-label"><i class="fas fa-server"></i> Global Streaming Hub</div>
                    <div class="setting-control">
                        <select id="global-hub-select" class="setting-select">
                            <option value="">Per-camera (default)</option>
                            <option value="mediamtx">MediaMTX — all cameras</option>
                            <option value="go2rtc">go2rtc — all cameras</option>
                        </select>
                    </div>
                </div>
                <span id="hub-save-status" style="font-size:12px;padding:2px 0 4px 0;display:block;"></span>
                <div class="setting-description">
                    Override the streaming relay for every camera globally.<br>
                    <strong>Per-camera:</strong> Each camera uses its individually configured hub.<br>
                    <strong>MediaMTX:</strong> Force all cameras through MediaMTX (FFmpeg-based, stable).<br>
                    <strong>go2rtc:</strong> Force all cameras through go2rtc (lower latency, WebRTC-native).<br>
                    Takes effect within ~30s (server-side cache). Current streams reconnect on next playback.
                </div>
            </div>

            <div class="setting-row">
                <div class="setting-top">
                    <div class="setting-label"><i class="fas fa-broadcast-tower"></i> Fullscreen: Use WebRTC</div>
                    <div class="setting-control">
                        <label class="setting-toggle">
                            <input type="checkbox" id="fullscreen-stream-type-toggle" ${this.isFullscreenWebRTCEnabled() ? 'checked' : ''}>
                            <span class="toggle-slider"></span>
                        </label>
                    </div>
                </div>
                <div class="setting-description">
                    When enabled, fullscreen uses WebRTC (~200ms latency).<br>
                    When disabled, fullscreen uses HLS (~2-4s latency, higher quality). Page will reload.
                </div>
            </div>

            ${!mobile ? `
            <div class="setting-row">
                <div class="setting-top">
                    <div class="setting-label"><i class="fas fa-image"></i> Force MJPEG Mode</div>
                    <div class="setting-control">
                        <label class="setting-toggle">
                            <input type="checkbox" id="force-mjpeg-toggle" ${this.isForceMJPEGEnabled() ? 'checked' : ''}>
                            <span class="toggle-slider"></span>
                        </label>
                    </div>
                </div>
                <div class="setting-description">
                    Use MJPEG instead of HLS for all cameras. Lower quality, less CPU. Page will reload.
                </div>
            </div>

            <div class="setting-row">
                <div class="setting-top">
                    <div class="setting-label"><i class="fas fa-camera"></i> Grid: Snapshots Only</div>
                    <div class="setting-control">
                        <label class="setting-toggle">
                            <input type="checkbox" id="grid-snapshots-toggle" ${this.isGridSnapshotsEnabled() ? 'checked' : ''}>
                            <span class="toggle-slider"></span>
                        </label>
                    </div>
                </div>
                <div class="setting-description">
                    Use snapshot polling (~1 fps) in grid view instead of live video.<br>
                    Reduces CPU and bandwidth. Fullscreen still uses full video. Page will reload.
                </div>
            </div>
            ` : `
            <div class="setting-row">
                <div class="setting-top">
                    <div class="setting-label"><i class="fas fa-broadcast-tower" style="color:#17a2b8;"></i> Mobile Grid: Force WebRTC</div>
                    <div class="setting-control">
                        <label class="setting-toggle">
                            <input type="checkbox" id="force-webrtc-grid-toggle" ${this.isForceWebRTCGridEnabled() ? 'checked' : ''}>
                            <span class="toggle-slider"></span>
                        </label>
                    </div>
                </div>
                <div class="setting-description" style="border-left:3px solid #17a2b8;padding-left:8px;">
                    <strong style="color:#17a2b8;">Experimental.</strong> Use WebRTC in mobile grid instead of snapshots.<br>
                    • Safari limits ~4-8 concurrent video decodes<br>
                    • Higher battery and CPU usage<br>
                    • Requires DTLS enabled on server. Page will reload.
                </div>
            </div>
            `}

        </div>

        <!-- ── Audio tab ───────────────────────────────────────────── -->
        <div class="settings-tab-panel" data-tab-panel="audio">

            <div class="setting-row">
                <div class="setting-top">
                    <div class="setting-label"><i class="fas fa-volume-up"></i> Camera Audio</div>
                    <div class="setting-control" style="display:flex;gap:8px;">
                        <button id="mute-all-btn" class="setting-btn setting-btn-secondary">
                            <i class="fas fa-volume-mute"></i> Mute All
                        </button>
                        <button id="unmute-all-btn" class="setting-btn setting-btn-primary">
                            <i class="fas fa-volume-up"></i> Unmute All
                        </button>
                    </div>
                </div>
                <div class="setting-description">
                    Control audio for all cameras at once. Individual camera audio can be toggled
                    via the speaker icon on each stream. Preferences saved per camera.<br>
                    <strong>Note:</strong> Audio starts muted by default (browser autoplay policy).
                </div>
            </div>

        </div>

        <!-- ── Network tab (admin only) ─────────────────────────────── -->
        ${window.USER_ROLE === 'admin' ? `
        <div class="settings-tab-panel" data-tab-panel="network">

            <div class="setting-row" id="trusted-network-setting-row">
                <div class="setting-top">
                    <div class="setting-label"><i class="fas fa-shield-alt"></i> Trust This Network</div>
                    <div class="setting-control">
                        <label class="setting-toggle">
                            <input type="checkbox" id="trusted-network-toggle-modal">
                            <span class="toggle-slider"></span>
                        </label>
                        <span id="trusted-network-status" style="font-size:12px;color:#888;margin-left:8px;"></span>
                    </div>
                </div>
                <div class="setting-description">
                    When enabled, clients on the same subnet skip the login screen.<br>
                    <span id="trusted-network-client-info" style="color:#666;font-size:11px;"></span>
                </div>
            </div>

        </div>
        ` : ''}

        <!-- ── Storage tab (admin only) ────────────────────────────── -->
        ${window.USER_ROLE === 'admin' ? `
        <div class="settings-tab-panel" data-tab-panel="storage">

            <div class="setting-row" style="border-left-color:#28a745;">
                <div class="setting-top">
                    <div class="setting-label">
                        <i class="fas fa-hdd" style="color:#28a745;"></i> Storage Status
                    </div>
                </div>
                <div class="setting-description" style="margin-bottom:12px;">
                    View disk usage for recent and archive storage tiers.
                    Migrate old recordings or cleanup archive storage.
                </div>
                <div id="storage-status-container"></div>
            </div>

        </div>
        ` : ''}
    `;

        this.$content.html(html);
        console.log('[SettingsUI] Settings rendered');

        if (window.USER_ROLE === 'admin') {
            storageStatus.init('#storage-status-container');
        }
    }

    /**
     * Fetch the current global streaming hub override from the DB and populate the select.
     * Called after render() so the DOM element exists. Runs async — non-blocking.
     * Flask endpoint: GET /api/settings/global-hub
     * Returns { value: 'go2rtc' | 'mediamtx' | null }
     */
    async loadGlobalHubSetting() {
        try {
            const resp = await fetch('/api/settings/global-hub');
            if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
            const data = await resp.json();
            const current = data.value || '';
            $('#global-hub-select').val(current);
            console.log('[SettingsUI] Global hub setting loaded:', current || '(per-camera)');
        } catch (err) {
            console.warn('[SettingsUI] Failed to load global hub setting:', err);
        }
    }

    /**
     * Persist the global streaming hub override via PostgREST PATCH.
     * @param {string|null} value - 'mediamtx', 'go2rtc', or null (per-camera)
     * Flask endpoint: PUT /api/settings/global-hub
     * Body: { value: 'go2rtc' | 'mediamtx' | null }
     */
    async saveGlobalHubSetting(value) {
        const $status = $('#hub-save-status');
        $status.text('Saving…').css('color', '#aaa');
        try {
            const resp = await fetch('/api/settings/global-hub', {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ value: value })
            });
            if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
            $status.text('Saved').css('color', '#2ecc71');
            setTimeout(() => $status.text(''), 2500);
        } catch (err) {
            $status.text(err.message).css('color', '#e74c3c');
        }
    }

    /**
     * Load trusted network state from the backend and wire the toggle in the Network tab.
     * Called when user switches to the Network tab.
     * Flask endpoint: GET /api/settings/trusted-network
     */
    async loadNetworkSettings() {
        const $toggle = $('#trusted-network-toggle-modal');
        const $status = $('#trusted-network-status');
        const $info = $('#trusted-network-client-info');

        $status.text('Loading…').css('color', '#888');

        try {
            const resp = await fetch('/api/settings/trusted-network');
            if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
            const data = await resp.json();

            $toggle.prop('checked', !!data.enabled);
            $status.text('');
            if (data.client_ip) {
                const subnet = data.on_same_subnet ? ' — same subnet as server' : ' — different subnet';
                $info.text(`Your IP: ${data.client_ip}${subnet}`);
            }

            // Wire toggle (use .off to avoid duplicate bindings on re-entry)
            $toggle.off('change.network').on('change.network', async function () {
                const enabled = this.checked;
                $status.text('Saving…').css('color', '#aaa');
                try {
                    const r = await fetch('/api/settings/trusted-network', {
                        method: 'PUT',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ enabled })
                    });
                    const result = await r.json();
                    if (!r.ok || !result.success) throw new Error(result.error || `HTTP ${r.status}`);
                    $status.text('Saved').css('color', '#2ecc71');
                    setTimeout(() => $status.text(''), 2500);
                } catch (err) {
                    $toggle.prop('checked', !enabled);  // revert
                    $status.text(err.message).css('color', '#e74c3c');
                }
            });
        } catch (err) {
            $status.text(`Failed: ${err.message}`).css('color', '#e74c3c');
        }
    }

    /**
     * Keys that must never appear in the Advanced Settings tab UI.
     * Either sensitive (secret key) or handled by a dedicated tab.
     */
    /** Show the Save/Cancel bar in the settings header. */
    _showHeaderActions() {
        this.$headerActions.css('display', 'flex');
    }

    /** Hide the Save/Cancel bar in the settings header. */
    _hideHeaderActions() {
        // Buttons are always visible — intentional no-op
    }

    /**
     * Save all pending Advanced tab edits in parallel.
     * Clears cache on full success so next open re-fetches from DB.
     */
    async _saveAllPending() {
        const pending = { ...this._advancedPending };
        const keys = Object.keys(pending);
        if (keys.length === 0) {
            return;
        }

        const $container = $('#advanced-settings-container');
        this.$saveBtn.prop('disabled', true).html('<i class="fas fa-spinner fa-spin"></i> Saving…');

        const results = await Promise.allSettled(
            keys.map(async (key) => {
                const $input = $container.find(`.advanced-setting-input[data-key="${key}"]`);
                await this.saveAdvancedSetting(key, pending[key], $input);
                delete this._advancedPending[key];
            })
        );

        this.$saveBtn.prop('disabled', false).html('<i class="fas fa-check"></i> Save');

        const errors = results.filter(r => r.status === 'rejected');
        if (errors.length === 0) {
            this._advancedCache = null;  // force re-fetch on next open
        }
    }

    updateDelayInputState(enabled) {
        const $delayGroup = $('#delay-input-group');
        const $delayInput = $('#auto-fullscreen-delay');

        if (enabled) {
            $delayGroup.removeClass('disabled');
            $delayInput.prop('disabled', false);
        } else {
            $delayGroup.addClass('disabled');
            $delayInput.prop('disabled', true);
        }
    }

    /**
     * Check if video fit default is 'fill' (stretch) vs 'cover' (crop).
     * Reads window.VIDEO_FIT_DEFAULT set by the server, falls back to localStorage.
     */
    isVideoFitFill() {
        return (window.VIDEO_FIT_DEFAULT || localStorage.getItem('videoFitMode') || 'cover') === 'fill';
    }

    /**
     * Check if Force MJPEG mode is enabled
     * Checks both URL param and localStorage
     */
    isForceMJPEGEnabled() {
        // Check URL param first
        const urlParams = new URLSearchParams(window.location.search);
        if (urlParams.get('forceMJPEG') === 'true') {
            return true;
        }
        // Fall back to localStorage
        return localStorage.getItem('forceMJPEG') === 'true';
    }

    /**
     * Check if fullscreen should use WebRTC instead of HLS
     * WebRTC offers lower latency (~200ms) but HLS is more stable
     * Default is HLS (unchecked) for stability
     */
    isFullscreenWebRTCEnabled() {
        return localStorage.getItem('fullscreenStreamType') === 'webrtc';
    }

    /**
     * Check if grid view should use snapshot polling instead of live video.
     * Snapshot mode uses ~1 fps polling, reducing CPU/bandwidth usage.
     * iOS always uses this mode automatically due to Safari limitations.
     * This setting allows desktop users to opt-in for the same behavior.
     */
    isGridSnapshotsEnabled() {
        return localStorage.getItem('gridSnapshotsOnly') === 'true';
    }

    /**
     * Check if iOS should force WebRTC in grid view instead of snapshots.
     * This is an EXPERIMENTAL option - may cause issues with many cameras
     * due to Safari's concurrent video decode limits (~4-8 streams).
     *
     * Requires DTLS to be enabled on the server for iOS WebRTC support.
     */
    isForceWebRTCGridEnabled() {
        return localStorage.getItem('forceWebRTCGrid') === 'true';
    }

    /**
     * Check if quiet status messages mode is enabled.
     * When enabled, verbose status messages (Refreshing, Degraded, Recovered, etc.)
     * are hidden and only important statuses are shown (Starting, Connecting, Live, Failed).
     */
    isQuietStatusEnabled() {
        return localStorage.getItem('quietStatusMessages') === 'true';
    }
}

// Create and export singleton instance
export const settingsUI = new SettingsUI();

// Initialize on document ready
$(document).ready(() => {
    settingsUI.init();
});
