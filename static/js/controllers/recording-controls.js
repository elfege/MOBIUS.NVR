/**
 * Manual Recording Controls
 * Location: ~/0_MOBIUS.NVR/static/js/controllers/recording-controls.js
 * Handles manual start/stop recording buttons on camera tiles
 */

import { RecordingController } from './recording-controller.js';

export class RecordingControls {
    constructor() {
        this.controller = new RecordingController();
        this.recordingTimers = new Map();
        this.recordingDurations = new Map();
        
        this.init();
    }

    /**
     * Initialize recording controls
     */
    init() {
        this.attachButtonEvents();
        this.syncActiveRecordings();
        
        // Sync every 30 seconds
        setInterval(() => this.syncActiveRecordings(), 30000);
    }

    /**
     * Attach click events to recording buttons
     */
    attachButtonEvents() {
        $(document).on('click', '.camera-record-btn', async (e) => {
            e.preventDefault();
            e.stopPropagation();
            
            const $button = $(e.currentTarget);
            const cameraId = $button.data('camera-id');
            const isRecording = $button.attr('data-recording') === 'true';
            
            if (isRecording) {
                await this.stopRecording(cameraId, $button);
            } else {
                await this.startRecording(cameraId, $button);
            }
        });
    }

    /**
     * Start recording for a camera
     * @param {string} cameraId - Camera ID
     * @param {jQuery} $button - Recording button element
     */
    async startRecording(cameraId, $button) {
        const $streamItem = $button.closest('.stream-item');
        const cameraName = $streamItem.data('camera-name') || cameraId;
        
        try {
            // Disable button during API call
            $button.prop('disabled', true);
            $button.find('i').removeClass('fa-circle').addClass('fa-spinner fa-spin');
            
            // Start recording (no duration = record until stopped)
            const result = await this.controller.startRecording(cameraId);
            
            if (result.success) {
                // Update button state
                this.setRecordingState($button, true, result.recording_id);
                
                // Show duration counter
                this.showDurationCounter(cameraId, $streamItem);
                
                // Show success notification
                this.showNotification(cameraName, 'Recording started', 'success');
                
                console.log(`Recording started for ${cameraId}: ${result.recording_id}`);
            } else {
                throw new Error('Failed to start recording');
            }
            
        } catch (error) {
            console.error(`Failed to start recording for ${cameraId}:`, error);
            this.showNotification(cameraName, `Failed to start: ${error.message}`, 'error');
            
            // Reset button
            $button.find('i').removeClass('fa-spinner fa-spin').addClass('fa-circle');
        } finally {
            $button.prop('disabled', false);
        }
    }

    /**
     * Stop recording for a camera
     * @param {string} cameraId - Camera ID
     * @param {jQuery} $button - Recording button element
     */
    async stopRecording(cameraId, $button) {
        const $streamItem = $button.closest('.stream-item');
        const cameraName = $streamItem.data('camera-name') || cameraId;
        const recordingInfo = this.controller.getRecordingInfo(cameraId);
        
        if (!recordingInfo) {
            console.warn(`No recording info found for ${cameraId}`);
            return;
        }
        
        try {
            // Disable button during API call
            $button.prop('disabled', true);
            $button.find('i').removeClass('fa-circle').addClass('fa-spinner fa-spin');
            
            // Stop recording
            const result = await this.controller.stopRecording(recordingInfo.recordingId);
            
            if (result.success) {
                // Update button state
                this.setRecordingState($button, false);
                
                // Hide duration counter
                this.hideDurationCounter(cameraId);
                
                // Show success notification
                this.showNotification(cameraName, 'Recording stopped', 'success');
                
                console.log(`Recording stopped for ${cameraId}`);
            } else {
                throw new Error('Failed to stop recording');
            }
            
        } catch (error) {
            console.error(`Failed to stop recording for ${cameraId}:`, error);
            this.showNotification(cameraName, `Failed to stop: ${error.message}`, 'error');
            
            // Reset button
            $button.find('i').removeClass('fa-spinner fa-spin').addClass('fa-circle');
        } finally {
            $button.prop('disabled', false);
        }
    }

