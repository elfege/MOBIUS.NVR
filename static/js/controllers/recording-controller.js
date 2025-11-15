/**
 * Recording Controller - API Client
 * Location: ~/0_NVR/static/js/controllers/recording-controller.js
 * Handles all recording-related API calls
 */

export class RecordingController {
    constructor() {
        this.baseUrl = '/api/recording';
        this.activeRecordings = new Map();
    }

    /**
     * Get recording settings for a camera
     * @param {string} cameraId - Camera ID
     * @returns {Promise<Object>} - Camera recording settings
     */
    async getSettings(cameraId) {
        try {
            const response = await axios.get(`${this.baseUrl}/settings/${cameraId}`);
            return response.data;
        } catch (error) {
            console.error(`Failed to load settings for ${cameraId}:`, error);
            throw new Error(`Failed to load camera settings: ${error.response?.data?.error || error.message}`);
        }
    }

    /**
     * Update recording settings for a camera
     * @param {string} cameraId - Camera ID
     * @param {Object} settings - Settings object
     * @returns {Promise<Object>} - Update result
     */
    async updateSettings(cameraId, settings) {
        try {
            const response = await axios.post(`${this.baseUrl}/settings/${cameraId}`, settings);
            return response.data;
        } catch (error) {
            console.error(`Failed to update settings for ${cameraId}:`, error);
            throw new Error(`Failed to update settings: ${error.response?.data?.error || error.message}`);
        }
    }

    /**
     * Start manual recording for a camera
     * @param {string} cameraId - Camera ID
     * @param {number} duration - Duration in seconds (optional)
     * @returns {Promise<Object>} - Recording info with recording_id
     */
    async startRecording(cameraId, duration = null) {
        try {
            const payload = { camera_id: cameraId };
            if (duration) {
                payload.duration = duration;
            }
            
            const response = await axios.post(`${this.baseUrl}/start/${cameraId}`, payload);
            
            if (response.data.recording_id) {
                this.activeRecordings.set(cameraId, {
                    recordingId: response.data.recording_id,
                    startTime: Date.now(),
                    duration: duration
                });
            }
            
            return response.data;
        } catch (error) {
            console.error(`Failed to start recording for ${cameraId}:`, error);
            throw new Error(`Failed to start recording: ${error.response?.data?.error || error.message}`);
        }
    }

    /**
     * Stop manual recording
     * @param {string} recordingId - Recording ID to stop
     * @returns {Promise<Object>} - Stop result
     */
    async stopRecording(recordingId) {
        try {
            const response = await axios.post(`${this.baseUrl}/stop/${recordingId}`);
            
            // Remove from active recordings
            for (const [cameraId, info] of this.activeRecordings.entries()) {
                if (info.recordingId === recordingId) {
                    this.activeRecordings.delete(cameraId);
                    break;
                }
            }
            
            return response.data;
        } catch (error) {
            console.error(`Failed to stop recording ${recordingId}:`, error);
            throw new Error(`Failed to stop recording: ${error.response?.data?.error || error.message}`);
        }
    }

    /**
     * Get all active recordings
     * @returns {Promise<Array>} - List of active recordings
     */
    async getActiveRecordings() {
        try {
            const response = await axios.get(`${this.baseUrl}/active`);
            return response.data.recordings || [];
        } catch (error) {
            console.error('Failed to get active recordings:', error);
            return [];
        }
    }

    /**
     * Get recording info for a camera
     * @param {string} cameraId - Camera ID
     * @returns {Object|null} - Active recording info or null
     */
    getRecordingInfo(cameraId) {
        return this.activeRecordings.get(cameraId) || null;
    }

    /**
     * Check if camera is currently recording
     * @param {string} cameraId - Camera ID
     * @returns {boolean} - True if recording
     */
    isRecording(cameraId) {
        return this.activeRecordings.has(cameraId);
    }

    /**
     * Sync active recordings from server
     * @returns {Promise<void>}
     */
    async syncActiveRecordings() {
        try {
            const recordings = await this.getActiveRecordings();
            
            // Clear local cache
            this.activeRecordings.clear();
            
            // Rebuild from server data
            // Note: API returns {recording_id, camera_id, start_time, ...}
            recordings.forEach(rec => {
                this.activeRecordings.set(rec.camera_id, {
                    recordingId: rec.recording_id,
                    startTime: rec.start_time * 1000, // Convert to milliseconds
                    duration: rec.duration
                });
            });
        } catch (error) {
            console.error('Failed to sync active recordings:', error);
        }
    }
}