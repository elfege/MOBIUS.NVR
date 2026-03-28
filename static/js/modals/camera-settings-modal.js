/**
 * Camera Settings Modal
 * Location: ~/0_MOBIUS.NVR/static/js/modals/camera-settings-modal.js
 * Orchestrates recording settings UI
 */

import { RecordingController } from '../controllers/recording-controller.js';
import { RecordingSettingsForm } from '../forms/camera-settings-form.js';

export class CameraSettingsModal {
    constructor() {
        this.controller = new RecordingController();
        this.form = new RecordingSettingsForm(this.controller);
        this.$modal = null;
        this.$modalBody = null;
        this.currentCameraId = null;
        this.currentCameraName = null;
        this.cameraCapabilities = [];
        
        this.init();
    }

    /**
     * Initialize modal
     */
    init() {
        this.$modal = $('#recording-settings-modal');
        this.$modalBody = this.$modal.find('.recording-modal-body');
        
        if (!this.$modal.length) {
            console.error('Recording settings modal not found in DOM');
            return;
        }
        
        this.attachModalEvents();
        this.attachCameraButtonEvents();
    }

    /**
     * Attach modal control events
     */
    attachModalEvents() {
        // X button
        this.$modal.find('.recording-modal-close').on('click', () => {
            this.hide();
        });

        // Cancel button
        this.$modal.find('#cancel-settings-btn').on('click', () => {
            this.hide();
        });

        // Backdrop click and Escape key intentionally disabled —
        // only X, Cancel, and Save may close this modal.
    }

    /**
     * Attach click events to camera settings buttons
     */
    attachCameraButtonEvents() {
        $(document).on('click', '.camera-settings-btn', async (e) => {
            e.preventDefault();
            e.stopPropagation();
            
            const $button = $(e.currentTarget);
            const cameraId = $button.data('camera-id');
            const $streamItem = $button.closest('.stream-item');
            const cameraName = $streamItem.data('camera-name');

            // Fetch camera data from API to get capabilities and stream type
            let capabilities = [];
            let cameraType = null;
            let streamType = null;
            try {
                const response = await axios.get(`/api/cameras/${cameraId}`);
                capabilities = response.data.capabilities || [];
                cameraType = response.data.type || null;
                streamType = response.data.stream_type || null;
            } catch (error) {
                console.error(`Failed to load camera data for ${cameraId}:`, error);
            }

            await this.show(cameraId, cameraName, capabilities, cameraType, streamType);
        });
    }

    /**
     * Show modal with camera settings
     * @param {string} cameraId - Camera ID
     * @param {string} cameraName - Camera display name
     * @param {Array} capabilities - Camera capabilities
     * @param {string} cameraType - Camera type (reolink, amcrest, eufy, unifi)
     * @param {string} streamType - Stream type (LL_HLS, MJPEG, etc.)
     */
    async show(cameraId, cameraName, capabilities = [], cameraType = null, streamType = null) {
        this.currentCameraId = cameraId;
        this.currentCameraName = cameraName;
        this.cameraCapabilities = capabilities;
        this.cameraType = cameraType;
        this.streamType = streamType;
        
        // Update modal title
        this.$modal.find('#modal-camera-name').text(cameraName);
        
        // Show loading state
        this.$modalBody.html(`
            <div style="text-align: center; padding: 40px;">
                <div class="recording-loading" style="width: 40px; height: 40px; border-width: 4px;"></div>
                <p style="margin-top: 20px; color: #999;">Loading settings...</p>
            </div>
        `);
        
        // Show modal
        this.$modal.fadeIn(200);
        
        try {
            // Load recording settings, display settings, and full camera config in parallel.
            // Full config is needed upfront for the streaming hub toggle (General tab).
            const [settings, displayResp, cameraConfig] = await Promise.all([
                this.controller.getSettings(cameraId),
                fetch(`/api/camera/${cameraId}/display`).then(r => r.json()).catch(() => ({ video_fit_mode: null })),
                fetch(`/api/cameras/${cameraId}`).then(r => r.json()).catch(() => ({}))
            ]);
            const videoFitMode = displayResp.video_fit_mode || null;
            const streamingHub = cameraConfig.streaming_hub || 'mediamtx';
            const go2rtcSource = cameraConfig.go2rtc_source || null;

            // Generate and insert form
            const formHtml = this.form.generateForm(
                cameraId, settings.settings, capabilities, cameraType,
                this.streamType, cameraName, videoFitMode, streamingHub, go2rtcSource
            );
            this.$modalBody.html(formHtml);

            // Pre-populate the Advanced tab cache so it doesn't re-fetch
            if (Object.keys(cameraConfig).length > 0) {
                this.form.fullCameraConfig = cameraConfig;
            }

            // Attach form events
            this.form.attachEvents(
                (cameraId, formData) => this.onSave(cameraId, formData),
                () => this.hide()
            );
            
        } catch (error) {
            console.error('Failed to load camera settings:', error);
            this.$modalBody.html(`
                <div class="recording-alert recording-alert-error">
                    <i class="fas fa-exclamation-circle"></i>
                    <span>Failed to load settings: ${error.message}</span>
                </div>
                <div style="text-align: center; padding: 20px;">
                    <button class="recording-btn recording-btn-secondary" onclick="$('#recording-settings-modal').fadeOut(200)">
                        <i class="fas fa-times"></i> Close
                    </button>
                </div>
            `);
        }
    }

