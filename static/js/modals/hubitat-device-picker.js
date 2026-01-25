/**
 * Hubitat Device Picker Modal
 * Location: ~/0_NVR/static/js/modals/hubitat-device-picker.js
 *
 * Provides UI for selecting Hubitat smart plug devices to associate with cameras.
 * Features smart matching based on camera name similarity.
 *
 * Usage:
 *   import { HubitatDevicePicker } from './modals/hubitat-device-picker.js';
 *   const picker = new HubitatDevicePicker();
 *   picker.show('T8416P0023352DA9', 'Living Room');
 *
 * Author: NVR System
 * Date: January 24, 2026
 */

export class HubitatDevicePicker {
    constructor() {
        this.$modal = null;
        this.currentCameraSerial = null;
        this.currentCameraName = null;
        this.devices = [];
        this.showAllDevices = false;

        this.init();
    }

    /**
     * Initialize modal - create DOM elements and attach events
     */
    init() {
        // Create modal HTML if it doesn't exist
        if (!$('#hubitat-device-picker-modal').length) {
            this.createModalHTML();
        }

        this.$modal = $('#hubitat-device-picker-modal');
        this.attachEvents();
    }

    /**
     * Create modal HTML structure
     */
    createModalHTML() {
        const modalHTML = `
            <div id="hubitat-device-picker-modal" class="hubitat-modal">
                <div class="hubitat-modal-content">
                    <div class="hubitat-modal-header">
                        <h3>Select Power Device</h3>
                        <button class="hubitat-modal-close">&times;</button>
                    </div>
                    <div class="hubitat-modal-body">
                        <div class="hubitat-picker-camera-info">
                            <span class="hubitat-picker-label">Camera:</span>
                            <span class="hubitat-picker-camera-name"></span>
                        </div>
                        <div class="hubitat-picker-status">
                            <span class="hubitat-picker-status-text">Loading devices...</span>
                        </div>
                        <div class="hubitat-devices-list"></div>
                        <div class="hubitat-show-more">
                            <button class="hubitat-show-more-btn">Show more devices</button>
                        </div>
                    </div>
                </div>
            </div>
        `;

        $('body').append(modalHTML);
    }

    /**
     * Attach event handlers
     */
    attachEvents() {
        // Close button
        this.$modal.on('click', '.hubitat-modal-close', () => {
            this.hide();
        });

        // Click outside modal to close
        this.$modal.on('click', (e) => {
            if ($(e.target).hasClass('hubitat-modal')) {
                this.hide();
            }
        });

        // Escape key to close
        $(document).on('keydown', (e) => {
            if (e.key === 'Escape' && this.$modal.is(':visible')) {
                this.hide();
            }
        });

        // Show more devices button
        this.$modal.on('click', '.hubitat-show-more-btn', () => {
            this.showAllDevices = true;
            this.renderDevices();
        });

        // Device selection
        this.$modal.on('click', '.hubitat-device-item', async (e) => {
            const $item = $(e.currentTarget);
            const deviceId = $item.data('device-id');
            const deviceLabel = $item.data('device-label');

            await this.selectDevice(deviceId, deviceLabel);
        });
    }

    /**
     * Show the device picker modal
     * @param {string} cameraSerial - Camera serial number
     * @param {string} cameraName - Camera display name
     */
    async show(cameraSerial, cameraName) {
        this.currentCameraSerial = cameraSerial;
        this.currentCameraName = cameraName;
        this.showAllDevices = false;
        this.devices = [];

        // Update camera info display
        this.$modal.find('.hubitat-picker-camera-name').text(cameraName || cameraSerial);

        // Reset device list (show it in case it was hidden from previous selection)
        this.$modal.find('.hubitat-devices-list').html('').show();
        this.$modal.find('.hubitat-picker-status-text').text('Loading devices...');
        this.$modal.find('.hubitat-picker-status').show();
        this.$modal.find('.hubitat-show-more').hide();

        // Show modal
        this.$modal.addClass('hubitat-modal-visible');

        // Fetch devices
        await this.loadDevices();
    }

    /**
     * Hide the device picker modal
     */
    hide() {
        this.$modal.removeClass('hubitat-modal-visible');
        this.currentCameraSerial = null;
        this.currentCameraName = null;
    }

    /**
     * Load devices from Hubitat API
     */
    async loadDevices() {
        try {
            const response = await fetch('/api/hubitat/devices/switch');

            if (!response.ok) {
                const error = await response.json();
                throw new Error(error.error || 'Failed to load devices');
            }

            this.devices = await response.json();

            if (this.devices.length === 0) {
                this.$modal.find('.hubitat-picker-status-text')
                    .text('No switch devices found in Hubitat');
                return;
            }

            // Score and sort devices by name similarity
            this.scoreDevices();

            // Render device list
            this.$modal.find('.hubitat-picker-status').hide();
            this.renderDevices();

        } catch (error) {
            console.error('[HubitatDevicePicker] Failed to load devices:', error);
            this.$modal.find('.hubitat-picker-status-text')
                .text(`Error: ${error.message}`);
        }
    }

