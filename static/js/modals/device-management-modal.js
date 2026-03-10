/**
 * Device Management Modal
 *
 * Admin-only interface for viewing connected devices and toggling trusted status.
 * Trusted devices bypass login entirely — the browser auto-authenticates via
 * a device_token cookie matched against the trusted_devices DB table.
 *
 * Also handles device registration and heartbeat for ALL users (not just admin).
 */

class DeviceManagementModal {
    constructor() {
        this.$modal = $('#device-management-modal');
        this.$backdrop = $('#device-management-backdrop');
        this.$closeBtn = $('#device-management-close');
        this.$deviceList = $('#device-list');
        this.$refreshBtn = $('#device-refresh-btn');

        this.init();
    }

    /**
     * Initialize event handlers
     */
    init() {
        this.$closeBtn.on('click', () => this.hide());
        this.$backdrop.on('click', () => this.hide());
        this.$refreshBtn.on('click', () => this.loadDevices());

        // Trust toggle via event delegation
        this.$deviceList.on('click', '.device-trust-btn', (e) => {
            const deviceId = $(e.currentTarget).data('id');
            const currentTrust = $(e.currentTarget).data('trusted') === true;
            this.toggleTrust(deviceId, !currentTrust);
        });

        // Rename device via event delegation
        this.$deviceList.on('click', '.device-rename-btn', (e) => {
            const deviceId = $(e.currentTarget).data('id');
            const currentName = $(e.currentTarget).data('name') || '';
            this.renameDevice(deviceId, currentName);
        });

        // Delete device via event delegation
        this.$deviceList.on('click', '.device-delete-btn', (e) => {
            const deviceId = $(e.currentTarget).data('id');
            this.deleteDevice(deviceId);
        });
    }

    /**
     * Show device management modal and load devices
     */
    async show() {
        await this.loadDevices();
        this.$modal.fadeIn(200);
        this.$backdrop.fadeIn(200);
    }

    /**
     * Hide device management modal
     */
    hide() {
        this.$modal.fadeOut(200);
        this.$backdrop.fadeOut(200);
    }

    /**
     * Load all devices from admin API
     */
    async loadDevices() {
        try {
            const response = await fetch('/api/admin/devices');
            if (!response.ok) throw new Error('Failed to fetch devices');

            const devices = await response.json();
            this.renderDeviceList(devices);
        } catch (error) {
            console.error('[DeviceModal] Error loading devices:', error);
            this.$deviceList.html('<div class="error-message">Failed to load devices</div>');
        }
    }

    /**
     * Render device list
     *
     * @param {Array} devices - Array of device objects from API
     */
    renderDeviceList(devices) {
        if (!devices || devices.length === 0) {
            this.$deviceList.html('<div class="no-devices-message">No devices registered</div>');
            return;
        }

        const now = new Date();
        const myToken = localStorage.getItem('nvr_device_token');

        const html = devices.map(device => {
            const lastSeen = new Date(device.last_seen);
            const diffMs = now - lastSeen;
            const diffMin = Math.floor(diffMs / 60000);
            const isOnline = diffMin < 5;
            const isThisDevice = device.device_token === myToken;

            // Parse user-agent for a readable device description
            const deviceDesc = this._parseUserAgent(device.user_agent);
            const displayName = device.device_name || deviceDesc;
            const timeAgo = this._formatTimeAgo(diffMs);

            return `
                <div class="device-item ${isOnline ? 'device-online' : 'device-offline'} ${isThisDevice ? 'device-current' : ''}">
                    <div class="device-info">
                        <div class="device-header">
                            <span class="device-status-dot ${isOnline ? 'online' : 'offline'}"></span>
                            <span class="device-name">${this.escapeHtml(displayName)}</span>
                            ${isThisDevice ? '<span class="device-this-badge">This device</span>' : ''}
                            ${device.is_trusted ? '<span class="device-trusted-badge">Trusted</span>' : ''}
                        </div>
                        <div class="device-details">
                            <span class="device-user"><i class="fas fa-user"></i> ${this.escapeHtml(device.username)}</span>
                            <span class="device-ip"><i class="fas fa-network-wired"></i> ${this.escapeHtml(device.ip_address)}</span>
                            <span class="device-last-seen"><i class="fas fa-clock"></i> ${isOnline ? 'Online now' : timeAgo}</span>
                        </div>
                    </div>
                    <div class="device-actions">
                        <button class="btn btn-sm device-trust-btn ${device.is_trusted ? 'btn-trusted' : 'btn-secondary'}"
                                data-id="${device.id}"
                                data-trusted="${device.is_trusted}"
                                title="${device.is_trusted ? 'Revoke trust' : 'Mark as trusted'}">
                            <i class="fas ${device.is_trusted ? 'fa-shield-alt' : 'fa-shield-alt'}"></i>
                        </button>
                        <button class="btn btn-sm btn-secondary device-rename-btn"
                                data-id="${device.id}"
                                data-name="${this.escapeHtml(device.device_name || '')}"
                                title="Rename device">
                            <i class="fas fa-pen"></i>
                        </button>
                        <button class="btn btn-sm btn-danger device-delete-btn"
                                data-id="${device.id}"
                                title="Remove device">
                            <i class="fas fa-trash"></i>
                        </button>
                    </div>
                </div>
            `;
        }).join('');

        this.$deviceList.html(html);
    }

