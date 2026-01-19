/**
 * SETTINGS UI MODULE (ES6 + jQuery)
 * Handles UI rendering and event handling for settings panel
 */

import { fullscreenHandler } from './fullscreen-handler.js';

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
        // Cache jQuery selectors (initialized in init)
        this.$overlay = null;
        this.$content = null;
        this.$closeBtn = null;
    }

    init() {
        console.log('[SettingsUI] Initializing...');

        this.$overlay = $('#settings-overlay');
        this.$content = $('.settings-content');
        this.$closeBtn = $('#settings-close');

        this.setupEventListeners();
        this.render();
    }

    setupEventListeners() {
        // Close button
        this.$closeBtn.on('click', () => this.hide());

        // Click outside panel to close
        this.$overlay.on('click', (e) => {
            if ($(e.target).is(this.$overlay)) {
                this.hide();
            }
        });

        // Escape key to close
        $(document).on('keydown', (e) => {
            if (e.key === 'Escape' && this.$overlay.hasClass('active')) {
                this.hide();
            }
        });

        // Fullscreen button click
        this.$content.on('click', '#fullscreen-btn', () => {
            console.log('[SettingsUI] Fullscreen button clicked');
            fullscreenHandler.toggleFullscreen();
        });

        // Auto-fullscreen toggle
        this.$content.on('change', '#auto-fullscreen-toggle', (e) => {
            const enabled = $(e.currentTarget).is(':checked');
            console.log('[SettingsUI] Auto-fullscreen toggled:', enabled);

            fullscreenHandler.setAutoFullscreenEnabled(enabled);
            this.updateDelayInputState(enabled);
        });

        // Auto-fullscreen delay input
        this.$content.on('change', '#auto-fullscreen-delay', (e) => {
            const value = parseInt($(e.currentTarget).val()) || 3;
            console.log('[SettingsUI] Auto-fullscreen delay changed:', value);

            fullscreenHandler.setAutoFullscreenDelay(value);
        });

        // Validate delay input on keyup
        this.$content.on('keyup', '#auto-fullscreen-delay', (e) => {
            const $input = $(e.currentTarget);
            const value = parseInt($input.val());

            if (value < 1) $input.val(1);
            if (value > 60) $input.val(60);
        });

        // Grid style select
        this.$content.on('change', '#grid-style-select', (e) => {
            const style = $(e.currentTarget).val();
            console.log('[SettingsUI] Grid style changed:', style);

            fullscreenHandler.setGridStyle(style);
        });

        // Force MJPEG toggle (desktop only)
        this.$content.on('change', '#force-mjpeg-toggle', (e) => {
            const enabled = $(e.currentTarget).is(':checked');
            console.log('[SettingsUI] Force MJPEG toggled:', enabled);

            localStorage.setItem('forceMJPEG', enabled ? 'true' : 'false');

            // Redirect to apply the setting
            if (enabled) {
                window.location.href = '/streams?forceMJPEG=true';
            } else {
                window.location.href = '/streams';
            }
        });

        // Grid Snapshots Only toggle (desktop only)
        // Uses snapshot polling (like iOS) instead of HLS/WebRTC in grid view
        // Reduces CPU/bandwidth usage at cost of lower framerate (~1 fps)
        this.$content.on('change', '#grid-snapshots-toggle', (e) => {
            const enabled = $(e.currentTarget).is(':checked');
            console.log('[SettingsUI] Grid Snapshots toggled:', enabled);

            localStorage.setItem('gridSnapshotsOnly', enabled ? 'true' : 'false');

            // Reload to apply the setting
            window.location.reload();
        });

        // Fullscreen stream type toggle (HLS vs WebRTC)
        // When enabled, fullscreen mode uses WebRTC for lower latency (~200ms)
        // When disabled, fullscreen mode uses HLS (more stable, ~2-4s latency)
        this.$content.on('change', '#fullscreen-stream-type-toggle', (e) => {
            const useWebRTC = $(e.currentTarget).is(':checked');
            console.log('[SettingsUI] Fullscreen stream type toggled:', useWebRTC ? 'WebRTC' : 'HLS');

            localStorage.setItem('fullscreenStreamType', useWebRTC ? 'webrtc' : 'hls');

            // Reload to apply the setting
            window.location.reload();
        });

        // iOS Force WebRTC Grid toggle (experimental)
        // When enabled, iOS grid view uses WebRTC instead of snapshot polling
        // WARNING: May cause issues with many cameras due to Safari video decode limits
        this.$content.on('change', '#force-webrtc-grid-toggle', (e) => {
            const enabled = $(e.currentTarget).is(':checked');
            console.log('[SettingsUI] iOS Force WebRTC Grid toggled:', enabled);

            if (enabled) {
                // Show confirmation modal before enabling
                const confirmed = confirm(
                    '⚠️ EXPERIMENTAL FEATURE ⚠️\n\n' +
                    'Force WebRTC in Grid Mode may cause:\n\n' +
                    '• Black screens or frozen video\n' +
                    '• Safari video decode limits (~4-8 streams)\n' +
                    '• High battery drain\n' +
                    '• App crashes with many cameras\n\n' +
                    'This setting requires DTLS to be enabled on the server.\n\n' +
                    'Are you sure you want to enable this?'
                );

                if (!confirmed) {
                    // User cancelled - revert checkbox
                    $(e.currentTarget).prop('checked', false);
                    return;
                }
            }

            localStorage.setItem('forceWebRTCGrid', enabled ? 'true' : 'false');

            // Reload to apply the setting
            window.location.reload();
        });

        // Quiet Status Messages toggle
        // When enabled, hides verbose status messages (Refreshing, Degraded, Recovered, etc.)
        // Only shows important statuses: Starting, Connecting, Live, Failed, Error
        this.$content.on('change', '#quiet-status-toggle', (e) => {
            const enabled = $(e.currentTarget).is(':checked');
            console.log('[SettingsUI] Quiet Status Messages toggled:', enabled);

            localStorage.setItem('quietStatusMessages', enabled ? 'true' : 'false');

            // No reload needed - takes effect immediately on next status update
        });

        // Mute all cameras button
        this.$content.on('click', '#mute-all-btn', () => {
            console.log('[SettingsUI] Mute all cameras clicked');
            this.setAllCamerasAudio(false);
        });

        // Unmute all cameras button
        this.$content.on('click', '#unmute-all-btn', () => {
            console.log('[SettingsUI] Unmute all cameras clicked');
            this.setAllCamerasAudio(true);
        });
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
    }

    hide() {
        console.log('[SettingsUI] Hiding settings panel');
        this.$overlay.removeClass('active');
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

        const html = `
        <!-- Fullscreen Button Setting -->
        <div class="setting-row">
            <div class="setting-top">
                <div class="setting-label">
                    <i class="fas fa-expand"></i>
                    Fullscreen Mode
                </div>
                <div class="setting-control">
                    <button id="fullscreen-btn" class="setting-btn setting-btn-primary">
                        <i class="fas fa-expand-arrows-alt"></i>
                        Toggle Fullscreen
                    </button>
                </div>
            </div>
            <div class="setting-description">
                Enter or exit fullscreen mode. You can also press F11 on most browsers.
            </div>
        </div>

        <!-- Auto-Fullscreen Toggle Setting -->
        <div class="setting-row">
            <div class="setting-top">
                <div class="setting-label">
                    <i class="fas fa-magic"></i>
                    Auto-Fullscreen
                </div>
                <div class="setting-control">
                    <label class="setting-toggle">
                        <input type="checkbox" id="auto-fullscreen-toggle"
                               ${settings.autoFullscreenEnabled ? 'checked' : ''}>
                        <span class="toggle-slider"></span>
                    </label>
                </div>
            </div>
            <div class="setting-description">
                Automatically enter fullscreen mode when page loads and after exiting fullscreen.
                <strong>Note:</strong> You must click anywhere on the page first for this to work (browser security).
            </div>

            <!-- Auto-Fullscreen Delay Input -->
            <div class="setting-input-group ${settings.autoFullscreenEnabled ? '' : 'disabled'}"
                 id="delay-input-group">
                <label for="auto-fullscreen-delay" class="setting-input-label">
                    <i class="fas fa-clock"></i>
                    Enter fullscreen after
                </label>
                <input type="number"
                       id="auto-fullscreen-delay"
                       class="setting-input"
                       min="1"
                       max="60"
                       value="${settings.autoFullscreenDelay}"
                       ${settings.autoFullscreenEnabled ? '' : 'disabled'}>
                <span class="setting-input-label">seconds</span>
            </div>
        </div>

        <!-- Grid Style Setting - NEW -->
        <div class="setting-row">
            <div class="setting-top">
                <div class="setting-label">
                    <i class="fas fa-th"></i>
                    Grid Layout Style
                </div>
                <div class="setting-control">
                    <select id="grid-style-select" class="setting-select">
                        <option value="spaced" ${settings.gridStyle === 'spaced' ? 'selected' : ''}>
                            Spaced & Rounded
                        </option>
                        <option value="attached" ${settings.gridStyle === 'attached' ? 'selected' : ''}>
                            Attached (NVR Style)
                        </option>
                    </select>
                </div>
            </div>
            <div class="setting-description">
                <strong>Spaced & Rounded:</strong> Modern look with gaps and rounded corners.<br>
                <strong>Attached:</strong> Professional NVR appearance with no gaps - saves screen space.
            </div>
        </div>

        <!-- Force MJPEG Mode Setting (Desktop Only) -->
        ${!isPortableDevice() ? `
        <div class="setting-row">
            <div class="setting-top">
                <div class="setting-label">
                    <i class="fas fa-image"></i>
                    Force MJPEG Mode
                </div>
                <div class="setting-control">
                    <label class="setting-toggle">
                        <input type="checkbox" id="force-mjpeg-toggle"
                               ${this.isForceMJPEGEnabled() ? 'checked' : ''}>
                        <span class="toggle-slider"></span>
                    </label>
                </div>
            </div>
            <div class="setting-description">
                Use MJPEG (snapshot-based) streams instead of HLS video for all cameras.
                Lower quality but uses less bandwidth and CPU. Page will reload when changed.
            </div>
        </div>

        <!-- Grid Snapshots Only Setting (Desktop Only) -->
        <div class="setting-row">
            <div class="setting-top">
                <div class="setting-label">
                    <i class="fas fa-camera"></i>
                    Grid: Snapshots Only
                </div>
                <div class="setting-control">
                    <label class="setting-toggle">
                        <input type="checkbox" id="grid-snapshots-toggle"
                               ${this.isGridSnapshotsEnabled() ? 'checked' : ''}>
                        <span class="toggle-slider"></span>
                    </label>
                </div>
            </div>
            <div class="setting-description">
                Use snapshot polling (~1 fps) in grid view instead of live video streams.<br>
                Reduces CPU and bandwidth usage. Fullscreen still uses full video.<br>
                <strong>Note:</strong> iOS always uses this mode due to browser limitations.
            </div>
        </div>
        ` : ''}

        <!-- Fullscreen Stream Type Setting (HLS vs WebRTC) -->
        <div class="setting-row">
            <div class="setting-top">
                <div class="setting-label">
                    <i class="fas fa-broadcast-tower"></i>
                    Fullscreen: Use WebRTC
                </div>
                <div class="setting-control">
                    <label class="setting-toggle">
                        <input type="checkbox" id="fullscreen-stream-type-toggle"
                               ${this.isFullscreenWebRTCEnabled() ? 'checked' : ''}>
                        <span class="toggle-slider"></span>
                    </label>
                </div>
            </div>
            <div class="setting-description">
                When enabled, fullscreen uses WebRTC (~200ms latency, lower quality).<br>
                When disabled, fullscreen uses HLS (~2-4s latency, higher quality).<br>
                Page will reload when changed.
            </div>
        </div>

        <!-- iOS WebRTC Grid Mode (Experimental) - Only show on iOS devices -->
        ${isPortableDevice() && /iPad|iPhone|iPod/.test(navigator.userAgent) ? `
        <div class="setting-row">
            <div class="setting-top">
                <div class="setting-label">
                    <i class="fas fa-exclamation-triangle" style="color: #dc3545;"></i>
                    iOS Grid: Force WebRTC
                </div>
                <div class="setting-control">
                    <label class="setting-toggle">
                        <input type="checkbox" id="force-webrtc-grid-toggle"
                               ${this.isForceWebRTCGridEnabled() ? 'checked' : ''}>
                        <span class="toggle-slider"></span>
                    </label>
                </div>
            </div>
            <div class="setting-description" style="background-color: rgba(220,53,69,0.1); padding: 10px; border-radius: 4px; border-left: 3px solid #dc3545;">
                <strong style="color: #dc3545; font-size: 1.1em;">⚠️ EXPERIMENTAL - USE WITH CAUTION</strong><br><br>
                Use WebRTC (~200ms latency) in grid view instead of snapshots (1fps).<br><br>
                <strong style="color: #dc3545;">Known Issues:</strong><br>
                <span style="color: #dc3545;">• Safari limits concurrent video decodes (~4-8 streams)</span><br>
                <span style="color: #dc3545;">• May cause black screens or freezes with many cameras</span><br>
                <span style="color: #dc3545;">• Significantly higher battery and CPU usage</span><br>
                <span style="color: #dc3545;">• Requires DTLS to be enabled on server</span><br><br>
                Page will reload when changed.
            </div>
        </div>
        ` : ''}

        <!-- Quiet Status Messages Setting -->
        <div class="setting-row">
            <div class="setting-top">
                <div class="setting-label">
                    <i class="fas fa-comment-slash"></i>
                    Quiet Status Messages
                </div>
                <div class="setting-control">
                    <label class="setting-toggle">
                        <input type="checkbox" id="quiet-status-toggle"
                               ${this.isQuietStatusEnabled() ? 'checked' : ''}>
                        <span class="toggle-slider"></span>
                    </label>
                </div>
            </div>
            <div class="setting-description">
                Hide verbose status messages like "Refreshing...", "Degraded", "Recovered".<br>
                Only show important statuses: Starting, Connecting, Live, Failed.<br>
                Useful for a cleaner interface when streams are stable.
            </div>
        </div>

        <!-- Audio Controls Setting -->
        <div class="setting-row">
            <div class="setting-top">
                <div class="setting-label">
                    <i class="fas fa-volume-up"></i>
                    Camera Audio
                </div>
                <div class="setting-control" style="display: flex; gap: 8px;">
                    <button id="mute-all-btn" class="setting-btn setting-btn-secondary">
                        <i class="fas fa-volume-mute"></i> Mute All
                    </button>
                    <button id="unmute-all-btn" class="setting-btn setting-btn-primary">
                        <i class="fas fa-volume-up"></i> Unmute All
                    </button>
                </div>
            </div>
            <div class="setting-description">
                Control audio for all cameras at once. Individual camera audio can be toggled using
                the speaker icon on each stream. Audio preferences are saved per camera.
                <br><strong>Note:</strong> Audio starts muted by default (browser autoplay policy).
            </div>
        </div>
    `;

        this.$content.html(html);
        console.log('[SettingsUI] Settings rendered');
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
