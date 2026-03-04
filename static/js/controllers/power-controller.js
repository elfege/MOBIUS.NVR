/**
 * Power Controller
 * Location: ~/0_MOBIUS.NVR/static/js/controllers/power-controller.js
 *
 * Handles power button clicks in stream controls.
 * - For hubitat: Opens device picker if no device configured, otherwise triggers power cycle
 * - For poe: Triggers power cycle directly (config done elsewhere)
 *
 * Author: NVR System
 * Date: January 24, 2026
 */

import { hubitatDevicePicker } from '../modals/hubitat-device-picker.js';

/**
 * Initialize power control buttons for all streams
 */
function initPowerControls() {
    console.log('[PowerController] Initializing power controls');

    // Attach click handlers to all power buttons
    $(document).on('click', '.stream-power-btn', handlePowerButtonClick);

    // Set up callback for device picker selection
    hubitatDevicePicker.setOnDeviceSelected((cameraSerial, deviceId, deviceLabel) => {
        console.log(`[PowerController] Device selected for ${cameraSerial}: ${deviceLabel} (${deviceId})`);

        // Update button state to show it's now configured
        const $btn = $(`.stream-power-btn[data-camera-id="${cameraSerial}"]`);
        $btn.addClass('power-configured');
        $btn.attr('data-device-id', deviceId);

        // Optionally trigger power cycle immediately after selection
        // triggerPowerCycle(cameraSerial, 'hubitat');
    });
}

/**
 * Handle power button click
 * @param {Event} e - Click event
 */
async function handlePowerButtonClick(e) {
    e.preventDefault();
    e.stopPropagation();

    const $btn = $(e.currentTarget);
    const cameraSerial = $btn.data('camera-id');
    const powerSupply = $btn.data('power-supply');
    const deviceId = $btn.data('device-id');

    console.log(`[PowerController] Power button clicked: ${cameraSerial}, type: ${powerSupply}, deviceId: ${deviceId}`);

    // Check if button is in cycling state
    if ($btn.hasClass('power-cycling')) {
        console.log('[PowerController] Power cycle already in progress');
        return;
    }

    if (powerSupply === 'hubitat') {
        if (!deviceId) {
            // No device configured - open picker
            const cameraName = $btn.closest('.stream-item').data('camera-name') || cameraSerial;
            hubitatDevicePicker.show(cameraSerial, cameraName);
        } else {
            // Device configured - confirm and trigger power cycle
            const cameraName = $btn.closest('.stream-item').data('camera-name') || cameraSerial;
            if (confirm(`Power cycle ${cameraName}?\n\nThis will turn off the smart plug for 10 seconds, then turn it back on.`)) {
                await triggerPowerCycle(cameraSerial, 'hubitat', $btn);
            }
        }
    } else if (powerSupply === 'poe') {
        // POE - trigger directly (config is switch_mac + port in cameras.json)
        const cameraName = $btn.closest('.stream-item').data('camera-name') || cameraSerial;
        if (confirm(`Power cycle ${cameraName} via POE?\n\nThis will cycle the POE port, causing the camera to restart.`)) {
            await triggerPowerCycle(cameraSerial, 'poe', $btn);
        }
    }
}

/**
 * Trigger power cycle via API
 * @param {string} cameraSerial - Camera serial number
 * @param {string} powerType - 'hubitat' or 'poe'
 * @param {jQuery} $btn - Button element for state updates
 */
async function triggerPowerCycle(cameraSerial, powerType, $btn) {
    const endpoint = powerType === 'hubitat'
        ? `/api/power/${cameraSerial}/cycle`
        : `/api/poe/${cameraSerial}/cycle`;

    try {
        // Update button state
        $btn.addClass('power-cycling');
        $btn.removeClass('power-error');

        const response = await fetch(endpoint, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            }
        });

        const result = await response.json();

        if (result.success) {
            console.log(`[PowerController] Power cycle started for ${cameraSerial}`);

            // Poll for completion
            pollPowerStatus(cameraSerial, powerType, $btn);
        } else {
            console.error(`[PowerController] Power cycle failed: ${result.error}`);
            $btn.removeClass('power-cycling');
            $btn.addClass('power-error');
            alert(`Power cycle failed: ${result.error}`);
        }

    } catch (error) {
        console.error(`[PowerController] Power cycle error:`, error);
        $btn.removeClass('power-cycling');
        $btn.addClass('power-error');
        alert(`Power cycle error: ${error.message}`);
    }
}

/**
 * Poll power cycle status until complete
 * @param {string} cameraSerial - Camera serial number
 * @param {string} powerType - 'hubitat' or 'poe'
 * @param {jQuery} $btn - Button element for state updates
 */
function pollPowerStatus(cameraSerial, powerType, $btn) {
    const endpoint = powerType === 'hubitat'
        ? `/api/power/${cameraSerial}/status`
        : `/api/poe/${cameraSerial}/status`;

    const pollInterval = setInterval(async () => {
        try {
            const response = await fetch(endpoint);
            const status = await response.json();

            console.log(`[PowerController] Power status for ${cameraSerial}:`, status.state);

            if (status.state === 'complete') {
                clearInterval(pollInterval);
                $btn.removeClass('power-cycling power-error');
                console.log(`[PowerController] Power cycle complete for ${cameraSerial}`);
            } else if (status.state === 'failed') {
                clearInterval(pollInterval);
                $btn.removeClass('power-cycling');
                $btn.addClass('power-error');
                console.error(`[PowerController] Power cycle failed: ${status.error}`);
            } else if (status.state === 'idle') {
                // Cycle finished but we missed the complete state
                clearInterval(pollInterval);
                $btn.removeClass('power-cycling power-error');
            }
            // Keep polling for 'powering_off', 'powering_on', 'cycling' states

        } catch (error) {
            console.error(`[PowerController] Status poll error:`, error);
            // Don't stop polling on network errors - might be temporary
        }
    }, 1000);

    // Stop polling after 60 seconds (safety timeout)
    setTimeout(() => {
        clearInterval(pollInterval);
        $btn.removeClass('power-cycling');
    }, 60000);
}

// Initialize on DOM ready
$(document).ready(() => {
    initPowerControls();
});

export { initPowerControls };
