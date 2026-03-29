/**
 * SettingsAPI — Unified API client for all NVR settings.
 *
 * Single point of access for reading/writing settings from JavaScript.
 * Maps to the Python Settings class and routes/settings_routes.py endpoints.
 *
 * Three scopes:
 *   - Global:  nvr_settings table (streaming hub, trusted network, etc.)
 *   - Camera:  cameras table (per-camera config)
 *   - User:    user_camera_preferences table (per-user per-camera prefs)
 *
 * Usage:
 *   import { SettingsAPI } from './services/settings-api.js';
 *   const hub = await SettingsAPI.getGlobal('streaming_hub_global');
 *   await SettingsAPI.setCameraSetting(serial, 'streaming_hub', 'go2rtc');
 *   await SettingsAPI.setUserPreference(serial, 'preferred_stream_type', 'WEBRTC');
 */

export class SettingsAPI {

    // =====================================================================
    //  Internal HTTP helpers
    // =====================================================================

    /**
     * GET request with error handling.
     * @param {string} url - API endpoint
     * @returns {Promise<any>} Response data or null on error
     */
    static async _get(url) {
        try {
            const resp = await axios.get(url);
            return resp.data;
        } catch (e) {
            console.error(`[SettingsAPI] GET ${url} failed:`, e.response?.data || e.message);
            return null;
        }
    }

    /**
     * PUT request with error handling.
     * @param {string} url - API endpoint
     * @param {Object} data - Request body
     * @returns {Promise<Object|null>} Response data or null on error
     */
    static async _put(url, data) {
        try {
            const resp = await axios.put(url, data);
            return resp.data;
        } catch (e) {
            console.error(`[SettingsAPI] PUT ${url} failed:`, e.response?.data || e.message);
            return null;
        }
    }

    // =====================================================================
    //  Global settings (nvr_settings table)
    // =====================================================================

    /**
     * Get a global setting by key.
     * @param {string} key - Setting key (e.g. 'streaming_hub_global')
     * @returns {Promise<string|null>} Setting value or null
     */
    static async getGlobal(key) {
        const data = await this._get(`/api/settings/global/${key}`);
        return data?.value ?? null;
    }

    /**
     * Set a global setting.
     * @param {string} key - Setting key
     * @param {string} value - Setting value
     * @returns {Promise<boolean>} True if saved
     */
    static async setGlobal(key, value) {
        const data = await this._put(`/api/settings/global/${key}`, { value });
        return data?.success === true;
    }

    /**
     * Get all global settings.
     * @returns {Promise<Object>} Key-value map of all settings
     */
    static async getAllGlobals() {
        return await this._get('/api/settings/global') || {};
    }

    // =====================================================================
    //  Per-camera settings (cameras table)
    // =====================================================================

    /**
     * Get full camera config.
     * @param {string} serial - Camera serial number
     * @returns {Promise<Object|null>} Camera config or null
     */
    static async getCamera(serial) {
        return await this._get(`/api/settings/camera/${serial}`);
    }

    /**
     * Get a single camera setting.
     * @param {string} serial
     * @param {string} key
     * @returns {Promise<any>} Setting value
     */
    static async getCameraSetting(serial, key) {
        const data = await this._get(`/api/settings/camera/${serial}/${key}`);
        return data?.value ?? null;
    }

    /**
     * Set a single camera setting.
     * @param {string} serial
     * @param {string} key
     * @param {*} value
     * @returns {Promise<boolean>}
     */
    static async setCameraSetting(serial, key, value) {
        const data = await this._put(`/api/settings/camera/${serial}/${key}`, { value });
        return data?.success === true;
    }

    /**
     * Set multiple camera settings at once.
     * @param {string} serial
     * @param {Object} updates - Key-value pairs to update
     * @returns {Promise<boolean>}
     */
    static async setCameraBulk(serial, updates) {
        const data = await this._put(`/api/settings/camera/${serial}/bulk`, updates);
        return data?.success === true;
    }

    // =====================================================================
    //  Per-user preferences (user_camera_preferences table)
    // =====================================================================

    /**
     * Get all user preferences (all cameras).
     * @returns {Promise<Array>} Array of preference objects
     */
    static async getAllUserPreferences() {
        return await this._get('/api/settings/user/preferences') || [];
    }

    /**
     * Get a specific user preference for a camera.
     * @param {string} serial
     * @param {string} key - e.g. 'preferred_stream_type'
     * @returns {Promise<any>}
     */
    static async getUserPreference(serial, key) {
        const data = await this._get(`/api/settings/user/${serial}/${key}`);
        return data?.value ?? null;
    }

    /**
     * Set a user preference for a camera.
     * @param {string} serial
     * @param {string} key
     * @param {*} value
     * @returns {Promise<boolean>}
     */
    static async setUserPreference(serial, key, value) {
        const data = await this._put(`/api/settings/user/${serial}/${key}`, { value });
        return data?.success === true;
    }

    // =====================================================================
    //  Credentials (delegates to existing endpoints — encryption separate)
    // =====================================================================

    /**
     * Get credential status for a camera.
     * @param {string} serial
     * @returns {Promise<Object|null>}
     */
    static async getCredentials(serial) {
        return await this._get(`/api/camera/${serial}/credentials`);
    }

    /**
     * Save credentials for a camera.
     * @param {string} serial
     * @param {Object} data - {username, password, scope}
     * @returns {Promise<boolean>}
     */
    static async setCredentials(serial, data) {
        const resp = await this._put(`/api/camera/${serial}/credentials`, data);
        return resp?.success === true;
    }
}
