/**
 * Recording Controller - API Client
 * Location: ~/0_MOBIUS.NVR/static/js/controllers/recording-controller.js
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
            
            // URL segment order: <camera_id>/start, NOT /start/<camera_id>.
            // Backend route in routes/recording.py:96 is
            // `@recording_bp.route('/api/recording/<camera_id>/start', methods=['POST'])`.
            // The pre-2026-06-19 wording `/start/<id>` 404'd in prod —
            // caught when operator hit "Record" on SV3C_Living_3 and saw
            // a 404 toast (memory: project_frozen_stream_no_buttons*).
            // The httpx e2e tests passed because they hand-coded the
            // correct URL; only browser-driven (frontend-built-URL)
            // testing surfaces this bug.
            const response = await axios.post(`${this.baseUrl}/${cameraId}/start`, payload);
            
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
            // Same URL-segment-order fix as startRecording above. Backend
            // route is /api/recording/<camera_id>/stop. The variable name
            // here is `recordingId` but the route's docstring says
            // "recording_id passed as camera_id parameter" — semantic
            // confusion that's a separate cleanup. URL shape is what
            // we're fixing here; the semantic mismatch stays for now.
            const response = await axios.post(`${this.baseUrl}/${recordingId}/stop`);
            
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