    /**
     * Score devices by name similarity to camera name
     */
    scoreDevices() {
        const cameraName = (this.currentCameraName || '').toLowerCase();
        const cameraWords = cameraName.split(/\s+/).filter(w => w.length > 2);

        this.devices = this.devices.map(device => {
            const deviceLabel = (device.label || '').toLowerCase();
            const deviceWords = deviceLabel.split(/\s+/).filter(w => w.length > 2);

            // Calculate word overlap score
            let score = 0;
            for (const word of cameraWords) {
                if (deviceWords.includes(word)) {
                    score += 2;  // Exact word match
                } else if (deviceWords.some(dw => dw.includes(word) || word.includes(dw))) {
                    score += 1;  // Partial match
                }
            }

            // Bonus for "CAM" prefix (user's naming convention)
            if (deviceLabel.startsWith('cam ')) {
                score += 0.5;
            }

            return { ...device, score };
        });

        // Sort by score descending
        this.devices.sort((a, b) => b.score - a.score);
    }

    /**
     * Render the device list
     */
    renderDevices() {
        const $list = this.$modal.find('.hubitat-devices-list');
        $list.html('');

        // Determine how many to show
        const matchedDevices = this.devices.filter(d => d.score > 0);
        const unmatchedDevices = this.devices.filter(d => d.score === 0);

        let devicesToShow;
        if (this.showAllDevices) {
            devicesToShow = this.devices;
        } else {
            // Show matched devices, or first 5 if none match
            devicesToShow = matchedDevices.length > 0
                ? matchedDevices.slice(0, 5)
                : this.devices.slice(0, 5);
        }

        // Render devices
        for (const device of devicesToShow) {
            const isMatch = device.score > 0;
            const $item = $(`
                <div class="hubitat-device-item ${isMatch ? 'hubitat-device-match' : ''}"
                     data-device-id="${device.id}"
                     data-device-label="${device.label}">
                    <span class="hubitat-device-icon">
                        <i class="fas fa-plug"></i>
                    </span>
                    <span class="hubitat-device-label">${device.label}</span>
                    ${isMatch ? '<span class="hubitat-device-match-badge">Likely Match</span>' : ''}
                </div>
            `);
            $list.append($item);
        }

        // Show "Show more" button if there are hidden devices
        if (!this.showAllDevices && this.devices.length > devicesToShow.length) {
            const hiddenCount = this.devices.length - devicesToShow.length;
            this.$modal.find('.hubitat-show-more-btn')
                .text(`Show ${hiddenCount} more device${hiddenCount > 1 ? 's' : ''}`);
            this.$modal.find('.hubitat-show-more').show();
        } else {
            this.$modal.find('.hubitat-show-more').hide();
        }
    }

    /**
     * Handle device selection
     * @param {string} deviceId - Hubitat device ID
     * @param {string} deviceLabel - Device label (for display)
     */
    async selectDevice(deviceId, deviceLabel) {
        try {
            // Show loading state
            this.$modal.find('.hubitat-picker-status-text')
                .text(`Saving ${deviceLabel}...`);
            this.$modal.find('.hubitat-picker-status').show();
            this.$modal.find('.hubitat-devices-list').hide();

            // Save device to camera config using power_supply endpoint
            const response = await fetch(`/api/cameras/${this.currentCameraSerial}/power_supply`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({ device_id: deviceId }),
            });

            if (!response.ok) {
                const error = await response.json();
                throw new Error(error.error || 'Failed to save device');
            }

            console.log(`[HubitatDevicePicker] Set device ${deviceId} (${deviceLabel}) for camera ${this.currentCameraSerial}`);

            // Hide modal
            this.hide();

            // Trigger callback if provided
            if (this.onDeviceSelected) {
                this.onDeviceSelected(this.currentCameraSerial, deviceId, deviceLabel);
            }

        } catch (error) {
            console.error('[HubitatDevicePicker] Failed to save device:', error);
            this.$modal.find('.hubitat-picker-status-text')
                .text(`Error: ${error.message}`);
            this.$modal.find('.hubitat-devices-list').show();
        }
    }

    /**
     * Set callback for device selection
     * @param {Function} callback - Called with (cameraSerial, deviceId, deviceLabel)
     */
    setOnDeviceSelected(callback) {
        this.onDeviceSelected = callback;
    }
}

// Create singleton instance
export const hubitatDevicePicker = new HubitatDevicePicker();
