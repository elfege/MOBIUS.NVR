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
        ` : ''}

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
}

// Create and export singleton instance
export const settingsUI = new SettingsUI();

// Initialize on document ready
$(document).ready(() => {
    settingsUI.init();
});