    /**
     * Toggle trust status for a device
     *
     * @param {number} deviceId - Device ID
     * @param {boolean} trusted - New trust state
     */
    async toggleTrust(deviceId, trusted) {
        try {
            const response = await fetch(`/api/admin/devices/${deviceId}/trust`, {
                method: 'PATCH',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ is_trusted: trusted })
            });

            if (!response.ok) {
                const err = await response.json();
                throw new Error(err.error || 'Failed to update trust');
            }

            this.showMessage(trusted ? 'Device marked as trusted' : 'Trust revoked', 'success');
            await this.loadDevices();
        } catch (error) {
            console.error('[DeviceModal] Error toggling trust:', error);
            this.showMessage(error.message, 'error');
        }
    }

    /**
     * Rename a device
     *
     * @param {number} deviceId - Device ID
     * @param {string} currentName - Current device name
     */
    async renameDevice(deviceId, currentName) {
        const newName = prompt('Enter device name:', currentName);
        if (newName === null) return; // cancelled

        try {
            const response = await fetch(`/api/admin/devices/${deviceId}/name`, {
                method: 'PATCH',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ device_name: newName })
            });

            if (!response.ok) {
                const err = await response.json();
                throw new Error(err.error || 'Failed to rename device');
            }

            await this.loadDevices();
        } catch (error) {
            console.error('[DeviceModal] Error renaming device:', error);
            this.showMessage(error.message, 'error');
        }
    }

    /**
     * Delete a device
     *
     * @param {number} deviceId - Device ID
     */
    async deleteDevice(deviceId) {
        if (!confirm('Remove this device? If it was trusted, it will need to log in again.')) {
            return;
        }

        try {
            const response = await fetch(`/api/admin/devices/${deviceId}`, {
                method: 'DELETE'
            });

            if (!response.ok) {
                const err = await response.json();
                throw new Error(err.error || 'Failed to delete device');
            }

            this.showMessage('Device removed', 'success');
            await this.loadDevices();
        } catch (error) {
            console.error('[DeviceModal] Error deleting device:', error);
            this.showMessage(error.message, 'error');
        }
    }

    /**
     * Parse user-agent into a readable device description
     *
     * @param {string} ua - User-Agent string
     * @returns {string} Readable device description
     */
    _parseUserAgent(ua) {
        if (!ua) return 'Unknown device';

        // Detect platform
        let platform = 'Unknown';
        if (/iPad/.test(ua)) platform = 'iPad';
        else if (/iPhone/.test(ua)) platform = 'iPhone';
        else if (/Android/.test(ua)) platform = 'Android';
        else if (/Mac/.test(ua)) platform = 'Mac';
        else if (/Windows/.test(ua)) platform = 'Windows';
        else if (/Linux/.test(ua)) platform = 'Linux';

        // Detect browser
        let browser = '';
        if (/Chrome\//.test(ua) && !/Edg\//.test(ua)) browser = 'Chrome';
        else if (/Safari\//.test(ua) && !/Chrome\//.test(ua)) browser = 'Safari';
        else if (/Firefox\//.test(ua)) browser = 'Firefox';
        else if (/Edg\//.test(ua)) browser = 'Edge';

        return browser ? `${platform} (${browser})` : platform;
    }

    /**
     * Format milliseconds difference into human-readable "time ago" string
     *
     * @param {number} diffMs - Difference in milliseconds
     * @returns {string} Human-readable string
     */
    _formatTimeAgo(diffMs) {
        const minutes = Math.floor(diffMs / 60000);
        if (minutes < 1) return 'Just now';
        if (minutes < 60) return `${minutes}m ago`;
        const hours = Math.floor(minutes / 60);
        if (hours < 24) return `${hours}h ago`;
        const days = Math.floor(hours / 24);
        return `${days}d ago`;
    }

    /**
     * Show temporary message in modal header area
     *
     * @param {string} message - Message text
     * @param {string} type - 'success' or 'error'
     */
    showMessage(message, type = 'success') {
        const $message = $(`
            <div class="device-management-message ${type}">
                ${this.escapeHtml(message)}
            </div>
        `);
        this.$modal.find('.modal-header').after($message);
        setTimeout(() => {
            $message.fadeOut(300, function() { $(this).remove(); });
        }, 3000);
    }

    /**
     * Escape HTML to prevent XSS
     *
     * @param {string} str - String to escape
     * @returns {string} Escaped string
     */
    escapeHtml(str) {
        const div = document.createElement('div');
        div.textContent = str;
        return div.innerHTML;
    }
}


/**
 * Device Token Manager
 *
 * Handles device registration and heartbeat for ALL users (not admin-only).
 * Runs on every page load to ensure the device is registered and tracked.
 */
class DeviceTokenManager {
    constructor() {
        this.tokenKey = 'nvr_device_token';
    }

    /**
     * Initialize: register device with backend and store token
     */
    async register() {
        const existingToken = localStorage.getItem(this.tokenKey);

        try {
            const response = await fetch('/api/device/register', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ device_token: existingToken })
            });

            if (response.ok) {
                const data = await response.json();
                localStorage.setItem(this.tokenKey, data.device_token);
                console.log('[DeviceToken] Registered:', data.device_token.substring(0, 8) + '...');
            }
        } catch (error) {
            // Non-critical — don't block the page
            console.warn('[DeviceToken] Registration failed:', error.message);
        }
    }

    /**
     * Send heartbeat to update last_seen
     */
    async heartbeat() {
        const token = localStorage.getItem(this.tokenKey);
        if (!token) return;

        try {
            await fetch('/api/device/heartbeat', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ device_token: token })
            });
        } catch (error) {
            // Non-critical — silently fail
        }
    }

    /**
     * Get the current device token
     *
     * @returns {string|null} Device token or null if not registered
     */
    getToken() {
        return localStorage.getItem(this.tokenKey);
    }
}


// Initialize on page load
$(document).ready(function() {
    // Device token manager — register this device (all users)
    const deviceTokenManager = new DeviceTokenManager();
    deviceTokenManager.register();
    window.deviceTokenManager = deviceTokenManager;

    // Device management modal — admin only (button won't exist for non-admins)
    const deviceManagementModal = new DeviceManagementModal();
    window.deviceManagementModal = deviceManagementModal;

    $('#menu-manage-devices').on('click', function() {
        // Close nav menu if open
        $('#nav-menu').removeClass('open');
        $('#nav-menu-overlay').removeClass('show');
        deviceManagementModal.show();
    });
});
