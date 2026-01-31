/**
 * Storage Status Module (ES6 + jQuery)
 * Displays storage tier usage with progress bars, migration controls,
 * editable settings, and real-time operation progress.
 */

export class StorageStatus {
    constructor() {
        this.updateInterval = null;
        this.progressInterval = null;
        this.isOperationInProgress = false;
        this.settings = null;
        this.isEditMode = false;
    }

    /**
     * Get color class based on usage percentage
     * @param {number} usedPercent - Usage percentage (0-100)
     * @returns {string} CSS class name
     */
    getColorClass(usedPercent) {
        if (usedPercent >= 80) return 'storage-critical';  // Red
        if (usedPercent >= 60) return 'storage-warning';   // Yellow
        return 'storage-ok';                                // Green
    }

    /**
     * Format bytes to human readable string
     * @param {number} bytes - Bytes value
     * @returns {string} Formatted string (e.g., "1.23 GB")
     */
    formatBytes(bytes) {
        if (bytes === 0) return '0 B';
        const units = ['B', 'KB', 'MB', 'GB', 'TB'];
        const i = Math.floor(Math.log(bytes) / Math.log(1024));
        return (bytes / Math.pow(1024, i)).toFixed(2) + ' ' + units[i];
    }

    /**
     * Fetch storage stats from API
     * @returns {Promise<object>} Storage statistics
     */
    async fetchStats() {
        try {
            const response = await fetch('/api/storage/stats');
            if (!response.ok) throw new Error('API error');
            const data = await response.json();
            if (!data.success) throw new Error(data.error || 'Unknown error');
            return data;
        } catch (error) {
            console.error('[StorageStatus] Failed to fetch stats:', error);
            return null;
        }
    }

    /**
     * Fetch migration settings from API
     * @returns {Promise<object>} Migration settings
     */
    async fetchSettings() {
        try {
            const response = await fetch('/api/storage/settings');
            if (!response.ok) throw new Error('API error');
            const data = await response.json();
            if (!data.success) throw new Error(data.error || 'Unknown error');
            this.settings = data.settings;
            return data.settings;
        } catch (error) {
            console.error('[StorageStatus] Failed to fetch settings:', error);
            return null;
        }
    }