    /**
     * Hide modal
     */
    hide() {
        this.$modal.fadeOut(200);
        
        // Clear form after animation
        setTimeout(() => {
            this.$modalBody.empty();
            this.currentCameraId = null;
            this.currentCameraName = null;
            this.cameraCapabilities = [];
        }, 200);
    }

    /**
     * Handle successful save
     * @param {string} cameraId - Camera ID
     * @param {Object} formData - Saved form data
     */
    onSave(cameraId, formData) {
        console.log(`Settings saved for ${cameraId}:`, formData);
        
        // Update UI indicator if motion detection is enabled
        this.updateCameraIndicators(cameraId, formData);
        
        // Hide modal
        setTimeout(() => this.hide(), 1500);
    }

    /**
     * Update camera tile indicators based on settings
     * @param {string} cameraId - Camera ID
     * @param {Object} settings - Camera settings
     */
    updateCameraIndicators(cameraId, settings) {
        const $streamItem = $(`.stream-item[data-camera-serial="${cameraId}"]`);
        
        if (!$streamItem.length) return;
        
        // Remove existing recording indicators
        $streamItem.find('.recording-status-indicator').remove();
        
        // Add indicators based on settings
        const indicators = [];
        
        if (settings.motion_recording?.enabled) {
            const method = settings.motion_recording.detection_method;
            const methodLabel = method === 'onvif' ? 'ONVIF' : method === 'ffmpeg' ? 'FFmpeg' : '';
            if (methodLabel) {
                indicators.push(`<span class="recording-indicator recording-indicator-motion">
                    <i class="fas fa-running"></i> ${methodLabel} Motion
                </span>`);
            }
        }
        
        if (settings.continuous_recording?.enabled) {
            indicators.push(`<span class="recording-indicator recording-indicator-continuous">
                <i class="fas fa-circle"></i> 24/7
            </span>`);
        }
        
        if (settings.snapshots?.enabled) {
            indicators.push(`<span class="recording-indicator recording-indicator-snapshots">
                <i class="fas fa-camera"></i> Snapshots
            </span>`);
        }
        
        // Add indicators to stream overlay
        if (indicators.length > 0) {
            const $overlay = $streamItem.find('.stream-overlay');
            $overlay.append(`
                <div class="recording-status-indicator">
                    ${indicators.join('')}
                </div>
            `);
        }
    }
}

// Auto-initialize when DOM is ready
$(document).ready(() => {
    window.cameraSettingsModal = new CameraSettingsModal();
    FullscreenHandler.applyGridStyle()
});