/**
 * Recording Settings Form Handler
 * Location: ~/0_NVR/static/js/forms/recording-settings-form.js
 * Handles form generation, validation, and submission
 */

export class RecordingSettingsForm {
    constructor(controller) {
        this.controller = controller;
        this.currentCameraId = null;
        this.currentSettings = null;
        this.onSaveCallback = null;
    }

    /**
     * Generate form HTML
     * @param {string} cameraId - Camera ID
     * @param {Object} settings - Current settings
     * @param {Object} cameraCapabilities - Camera capabilities array
     * @param {string} cameraType - Camera type (reolink, amcrest, eufy, unifi)
     * @returns {string} - Form HTML
     */
    generateForm(cameraId, settings, cameraCapabilities = [], cameraType = null) {
        this.currentCameraId = cameraId;
        this.currentSettings = settings;
        
        const hasOnvif = cameraCapabilities.includes('ONVIF');
        const isReolink = cameraType && cameraType.toLowerCase() === 'reolink';
        
        return `
            <form id="recording-settings-form" class="recording-settings-form">
                
                <!-- Motion Recording Section -->
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
                            ${!hasOnvif ? '<span class="form-description" style="color: #e74c3c;">ℹ️ Camera does not support ONVIF</span>' : ''}
                            ${isReolink ? '<span class="form-description" style="color: #27ae60;">✅ Baichuan protocol available for real-time motion events</span>' : ''}
                        </div>
                        
                        <div class="recording-form-group">
                            <label for="recording-source">Recording Source</label>
                            <select id="recording-source" name="recording_source">
                                <option value="auto" ${settings.motion_recording?.recording_source === 'auto' ? 'selected' : ''}>
                                    Auto (Recommended)
                                </option>
                                <option value="mediamtx" ${settings.motion_recording?.recording_source === 'mediamtx' ? 'selected' : ''}>
                                    MediaMTX Tap
                                </option>
                                <option value="rtsp" ${settings.motion_recording?.recording_source === 'rtsp' ? 'selected' : ''}>
                                    Direct RTSP
                                </option>
                                <option value="mjpeg_service" ${settings.motion_recording?.recording_source === 'mjpeg_service' ? 'selected' : ''}>
                                    MJPEG Service
                                </option>
                            </select>
                            <span class="form-description">Source for recording stream</span>
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
                
                <!-- Continuous Recording Section -->
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
                
                <!-- Snapshots Section -->
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
                
                <!-- Form Actions -->
                <div class="recording-form-actions">
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

    /**
     * Extract form data
     * @returns {Object} - Structured settings object
     */
    extractFormData() {
        const form = $('#recording-settings-form');
        
        return {
            motion_recording: {
                enabled: form.find('#motion-enabled').is(':checked'),
                detection_method: form.find('#detection-method').val(),
                recording_source: form.find('#recording-source').val(),
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
        
        // Validate motion recording
        if (data.motion_recording.segment_duration_sec < 10 || data.motion_recording.segment_duration_sec > 3600) {
            errors.push('Motion segment duration must be between 10-3600 seconds');
        }
        
        if (data.motion_recording.max_age_days < 1 || data.motion_recording.max_age_days > 365) {
            errors.push('Motion max age must be between 1-365 days');
        }
        
        // Validate continuous recording
        if (data.continuous_recording.segment_duration_sec < 60 || data.continuous_recording.segment_duration_sec > 7200) {
            errors.push('Continuous segment duration must be between 60-7200 seconds');
        }
        
        // Validate snapshots
        if (data.snapshots.quality < 1 || data.snapshots.quality > 100) {
            errors.push('Snapshot quality must be between 1-100');
        }
        
        return {
            valid: errors.length === 0,
            errors: errors
        };
    }

    /**
     * Attach form events
     * @param {Function} onSave - Callback when form is saved
     * @param {Function} onCancel - Callback when form is cancelled
     */
    attachEvents(onSave, onCancel) {
        this.onSaveCallback = onSave;

        const form = $('#recording-settings-form');

        // Handle form submission
        form.on('submit', async (e) => {
            e.preventDefault();
            await this.handleSubmit();
        });

        // Handle cancel
        $('#cancel-settings-btn').on('click', () => {
            if (onCancel) onCancel();
        });

        // Toggle pre-buffer seconds input based on enable checkbox
        $('#pre-buffer-enabled').on('change', function() {
            $('#pre-buffer').prop('disabled', !$(this).is(':checked'));
        });
    }

    /**
     * Handle form submission
     */
    async handleSubmit() {
        const submitBtn = $('#save-settings-btn');
        const originalHtml = submitBtn.html();
        
        try {
            // Extract and validate data
            const formData = this.extractFormData();
            const validation = this.validateFormData(formData);
            
            if (!validation.valid) {
                this.showAlert('error', validation.errors.join('<br>'));
                return;
            }
            
            // Show loading state
            submitBtn.prop('disabled', true).html('<span class="recording-loading"></span> Saving...');
            
            // Submit to backend
            await this.controller.updateSettings(this.currentCameraId, formData);
            
            // Show success
            this.showAlert('success', 'Settings saved successfully');
            
            // Trigger callback
            if (this.onSaveCallback) {
                setTimeout(() => this.onSaveCallback(this.currentCameraId, formData), 1000);
            }
            
        } catch (error) {
            console.error('Form submission error:', error);
            this.showAlert('error', error.message);
            submitBtn.prop('disabled', false).html(originalHtml);
        }
    }

    /**
     * Show alert message
     * @param {string} type - Alert type (error, success, warning, info)
     * @param {string} message - Alert message
     */
    showAlert(type, message) {
        const alertHtml = `
            <div class="recording-alert recording-alert-${type}">
                <i class="fas fa-${type === 'error' ? 'exclamation-circle' : type === 'success' ? 'check-circle' : 'info-circle'}"></i>
                <span>${message}</span>
            </div>
        `;
        
        // Remove existing alerts
        $('.recording-modal-body .recording-alert').remove();
        
        // Add new alert
        $('.recording-modal-body').prepend(alertHtml);
        
        // Auto-remove success alerts
        if (type === 'success') {
            setTimeout(() => {
                $('.recording-modal-body .recording-alert').fadeOut(300, function() {
                    $(this).remove();
                });
            }, 3000);
        }
    }
}