    /**
     * Save migration settings to API
     * @param {object} settings - Settings to save
     * @returns {Promise<boolean>} Success status
     */
    async saveSettings(settings) {
        try {
            const response = await fetch('/api/storage/settings', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(settings)
            });
            const data = await response.json();
            if (!data.success) throw new Error(data.error || 'Failed to save');
            this.settings = data.settings;
            return true;
        } catch (error) {
            console.error('[StorageStatus] Failed to save settings:', error);
            alert(`Failed to save settings: ${error.message}`);
            return false;
        }
    }

    /**
     * Fetch current migration status for real-time updates
     * @returns {Promise<object>} Migration status
     */
    async fetchMigrationStatus() {
        try {
            const response = await fetch('/api/storage/migration-status');
            if (!response.ok) throw new Error('API error');
            const data = await response.json();
            return data;
        } catch (error) {
            console.error('[StorageStatus] Failed to fetch migration status:', error);
            return null;
        }
    }

    /**
     * Trigger a storage operation (migrate, cleanup, reconcile)
     * @param {string} operation - Operation type
     * @param {string} recordingType - Recording type (optional)
     * @returns {Promise<object>} Operation result
     */
    async triggerOperation(operation, recordingType = 'motion') {
        if (this.isOperationInProgress) {
            alert('Another operation is in progress');
            return null;
        }

        this.isOperationInProgress = true;
        this.updateButtonStates();
        this.showProgressIndicator(operation);
        this.startProgressPolling();

        try {
            let url, body;

            switch (operation) {
                case 'migrate':
                    url = '/api/storage/migrate';
                    body = { recording_type: recordingType };
                    break;
                case 'cleanup':
                    url = '/api/storage/cleanup';
                    body = { recording_type: recordingType };
                    break;
                case 'reconcile':
                    url = '/api/storage/reconcile';
                    body = {};
                    break;
                case 'full':
                    url = '/api/storage/migrate/full';
                    body = {};
                    break;
                default:
                    throw new Error(`Unknown operation: ${operation}`);
            }

            const response = await fetch(url, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(body)
            });

            const data = await response.json();

            if (data.success) {
                // Show success message
                this.showOperationResult(operation, data);
                // Refresh stats
                await this.refresh();
            } else {
                alert(`Operation failed: ${data.error}`);
            }

            return data;

        } catch (error) {
            console.error('[StorageStatus] Operation failed:', error);
            alert(`Operation failed: ${error.message}`);
            return null;
        } finally {
            this.isOperationInProgress = false;
            this.stopProgressPolling();
            this.hideProgressIndicator();
            this.updateButtonStates();
        }
    }

    /**
     * Start polling migration status for real-time updates
     */
    startProgressPolling() {
        if (this.progressInterval) {
            clearInterval(this.progressInterval);
        }

        this.progressInterval = setInterval(async () => {
            const status = await this.fetchMigrationStatus();
            if (status) {
                this.updateProgressDisplay(status);
            }
        }, 1000); // Poll every second
    }

    /**
     * Stop polling migration status
     */
    stopProgressPolling() {
        if (this.progressInterval) {
            clearInterval(this.progressInterval);
            this.progressInterval = null;
        }
    }

    /**
     * Show progress indicator during operation
     * @param {string} operation - Operation type
     */
    showProgressIndicator(operation) {
        const operationNames = {
            'migrate': 'Migrating files...',
            'cleanup': 'Cleaning up archive...',
            'reconcile': 'Reconciling database...',
            'full': 'Running full migration...'
        };

        const $indicator = $('#storage-progress-indicator');
        $indicator.find('.progress-operation').text(operationNames[operation] || 'Processing...');
        $indicator.find('.progress-files').text('0 files processed');
        $indicator.find('.progress-bytes').text('');
        $indicator.addClass('show');

        // Lock modal if applicable
        this.lockModal(true);
    }

    /**
     * Update progress display with current status
     * @param {object} status - Migration status from API
     */
    updateProgressDisplay(status) {
        const $indicator = $('#storage-progress-indicator');
        if (!$indicator.hasClass('show')) return;

        if (status.files_processed !== undefined) {
            const filesText = status.files_total > 0
                ? `${status.files_processed} / ${status.files_total} files`
                : `${status.files_processed} files processed`;
            $indicator.find('.progress-files').text(filesText);
        }

        if (status.bytes_processed > 0) {
            $indicator.find('.progress-bytes').text(this.formatBytes(status.bytes_processed));
        }

        if (status.current_file) {
            const filename = status.current_file.split('/').pop();
            $indicator.find('.progress-current').text(filename);
        }
    }

    /**
     * Hide progress indicator
     */
    hideProgressIndicator() {
        $('#storage-progress-indicator').removeClass('show');
        this.lockModal(false);
    }

    /**
     * Lock/unlock the modal during operations
     * @param {boolean} lock - Whether to lock the modal
     */
    lockModal(lock) {
        const $modal = $('.storage-status-container').closest('.modal, .settings-panel');
        if ($modal.length) {
            if (lock) {
                $modal.addClass('modal-locked');
                // Disable close buttons
                $modal.find('.close-btn, .modal-close, [data-dismiss="modal"]').prop('disabled', true);
            } else {
                $modal.removeClass('modal-locked');
                $modal.find('.close-btn, .modal-close, [data-dismiss="modal"]').prop('disabled', false);
            }
        }
    }

    /**
     * Show operation result in a user-friendly way
     */
    showOperationResult(operation, data) {
        let message;

        switch (operation) {
            case 'migrate':
                message = `Migration complete: ${data.migrated} files migrated, ${data.failed} failed`;
                break;
            case 'cleanup':
                message = `Cleanup complete: ${data.deleted} files deleted, ${this.formatBytes(data.bytes_freed)} freed`;
                break;
            case 'reconcile':
                message = `Reconciliation complete: ${data.orphaned_removed} orphaned entries removed`;
                break;
            case 'full':
                const totalMigrated = Object.values(data.migrate || {}).reduce((sum, r) => sum + r.migrated, 0);
                const totalDeleted = Object.values(data.cleanup || {}).reduce((sum, r) => sum + r.deleted, 0);
                message = `Full migration complete: ${totalMigrated} migrated, ${totalDeleted} deleted, ${data.reconcile?.orphaned_removed || 0} reconciled`;
                break;
        }

        // Update result display
        const $result = $('#storage-operation-result');
        $result.text(message).addClass('show');

        setTimeout(() => $result.removeClass('show'), 5000);
    }

    /**
     * Update button enabled/disabled states
     */
    updateButtonStates() {
        const $buttons = $('.storage-action-btn');
        if (this.isOperationInProgress) {
            $buttons.prop('disabled', true).addClass('disabled');
        } else {
            $buttons.prop('disabled', false).removeClass('disabled');
        }
    }

    /**
     * Toggle edit mode for settings
     */
    toggleEditMode() {
        this.isEditMode = !this.isEditMode;
        const $settings = $('.storage-settings-form');
        const $editBtn = $('.storage-edit-settings-btn');
        const $saveBtn = $('.storage-save-settings-btn');
        const $cancelBtn = $('.storage-cancel-settings-btn');

        if (this.isEditMode) {
            $settings.addClass('editing');
            $settings.find('input').prop('disabled', false);
            $editBtn.hide();
            $saveBtn.show();
            $cancelBtn.show();
        } else {
            $settings.removeClass('editing');
            $settings.find('input').prop('disabled', true);
            $editBtn.show();
            $saveBtn.hide();
            $cancelBtn.hide();
        }
    }

    /**
     * Render the storage status HTML
     * @param {object} stats - Storage statistics from API
     * @returns {string} HTML string
     */
    render(stats) {
        if (!stats) {
            return `
                <div class="storage-status-error">
                    <i class="fas fa-exclamation-triangle"></i>
                    Unable to load storage statistics
                </div>
            `;
        }

        const recent = stats.recent || {};
        const archive = stats.archive || {};
        const config = stats.config || {};
        const warnings = stats.warnings || [];

        const recentUsed = recent.used_percent || 0;
        const archiveUsed = archive.used_percent || 0;

        // Use fetched settings or config defaults
        const settings = this.settings || config;

        return `
            <div class="storage-status-container">
                <!-- Progress Indicator (hidden by default) -->
                <div id="storage-progress-indicator" class="storage-progress-indicator">
                    <div class="progress-spinner"></div>
                    <div class="progress-text">
                        <span class="progress-operation">Processing...</span>
                        <span class="progress-files">0 files</span>
                        <span class="progress-bytes"></span>
                        <span class="progress-current"></span>
                    </div>
                </div>

                <!-- Recent Storage -->
                <div class="storage-tier">
                    <div class="storage-tier-header">
                        <span class="storage-tier-name">
                            <i class="fas fa-bolt"></i> Recent Storage
                        </span>
                        <span class="storage-tier-path">${recent.host_path || '/mnt/sdc/NVR_Recent'}</span>
                    </div>
                    <div class="storage-progress-container">
                        <div class="storage-progress-bar ${this.getColorClass(recentUsed)}"
                             style="width: ${recentUsed}%"></div>
                    </div>
                    <div class="storage-tier-stats">
                        <span>${recentUsed.toFixed(1)}% used</span>
                        <span>${recent.used_gb || 0} GB / ${recent.total_gb || 0} GB</span>
                        <span>${recent.recording_count || 0} recordings</span>
                    </div>
                </div>

                <!-- Archive Storage -->
                <div class="storage-tier">
                    <div class="storage-tier-header">
                        <span class="storage-tier-name">
                            <i class="fas fa-archive"></i> Archive Storage
                        </span>
                        <span class="storage-tier-path">${archive.host_path || '/mnt/THE_BIG_DRIVE/NVR_RECORDINGS'}</span>
                    </div>
                    <div class="storage-progress-container">
                        <div class="storage-progress-bar ${this.getColorClass(archiveUsed)}"
                             style="width: ${archiveUsed}%"></div>
                    </div>
                    <div class="storage-tier-stats">
                        <span>${archiveUsed.toFixed(1)}% used</span>
                        <span>${archive.used_gb || 0} GB / ${archive.total_gb || 0} GB</span>
                        <span>${archive.recording_count || 0} recordings</span>
                    </div>
                </div>

                <!-- Warnings -->
                ${warnings.length > 0 ? `
                    <div class="storage-warnings">
                        ${warnings.map(w => `
                            <div class="storage-warning">
                                <i class="fas fa-exclamation-circle"></i> ${w}
                            </div>
                        `).join('')}
                    </div>
                ` : ''}

                <!-- Editable Migration Settings -->
                <div class="storage-settings-section">
                    <div class="storage-settings-header">
                        <span><i class="fas fa-cog"></i> Migration Settings</span>
                        <button class="storage-edit-settings-btn" title="Edit settings">
                            <i class="fas fa-edit"></i> Edit
                        </button>
                        <button class="storage-save-settings-btn" style="display:none" title="Save settings">
                            <i class="fas fa-save"></i> Save
                        </button>
                        <button class="storage-cancel-settings-btn" style="display:none" title="Cancel">
                            <i class="fas fa-times"></i> Cancel
                        </button>
                    </div>
                    <form class="storage-settings-form">
                        <div class="settings-row">
                            <label title="Files older than this migrate to archive">
                                <i class="fas fa-clock"></i> Migrate after
                                <input type="number" name="age_threshold_days"
                                       value="${settings.age_threshold_days || 3}"
                                       min="1" max="365" disabled> days
                            </label>
                            <label title="Files older than this are deleted from archive">
                                <i class="fas fa-trash"></i> Delete after
                                <input type="number" name="archive_retention_days"
                                       value="${settings.archive_retention_days || 90}"
                                       min="1" max="3650" disabled> days
                            </label>
                        </div>
                        <div class="settings-row">
                            <label title="Migration triggered when free space drops below this">
                                <i class="fas fa-hdd"></i> Min free
                                <input type="number" name="min_free_space_percent"
                                       value="${settings.min_free_space_percent || 20}"
                                       min="5" max="50" disabled> %
                            </label>
                        </div>
                        <div class="settings-row settings-size-limits">
                            <label title="Max storage size for Recent (0 = unlimited)">
                                <i class="fas fa-database"></i> Max Recent
                                <input type="number" name="max_recent_storage_mb"
                                       value="${settings.max_recent_storage_mb || 0}"
                                       min="0" step="1000" disabled> MB
                            </label>
                            <label title="Max storage size for Archive (0 = unlimited)">
                                <i class="fas fa-archive"></i> Max Archive
                                <input type="number" name="max_archive_storage_mb"
                                       value="${settings.max_archive_storage_mb || 0}"
                                       min="0" step="1000" disabled> MB
                            </label>
                        </div>
                        <div class="settings-hint">
                            <i class="fas fa-info-circle"></i>
                            Set max storage to 0 for unlimited (uses % threshold only)
                        </div>
                    </form>
                </div>

                <!-- Action Buttons -->
                <div class="storage-actions">
                    <button class="storage-action-btn storage-action-migrate"
                            data-operation="migrate"
                            title="Move old files from Recent to Archive">
                        <i class="fas fa-arrow-right"></i> Migrate Now
                    </button>
                    <button class="storage-action-btn storage-action-cleanup"
                            data-operation="cleanup"
                            title="Delete old files from Archive">
                        <i class="fas fa-trash-alt"></i> Cleanup Archive
                    </button>
                    <button class="storage-action-btn storage-action-reconcile"
                            data-operation="reconcile"
                            title="Remove orphaned database entries">
                        <i class="fas fa-sync"></i> Reconcile DB
                    </button>
                </div>

                <!-- Operation Result Display -->
                <div id="storage-operation-result" class="storage-operation-result"></div>
            </div>
        `;
    }

    /**
     * Initialize the component in a container element
     * @param {string|jQuery} container - Container selector or jQuery element
     */
    async init(container) {
        const $container = $(container);
        if (!$container.length) {
            console.error('[StorageStatus] Container not found:', container);
            return;
        }

        // Show loading state
        $container.html('<div class="storage-loading"><i class="fas fa-spinner fa-spin"></i> Loading storage status...</div>');

        // Fetch settings first
        await this.fetchSettings();

        // Fetch and render
        const stats = await this.fetchStats();
        $container.html(this.render(stats));

        // Setup event handlers for action buttons
        $container.on('click', '.storage-action-btn', async (e) => {
            const operation = $(e.currentTarget).data('operation');
            await this.triggerOperation(operation);
        });

        // Setup event handlers for settings edit
        $container.on('click', '.storage-edit-settings-btn', () => {
            this.toggleEditMode();
        });

        $container.on('click', '.storage-cancel-settings-btn', async () => {
            this.isEditMode = false;
            // Re-render to reset values
            const stats = await this.fetchStats();
            $container.html(this.render(stats));
            this.setupEventHandlers($container);
        });

        $container.on('click', '.storage-save-settings-btn', async () => {
            const $form = $('.storage-settings-form');
            const newSettings = {
                age_threshold_days: parseInt($form.find('[name="age_threshold_days"]').val()) || 3,
                archive_retention_days: parseInt($form.find('[name="archive_retention_days"]').val()) || 90,
                min_free_space_percent: parseInt($form.find('[name="min_free_space_percent"]').val()) || 20,
                max_recent_storage_mb: parseInt($form.find('[name="max_recent_storage_mb"]').val()) || 0,
                max_archive_storage_mb: parseInt($form.find('[name="max_archive_storage_mb"]').val()) || 0
            };

            const success = await this.saveSettings(newSettings);
            if (success) {
                this.isEditMode = false;
                // Re-render with new settings
                const stats = await this.fetchStats();
                $container.html(this.render(stats));
                this.setupEventHandlers($container);

                // Show success message
                const $result = $('#storage-operation-result');
                $result.text('Settings saved successfully').addClass('show');
                setTimeout(() => $result.removeClass('show'), 3000);
            }
        });

        // Auto-refresh every 30 seconds (unless operation in progress)
        this.startAutoRefresh($container);
    }

    /**
     * Setup event handlers (called after re-render)
     */
    setupEventHandlers($container) {
        // Handlers are delegated to container, so no re-setup needed
    }

    /**
     * Refresh the storage status display
     */
    async refresh() {
        const $container = $('.storage-status-container').parent();
        if ($container.length) {
            await this.fetchSettings();
            const stats = await this.fetchStats();
            $container.html(this.render(stats));
        }
    }

    /**
     * Start auto-refresh timer
     */
    startAutoRefresh(container) {
        if (this.updateInterval) {
            clearInterval(this.updateInterval);
        }

        this.updateInterval = setInterval(async () => {
            if (!this.isOperationInProgress && !this.isEditMode) {
                const stats = await this.fetchStats();
                if (stats) {
                    $(container).html(this.render(stats));
                }
            }
        }, 30000); // 30 seconds
    }

    /**
     * Stop auto-refresh timer
     */
    stopAutoRefresh() {
        if (this.updateInterval) {
            clearInterval(this.updateInterval);
            this.updateInterval = null;
        }
    }
}

// Create and export singleton instance
export const storageStatus = new StorageStatus();
