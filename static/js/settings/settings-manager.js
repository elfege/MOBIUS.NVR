/**
 * SETTINGS MANAGER MODULE (ES6 + jQuery)
 * Main controller that orchestrates all settings functionality
 */

import { fullscreenHandler } from './fullscreen-handler.js';
import { settingsUI } from './settings-ui.js';

class SettingsManager {
    constructor() {
        this.$settingsBtn = null;
        this.initialized = false;
    }

    init() {
        // Prevent double initialization
        if (this.initialized) {
            console.log('[SettingsManager] Already initialized, skipping');
            return;
        }

        console.log('[SettingsManager] Initializing...');

        this.$settingsBtn = $('#settings-btn');

        if (this.$settingsBtn.length === 0) {
            console.error('[SettingsManager] Settings button not found in DOM!');
            return;
        }

        this.$settingsBtn.on('click', (e) => {
            e.preventDefault();
            console.log('[SettingsManager] Settings button clicked');
            settingsUI.toggle();
        });

        this.initialized = true;
        console.log('[SettingsManager] Initialization complete');
    }

    openSettings() {
        settingsUI.show();
    }

    closeSettings() {
        settingsUI.hide();
    }

    getAllSettings() {
        return {
            fullscreen: fullscreenHandler.getSettings()
        };
    }
}

// Create singleton instance
export const settingsManager = new SettingsManager();

// Initialize with jQuery
$(document).ready(() => {
    settingsManager.init();
});