    /**
     * Set recording button state
     * @param {jQuery} $button - Button element
     * @param {boolean} isRecording - Recording state
     * @param {string} recordingId - Recording ID (when starting)
     */
    setRecordingState($button, isRecording, recordingId = null) {
        $button.attr('data-recording', isRecording ? 'true' : 'false');
        
        if (isRecording) {
            $button.attr('title', 'Stop Recording').attr('aria-label', 'Stop Recording');
            $button.find('i').removeClass('fa-spinner fa-spin').addClass('fa-circle');
        } else {
            $button.attr('title', 'Start Recording').attr('aria-label', 'Start Recording');
            $button.find('i').removeClass('fa-spinner fa-spin').addClass('fa-circle');
        }
    }

    /**
     * Show duration counter for active recording
     * @param {string} cameraId - Camera ID
     * @param {jQuery} $streamItem - Stream item element
     */
    showDurationCounter(cameraId, $streamItem) {
        // Create duration display if it doesn't exist
        let $duration = $streamItem.find('.recording-duration');
        if (!$duration.length) {
            $duration = $('<div class="recording-duration"></div>');
            $streamItem.append($duration);
        }
        
        $duration.addClass('active').text('00:00');
        
        // Start duration timer
        const startTime = Date.now();
        const timer = setInterval(() => {
            const elapsed = Math.floor((Date.now() - startTime) / 1000);
            const minutes = Math.floor(elapsed / 60);
            const seconds = elapsed % 60;
            $duration.text(`${String(minutes).padStart(2, '0')}:${String(seconds).padStart(2, '0')}`);
        }, 1000);
        
        this.recordingTimers.set(cameraId, timer);
        this.recordingDurations.set(cameraId, $duration);
    }

    /**
     * Hide duration counter
     * @param {string} cameraId - Camera ID
     */
    hideDurationCounter(cameraId) {
        // Clear timer
        const timer = this.recordingTimers.get(cameraId);
        if (timer) {
            clearInterval(timer);
            this.recordingTimers.delete(cameraId);
        }
        
        // Hide display
        const $duration = this.recordingDurations.get(cameraId);
        if ($duration) {
            $duration.removeClass('active');
            this.recordingDurations.delete(cameraId);
        }
    }

    /**
     * Sync active recordings from server
     */
    async syncActiveRecordings() {
        try {
            await this.controller.syncActiveRecordings();
            
            // Update UI for all cameras
            $('.camera-record-btn').each((_, button) => {
                const $button = $(button);
                const cameraId = $button.data('camera-id');
                const isRecording = this.controller.isRecording(cameraId);
                
                // Update button state if changed
                const currentState = $button.attr('data-recording') === 'true';
                if (isRecording !== currentState) {
                    this.setRecordingState($button, isRecording);
                    
                    if (isRecording) {
                        const $streamItem = $button.closest('.stream-item');
                        this.showDurationCounter(cameraId, $streamItem);
                    } else {
                        this.hideDurationCounter(cameraId);
                    }
                }
            });
            
        } catch (error) {
            console.error('Failed to sync active recordings:', error);
        }
    }

    /**
     * Show notification toast
     * @param {string} cameraName - Camera name
     * @param {string} message - Message text
     * @param {string} type - Notification type (success, error, info)
     */
    showNotification(cameraName, message, type = 'info') {
        const iconClass = type === 'success' ? 'fa-check-circle' : 
                         type === 'error' ? 'fa-exclamation-circle' : 
                         'fa-info-circle';
        
        const bgColor = type === 'success' ? 'rgba(46, 204, 113, 0.9)' : 
                       type === 'error' ? 'rgba(231, 76, 60, 0.9)' : 
                       'rgba(52, 152, 219, 0.9)';
        
        const $notification = $(`
            <div class="recording-notification" style="
                position: fixed;
                top: 80px;
                right: 20px;
                background: ${bgColor};
                color: #fff;
                padding: 16px 20px;
                border-radius: 8px;
                box-shadow: 0 4px 12px rgba(0, 0, 0, 0.3);
                z-index: 10000;
                display: flex;
                align-items: center;
                gap: 12px;
                min-width: 300px;
                animation: slideInRight 0.3s ease;
            ">
                <i class="fas ${iconClass}" style="font-size: 20px;"></i>
                <div>
                    <div style="font-weight: 600;">${cameraName}</div>
                    <div style="font-size: 13px; opacity: 0.9;">${message}</div>
                </div>
            </div>
        `);
        
        $('body').append($notification);
        
        // Auto-remove after 3 seconds
        setTimeout(() => {
            $notification.fadeOut(300, function() {
                $(this).remove();
            });
        }, 3000);
    }
}

// Auto-initialize when DOM is ready
$(document).ready(() => {
    window.recordingControls = new RecordingControls();
});