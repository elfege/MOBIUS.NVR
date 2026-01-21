/**
 * Timeline Playback Modal
 * Location: ~/0_NVR/static/js/modals/timeline-playback-modal.js
 * Version: 2026-01-20-v2 (debug)
 *
 * Provides timeline visualization of recordings with:
 * - Drag-select time range for export
 * - Zoom in/out for granular selection
 * - Export to MP4 with iOS compatibility option
 * - Progress tracking for long exports
 */

console.log('[Timeline] JS file loaded - version 2026-01-20-v2');

export class TimelinePlaybackModal {
    constructor() {
        // DOM elements
        this.$modal = null;
        this.$canvas = null;
        this.ctx = null;

        // State
        this.currentCameraId = null;
        this.currentCameraName = null;
        this.segments = [];
        this.timeRange = { start: null, end: null };
        this.selection = { start: null, end: null };
        this.zoomLevel = 1;
        this.panOffset = 0;

        // Export state
        this.currentExportJobId = null;
        this.exportPollInterval = null;

        // Preview state (segment-by-segment - legacy)
        this.selectedSegments = [];
        this.currentPreviewIndex = 0;
        this.isPlayingAll = false;

        // Merged preview state
        this.currentPreviewMergeJobId = null;
        this.previewMergePollingInterval = null;
        this.mergedPreviewReady = false;
        this._pendingAutoPlay = false;

        // Device detection for iOS/mobile compatibility
        this.isIOS = /iPad|iPhone|iPod/.test(navigator.userAgent) && !window.MSStream;
        this.isMobile = /Android|webOS|iPhone|iPad|iPod|BlackBerry|IEMobile|Opera Mini/i.test(navigator.userAgent);

        // Canvas interaction state
        this.isDragging = false;
        this.dragStart = null;

        // Colors for timeline visualization
        this.colors = {
            background: '#1a1a2e',
            gridLine: '#2d2d44',
            motionSegment: '#4CAF50',
            continuousSegment: '#2196F3',
            manualSegment: '#FF9800',
            selection: 'rgba(255, 255, 255, 0.3)',
            selectionBorder: '#ffffff',
            timeLabel: '#888888',
            noRecording: '#333344'
        };

        this.init();
    }

    /**
     * Initialize modal
     */
    init() {
        this.$modal = $('#timeline-playback-modal');

        if (!this.$modal.length) {
            console.warn('[Timeline] Modal not found in DOM - will retry on button click');
            return;
        }

        this.$canvas = $('#timeline-canvas');
        if (this.$canvas.length) {
            this.ctx = this.$canvas[0].getContext('2d');
        }

        this.attachModalEvents();
        this.attachButtonEvents();
        this.attachCanvasEvents();

        // Set default date to today
        const today = new Date().toISOString().split('T')[0];
        $('#timeline-date').val(today);

        console.log('[Timeline] Modal initialized');
    }

    /**
     * Attach modal control events
     */
    attachModalEvents() {
        // Close button
        this.$modal.find('.timeline-modal-close').on('click', () => this.hide());

        // Click outside modal to close
        this.$modal.on('click', (e) => {
            if ($(e.target).hasClass('timeline-modal')) {
                this.hide();
            }
        });

        // Escape key to close
        $(document).on('keydown', (e) => {
            if (e.key === 'Escape' && this.$modal.is(':visible')) {
                this.hide();
            }
        });

        // Load timeline button
        $('#timeline-load-btn').on('click', () => this.loadTimeline());

        // Date presets
        $('.timeline-preset-btn').on('click', (e) => {
            const hours = parseInt($(e.target).data('hours'));
            this.setPresetRange(hours);
        });

        // Zoom controls
        $('.timeline-zoom-btn').on('click', (e) => {
            const action = $(e.target).closest('button').data('zoom');
            this.handleZoom(action);
        });

        $('#timeline-zoom-slider').on('input', (e) => {
            this.zoomLevel = parseInt(e.target.value);
            this.renderTimeline();
        });

        // Export button
        $('#timeline-export-btn').on('click', () => this.startExport());

        // Cancel export button
        $('#timeline-cancel-export-btn').on('click', () => this.cancelExport());

        // Download button
        $('#timeline-download-btn').on('click', () => this.downloadExport());

        // Preview controls
        $('.timeline-preview-close').on('click', () => this.hidePreview());
        $('#preview-prev-btn').on('click', () => this.previewPrevious());
        $('#preview-next-btn').on('click', () => this.previewNext());
        $('#preview-play-all-btn').on('click', () => this.playAllSelected());

        // Cancel preview merge button
        $('.cancel-preview-merge-btn').on('click', () => this.cancelCurrentPreviewMerge());

        // Video event listeners - attach to handle playback
        this.attachVideoEventListeners();
    }

    /**
     * Attach video element event listeners
     * Separated into own method so it can be called after modal is in DOM
     */
    attachVideoEventListeners() {
        const video = document.getElementById('timeline-preview-video');
        if (!video) {
            console.warn('[Timeline] Video element not found - will retry when modal opens');
            return;
        }

        // Remove existing listeners to avoid duplicates
        video.removeEventListener('ended', this._boundOnPreviewEnded);
        video.removeEventListener('canplay', this._boundOnVideoCanPlay);

        // Create bound versions of handlers
        this._boundOnPreviewEnded = () => this.onPreviewEnded();
        this._boundOnVideoCanPlay = () => this.onVideoCanPlay();

        // Video ended event - auto-advance when playing all
        video.addEventListener('ended', this._boundOnPreviewEnded);

        // Video ready event - start playback when in play-all mode
        video.addEventListener('canplay', this._boundOnVideoCanPlay);

        console.log('[Timeline] Video event listeners attached');
    }

    /**
     * Attach click events to camera playback buttons
     */
    attachButtonEvents() {
        $(document).on('click', '.camera-playback-btn', async (e) => {
            e.preventDefault();
            e.stopPropagation();

            const $button = $(e.currentTarget);
            const cameraId = $button.data('camera-id');
            const $streamItem = $button.closest('.stream-item');
            const cameraName = $streamItem.data('camera-name') || cameraId;

            this.show(cameraId, cameraName);
        });
    }

    /**
     * Attach canvas interaction events for drag selection
     */
    attachCanvasEvents() {
        if (!this.$canvas.length) return;

        const canvas = this.$canvas[0];

        // Mouse down - start drag
        canvas.addEventListener('mousedown', (e) => {
            const rect = canvas.getBoundingClientRect();
            const x = e.clientX - rect.left;

            this.isDragging = true;
            this.dragStart = x;
            this.selection.start = this.xToTime(x);
            this.selection.end = null;
        });

        // Mouse move - update selection
        canvas.addEventListener('mousemove', (e) => {
            if (!this.isDragging) return;

            const rect = canvas.getBoundingClientRect();
            const x = e.clientX - rect.left;
            this.selection.end = this.xToTime(x);
            this.renderTimeline();
            this.updateSelectionInfo();
        });

        // Mouse up - finish drag
        canvas.addEventListener('mouseup', () => {
            if (this.isDragging) {
                this.isDragging = false;
                this.finalizeSelection();
            }
        });

        // Mouse leave - cancel drag if outside
        canvas.addEventListener('mouseleave', () => {
            if (this.isDragging) {
                this.isDragging = false;
            }
        });

        // Touch events for mobile
        canvas.addEventListener('touchstart', (e) => {
            const rect = canvas.getBoundingClientRect();
            const touch = e.touches[0];
            const x = touch.clientX - rect.left;

            this.isDragging = true;
            this.dragStart = x;
            this.selection.start = this.xToTime(x);
            this.selection.end = null;
        });

        canvas.addEventListener('touchmove', (e) => {
            if (!this.isDragging) return;
            e.preventDefault();

            const rect = canvas.getBoundingClientRect();
            const touch = e.touches[0];
            const x = touch.clientX - rect.left;
            this.selection.end = this.xToTime(x);
            this.renderTimeline();
            this.updateSelectionInfo();
        });

        canvas.addEventListener('touchend', () => {
            if (this.isDragging) {
                this.isDragging = false;
                this.finalizeSelection();
            }
        });
    }

    /**
     * Show modal for a camera
     */
    show(cameraId, cameraName) {
        this.currentCameraId = cameraId;
        this.currentCameraName = cameraName;

        // Update header
        $('#timeline-camera-name').text(cameraName);

        // Reset state
        this.segments = [];
        this.selection = { start: null, end: null };
        this.zoomLevel = 1;
        this.panOffset = 0;
        this.currentExportJobId = null;
        this.selectedSegments = [];
        this.currentPreviewIndex = 0;
        this.isPlayingAll = false;
        this._pendingAutoPlay = false;

        // Reset merged preview state
        this.currentPreviewMergeJobId = null;
        this.mergedPreviewReady = false;
        if (this.previewMergePollingInterval) {
            clearInterval(this.previewMergePollingInterval);
            this.previewMergePollingInterval = null;
        }

        // Reset UI
        this.showSection('empty', false);
        this.showSection('loading', false);
        this.showSection('canvas', false);
        this.showSection('zoom', false);
        this.showSection('export', false);
        this.showSection('progress', false);
        this.showSection('download', false);
        this.showSection('preview', false);
        this.showSection('previewMerge', false);
        $('#timeline-export-btn').prop('disabled', true);

        // Show modal
        this.$modal.show();

        // Ensure video event listeners are attached (may not have been if modal wasn't in DOM during init)
        this.attachVideoEventListeners();

        // Auto-load last 24 hours
        this.setPresetRange(24);
    }

    /**
     * Hide modal
     */
    async hide() {
        this.$modal.hide();

        // Cancel any ongoing export
        if (this.currentExportJobId) {
            this.cancelExport();
        }

        // Clear export poll interval
        if (this.exportPollInterval) {
            clearInterval(this.exportPollInterval);
            this.exportPollInterval = null;
        }

        // Cleanup preview merge (cancel if running, delete temp files)
        await this.cleanupPreviewMerge();
    }

    /**
     * Set time range from preset (last N hours)
     */
    setPresetRange(hours) {
        const now = new Date();
        const start = new Date(now.getTime() - hours * 60 * 60 * 1000);

        // Update date input
        $('#timeline-date').val(start.toISOString().split('T')[0]);

        // Update time inputs
        $('#timeline-start-time').val(start.toTimeString().slice(0, 5));
        $('#timeline-end-time').val(now.toTimeString().slice(0, 5));

        // Load timeline
        this.loadTimeline();
    }

    /**
     * Load timeline data from API
     */
    async loadTimeline() {
        const dateStr = $('#timeline-date').val();
        const startTime = $('#timeline-start-time').val();
        const endTime = $('#timeline-end-time').val();

        if (!dateStr) {
            alert('Please select a date');
            return;
        }

        // Build ISO timestamps
        const startDate = new Date(`${dateStr}T${startTime}:00`);
        let endDate = new Date(`${dateStr}T${endTime}:00`);

        // If end is before start, assume it's the next day
        if (endDate <= startDate) {
            endDate.setDate(endDate.getDate() + 1);
        }

        this.timeRange = {
            start: startDate,
            end: endDate
        };

        // Show loading
        this.showSection('loading', true);
        this.showSection('canvas', false);
        this.showSection('empty', false);

        try {
            const response = await fetch(
                `/api/timeline/segments/${this.currentCameraId}?` +
                `start=${startDate.toISOString()}&end=${endDate.toISOString()}`
            );

            const data = await response.json();

            if (!data.success) {
                throw new Error(data.error || 'Failed to load timeline');
            }

            this.segments = data.segments.map(seg => ({
                ...seg,
                start_time: new Date(seg.start_time),
                end_time: new Date(seg.end_time)
            }));

            this.showSection('loading', false);

            if (this.segments.length === 0) {
                this.showSection('empty', true);
            } else {
                this.showSection('canvas', true);
                this.showSection('zoom', true);
                this.showSection('export', true);
                this.setupCanvas();
                this.renderTimeline();
            }

        } catch (error) {
            console.error('[Timeline] Load error:', error);
            this.showSection('loading', false);
            this.showSection('empty', true);
            $('.timeline-empty').html(
                `<i class="fas fa-exclamation-triangle"></i> Error: ${error.message}`
            );
        }
    }

    /**
     * Setup canvas dimensions
     */
    setupCanvas() {
        const wrapper = this.$canvas.parent();
        const canvas = this.$canvas[0];

        // Set canvas size to match container
        canvas.width = wrapper.width() || 800;
        canvas.height = 150;

        this.ctx = canvas.getContext('2d');
    }

    /**
     * Render timeline visualization
     */
    renderTimeline() {
        if (!this.ctx || !this.timeRange.start) return;

        const canvas = this.$canvas[0];
        const width = canvas.width;
        const height = canvas.height;

        // Clear canvas
        this.ctx.fillStyle = this.colors.background;
        this.ctx.fillRect(0, 0, width, height);

        // Calculate visible time range based on zoom/pan
        const totalMs = this.timeRange.end - this.timeRange.start;
        const visibleMs = totalMs / this.zoomLevel;
        const visibleStart = new Date(this.timeRange.start.getTime() + this.panOffset);
        const visibleEnd = new Date(visibleStart.getTime() + visibleMs);

        // Draw time grid
        this.drawTimeGrid(width, height, visibleStart, visibleEnd);

        // Draw segments
        const segmentHeight = 60;
        const segmentY = 40;

        for (const seg of this.segments) {
            // Check if segment is visible
            if (seg.end_time < visibleStart || seg.start_time > visibleEnd) continue;

            const startX = this.timeToX(seg.start_time, visibleStart, visibleEnd, width);
            const endX = this.timeToX(seg.end_time, visibleStart, visibleEnd, width);
            const segWidth = Math.max(2, endX - startX);

            // Choose color based on recording type
            let color = this.colors.continuousSegment;
            if (seg.recording_type === 'motion') color = this.colors.motionSegment;
            else if (seg.recording_type === 'manual') color = this.colors.manualSegment;

            this.ctx.fillStyle = color;
            this.ctx.fillRect(startX, segmentY, segWidth, segmentHeight);

            // Add subtle border
            this.ctx.strokeStyle = 'rgba(255,255,255,0.2)';
            this.ctx.strokeRect(startX, segmentY, segWidth, segmentHeight);
        }

        // Draw selection overlay
        if (this.selection.start && this.selection.end) {
            const selStart = this.selection.start < this.selection.end ?
                this.selection.start : this.selection.end;
            const selEnd = this.selection.start < this.selection.end ?
                this.selection.end : this.selection.start;

            const startX = this.timeToX(selStart, visibleStart, visibleEnd, width);
            const endX = this.timeToX(selEnd, visibleStart, visibleEnd, width);

            this.ctx.fillStyle = this.colors.selection;
            this.ctx.fillRect(startX, 0, endX - startX, height);

            this.ctx.strokeStyle = this.colors.selectionBorder;
            this.ctx.lineWidth = 2;
            this.ctx.strokeRect(startX, 0, endX - startX, height);
        }

        // Draw legend
        this.drawLegend(width, height);
    }

    /**
     * Draw time grid lines and labels
     */
    drawTimeGrid(width, height, visibleStart, visibleEnd) {
        const visibleMs = visibleEnd - visibleStart;

        // Determine grid interval based on visible range
        let intervalMs;
        if (visibleMs > 24 * 60 * 60 * 1000) {
            intervalMs = 6 * 60 * 60 * 1000;  // 6 hours
        } else if (visibleMs > 6 * 60 * 60 * 1000) {
            intervalMs = 60 * 60 * 1000;  // 1 hour
        } else if (visibleMs > 60 * 60 * 1000) {
            intervalMs = 15 * 60 * 1000;  // 15 minutes
        } else {
            intervalMs = 5 * 60 * 1000;  // 5 minutes
        }

        // Find first grid line
        const firstGrid = new Date(
            Math.ceil(visibleStart.getTime() / intervalMs) * intervalMs
        );

        this.ctx.strokeStyle = this.colors.gridLine;
        this.ctx.fillStyle = this.colors.timeLabel;
        this.ctx.font = '10px monospace';
        this.ctx.textAlign = 'center';

        let current = firstGrid;
        while (current <= visibleEnd) {
            const x = this.timeToX(current, visibleStart, visibleEnd, width);

            // Draw vertical line
            this.ctx.beginPath();
            this.ctx.moveTo(x, 0);
            this.ctx.lineTo(x, height);
            this.ctx.stroke();

            // Draw time label
            const label = current.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
            this.ctx.fillText(label, x, height - 5);

            current = new Date(current.getTime() + intervalMs);
        }
    }

    /**
     * Draw legend
     */
    drawLegend(width, height) {
        const legendY = 15;
        const items = [
            { color: this.colors.motionSegment, label: 'Motion' },
            { color: this.colors.continuousSegment, label: 'Continuous' },
            { color: this.colors.manualSegment, label: 'Manual' }
        ];

        this.ctx.font = '10px sans-serif';
        let x = 10;

        for (const item of items) {
            // Draw color box
            this.ctx.fillStyle = item.color;
            this.ctx.fillRect(x, legendY - 8, 12, 12);

            // Draw label
            this.ctx.fillStyle = '#cccccc';
            this.ctx.textAlign = 'left';
            this.ctx.fillText(item.label, x + 16, legendY + 2);

            x += 80;
        }
    }

    /**
     * Convert time to X coordinate
     */
    timeToX(time, visibleStart, visibleEnd, width) {
        const t = time instanceof Date ? time.getTime() : time;
        const start = visibleStart.getTime();
        const end = visibleEnd.getTime();
        return ((t - start) / (end - start)) * width;
    }

    /**
     * Convert X coordinate to time
     */
    xToTime(x) {
        const canvas = this.$canvas[0];
        const width = canvas.width;

        const totalMs = this.timeRange.end - this.timeRange.start;
        const visibleMs = totalMs / this.zoomLevel;
        const visibleStart = new Date(this.timeRange.start.getTime() + this.panOffset);

        const ratio = x / width;
        return new Date(visibleStart.getTime() + ratio * visibleMs);
    }

    /**
     * Finalize selection after drag
     */
    finalizeSelection() {
        if (!this.selection.start || !this.selection.end) {
            return;
        }

        // Ensure start < end
        if (this.selection.start > this.selection.end) {
            [this.selection.start, this.selection.end] = [this.selection.end, this.selection.start];
        }

        // Find segments that overlap with selection
        this.selectedSegments = this.segments.filter(seg =>
            seg.end_time > this.selection.start && seg.start_time < this.selection.end
        );

        // Update export info
        this.updateExportInfo(this.selectedSegments);

        // Enable export button if segments selected
        $('#timeline-export-btn').prop('disabled', this.selectedSegments.length === 0);

        // Show preview if segments selected
        // Note: showPreview is async - catch errors to prevent silent failures
        if (this.selectedSegments.length > 0) {
            this.showPreview().catch(err => {
                console.error('[Timeline] showPreview error:', err);
            });
        }
    }

    /**
     * Update selection info display
     */
    updateSelectionInfo() {
        if (!this.selection.start || !this.selection.end) {
            $('#selection-start').text('--');
            $('#selection-end').text('--');
            $('#selection-duration').text('--');
            return;
        }

        const start = this.selection.start < this.selection.end ?
            this.selection.start : this.selection.end;
        const end = this.selection.start < this.selection.end ?
            this.selection.end : this.selection.start;

        $('#selection-start').text(start.toLocaleTimeString());
        $('#selection-end').text(end.toLocaleTimeString());

        const durationMs = end - start;
        const minutes = Math.floor(durationMs / 60000);
        const seconds = Math.floor((durationMs % 60000) / 1000);
        $('#selection-duration').text(`${minutes}m ${seconds}s`);
    }

    /**
     * Update export summary info
     */
    updateExportInfo(selectedSegments) {
        const totalDuration = selectedSegments.reduce((sum, seg) => sum + seg.duration_seconds, 0);
        const totalSize = selectedSegments.reduce((sum, seg) => sum + seg.file_size_bytes, 0);

        $('#export-segment-count').text(`${selectedSegments.length} segments`);

        const minutes = Math.floor(totalDuration / 60);
        const seconds = totalDuration % 60;
        $('#export-duration').text(`${minutes}:${seconds.toString().padStart(2, '0')}`);

        const sizeMB = (totalSize / (1024 * 1024)).toFixed(1);
        $('#export-size').text(`~${sizeMB} MB`);
    }

    /**
     * Handle zoom actions
     */
    handleZoom(action) {
        const slider = $('#timeline-zoom-slider');
        let level = parseInt(slider.val());

        switch (action) {
            case 'in':
                level = Math.min(10, level + 1);
                break;
            case 'out':
                level = Math.max(1, level - 1);
                break;
            case 'fit':
                level = 1;
                this.panOffset = 0;
                break;
        }

        slider.val(level);
        this.zoomLevel = level;
        this.renderTimeline();
    }

    /**
     * Start export process
     * If merged preview is ready, promotes it instead of re-merging
     */
    async startExport() {
        if (!this.selection.start || !this.selection.end) {
            alert('Please select a time range first');
            return;
        }

        const iosCompatible = $('#export-ios-compatible').is(':checked');

        // If merged preview is ready, promote it instead of re-merging
        if (this.mergedPreviewReady && this.currentPreviewMergeJobId) {
            await this.promotePreviewToExport(iosCompatible);
            return;
        }

        // Fall back to original export flow
        const start = this.selection.start < this.selection.end ?
            this.selection.start : this.selection.end;
        const end = this.selection.start < this.selection.end ?
            this.selection.end : this.selection.start;

        this.showSection('export', false);
        this.showSection('progress', true);
        this.updateExportProgress(0, 'Creating export job...');

        try {
            // Create export job
            const response = await fetch('/api/timeline/export', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    camera_id: this.currentCameraId,
                    start: start.toISOString(),
                    end: end.toISOString(),
                    ios_compatible: iosCompatible,
                    auto_start: true
                })
            });

            const data = await response.json();

            if (!data.success) {
                throw new Error(data.error || 'Failed to create export');
            }

            this.currentExportJobId = data.job.job_id;
            console.log('[Timeline] Export job created:', this.currentExportJobId);

            // Start polling for progress
            this.startProgressPolling();

        } catch (error) {
            console.error('[Timeline] Export error:', error);
            this.updateExportProgress(0, `Error: ${error.message}`);
            setTimeout(() => {
                this.showSection('progress', false);
                this.showSection('export', true);
            }, 3000);
        }
    }

    /**
     * Promote merged preview to permanent export
     * Reuses the already-merged temp file instead of re-merging
     */
    async promotePreviewToExport(iosCompatible) {
        this.showSection('export', false);
        this.showSection('progress', true);

        if (iosCompatible) {
            this.updateExportProgress(0, 'Converting for iOS...');
        } else {
            this.updateExportProgress(50, 'Preparing download...');
        }

        try {
            const response = await fetch(
                `/api/timeline/preview-merge/${this.currentPreviewMergeJobId}/promote`,
                {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ ios_compatible: iosCompatible })
                }
            );

            const data = await response.json();
            if (!data.success) throw new Error(data.error);

            console.log(`[Timeline] Preview promoted to export: ${data.filename}`);

            // Trigger download
            this.updateExportProgress(100, 'Complete!');

            // Store for download
            this.completedJob = {
                output_path: data.export_path,
                download_url: data.download_url,
                filename: data.filename
            };

            // Show download section
            setTimeout(() => {
                this.showSection('progress', false);
                this.showSection('download', true);
            }, 500);

            // Clear preview state since file was moved
            this.currentPreviewMergeJobId = null;
            this.mergedPreviewReady = false;

        } catch (error) {
            console.error('[Timeline] Export promotion failed:', error);
            this.updateExportProgress(0, `Error: ${error.message}`);

            // Fall back to full export after delay
            setTimeout(() => {
                this.showSection('progress', false);
                this.showSection('export', true);
            }, 3000);
        }
    }

    /**
     * Start polling for export progress
     */
    startProgressPolling() {
        this.exportPollInterval = setInterval(async () => {
            try {
                const response = await fetch(`/api/timeline/export/${this.currentExportJobId}`);
                const data = await response.json();

                if (!data.success) {
                    throw new Error(data.error);
                }

                const job = data.job;
                this.updateExportProgress(job.progress_percent, this.getStatusText(job.status));

                if (job.status === 'completed') {
                    this.exportComplete(job);
                } else if (job.status === 'failed') {
                    throw new Error(job.error_message || 'Export failed');
                } else if (job.status === 'cancelled') {
                    this.showSection('progress', false);
                    this.showSection('export', true);
                    clearInterval(this.exportPollInterval);
                }

            } catch (error) {
                console.error('[Timeline] Progress poll error:', error);
                clearInterval(this.exportPollInterval);
                this.updateExportProgress(0, `Error: ${error.message}`);
            }
        }, 1000);
    }

    /**
     * Get human-readable status text
     */
    getStatusText(status) {
        const statusMap = {
            'pending': 'Preparing...',
            'processing': 'Processing...',
            'merging': 'Merging segments...',
            'converting': 'Converting for iOS...',
            'completed': 'Complete!',
            'failed': 'Failed',
            'cancelled': 'Cancelled'
        };
        return statusMap[status] || status;
    }

    /**
     * Update export progress UI
     */
    updateExportProgress(percent, statusText) {
        $('.export-progress-fill').css('width', `${percent}%`);
        $('.export-percent').text(`${Math.round(percent)}%`);
        $('.export-status').text(statusText);
    }

    /**
     * Handle export completion
     */
    exportComplete(job) {
        clearInterval(this.exportPollInterval);
        this.exportPollInterval = null;

        this.showSection('progress', false);
        this.showSection('download', true);

        // Store job for download
        this.completedJob = job;
    }

    /**
     * Cancel ongoing export
     */
    async cancelExport() {
        if (!this.currentExportJobId) return;

        try {
            await fetch(`/api/timeline/export/${this.currentExportJobId}/cancel`, {
                method: 'POST'
            });
        } catch (error) {
            console.error('[Timeline] Cancel error:', error);
        }

        if (this.exportPollInterval) {
            clearInterval(this.exportPollInterval);
            this.exportPollInterval = null;
        }

        this.currentExportJobId = null;
        this.showSection('progress', false);
        this.showSection('export', true);
    }

    /**
     * Download completed export
     * On iOS, opens video in new tab where user can use share sheet to save to Photos
     */
    downloadExport() {
        // Use completed job's download URL if available (from promote), otherwise use export job ID
        let downloadUrl;
        if (this.completedJob && this.completedJob.download_url) {
            downloadUrl = this.completedJob.download_url;
        } else if (this.currentExportJobId) {
            downloadUrl = `/api/timeline/export/${this.currentExportJobId}/download`;
        } else {
            console.error('[Timeline] No download URL available');
            return;
        }

        if (this.isIOS) {
            // On iOS, open video in new tab to trigger share sheet
            // User can then tap share icon and choose "Save to Photos"
            this.showIOSDownloadInstructions();
            window.open(downloadUrl, '_blank');
        } else if (this.isMobile) {
            // Android and other mobile - open in new tab for native handling
            window.open(downloadUrl, '_blank');
        } else {
            // On desktop, trigger direct download
            const a = document.createElement('a');
            a.href = downloadUrl;
            a.download = this.completedJob?.filename || ''; // Let server set filename if not known
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
        }

        // Reset UI after brief delay (longer for iOS to allow reading instructions)
        const resetDelay = this.isIOS ? 5000 : 2000;
        setTimeout(() => {
            this.showSection('download', false);
            this.showSection('export', true);
            this.currentExportJobId = null;
            this.completedJob = null;
        }, resetDelay);
    }

    /**
     * Show iOS-specific download instructions
     * Informs user how to save video to Photos app
     */
    showIOSDownloadInstructions() {
        // Update the download ready section to show iOS instructions
        const $downloadSection = this.$modal.find('.timeline-download-ready');
        $downloadSection.find('.ios-instructions').remove(); // Remove any existing

        const instructions = $(`
            <div class="ios-instructions">
                <p><strong>To save to Photos:</strong></p>
                <ol>
                    <li>Video will open in a new tab</li>
                    <li>Tap the <i class="fas fa-share-square"></i> Share button</li>
                    <li>Choose "Save to Photos"</li>
                </ol>
            </div>
        `);

        $downloadSection.append(instructions);
    }

    /**
     * Show/hide modal sections
     */
    showSection(section, show) {
        const sectionMap = {
            'loading': '.timeline-loading',
            'empty': '.timeline-empty',
            'canvas': '.timeline-canvas-wrapper',
            'zoom': '.timeline-zoom-controls',
            'export': '.timeline-export-controls',
            'progress': '.timeline-export-progress',
            'download': '.timeline-download-ready',
            'preview': '.timeline-preview-section',
            'previewMerge': '.timeline-preview-merge-progress'
        };

        const selector = sectionMap[section];
        if (selector) {
            this.$modal.find(selector).toggle(show);
        }
    }

    // =========================================================================
    // Merged Preview Methods
    // =========================================================================

    /**
     * Show the preview section and start merging segments
     * Creates a single merged MP4 for preview playback
     * On iOS/mobile, automatically uses iOS-compatible encoding (H.264 Baseline)
     */
    async showPreview() {
        console.log('[Timeline] showPreview() called, segments:', this.selectedSegments.length);

        try {
            if (this.selectedSegments.length === 0) {
                console.log('[Timeline] No segments to preview');
                return;
            }

            // Cancel any existing preview merge
            console.log('[Timeline] Cancelling any existing preview merge...');
            await this.cancelCurrentPreviewMerge();
            console.log('[Timeline] Cancel complete');

            // Show preview section with merge progress
            console.log('[Timeline] Showing preview section...');
            this.showSection('preview', true);
            this.showSection('previewMerge', true);
            console.log('[Timeline] Preview section visible, checking DOM...');
            console.log('[Timeline] Preview section display:', this.$modal.find('.timeline-preview-section').css('display'));

            // Show different message for iOS (re-encoding takes longer)
            const statusMessage = this.isMobile
                ? 'Preparing video for mobile playback...'
                : 'Starting merge...';
            this.updateMergeProgress(0, statusMessage);

            // Hide video and controls until merge completes
            $('#timeline-preview-video').hide();
            $('.timeline-preview-info').hide();
            $('.timeline-preview-controls').hide();

            // Extract recording IDs from selected segments
            const segmentIds = this.selectedSegments.map(s => s.recording_id);

            // Start merge job - use iOS-compatible encoding on mobile devices
            // This ensures the video plays correctly on iOS Safari and Android
            const response = await fetch('/api/timeline/preview-merge', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    camera_id: this.currentCameraId,
                    segment_ids: segmentIds,
                    ios_compatible: this.isMobile  // Auto-enable on mobile
                })
            });

            const data = await response.json();
            if (!data.success) throw new Error(data.error);

            this.currentPreviewMergeJobId = data.job_id;
            this.startPreviewMergePolling();

            const modeStr = this.isMobile ? '(iOS-compatible)' : '(stream copy)';
            console.log(`[Timeline] Preview merge started: ${data.job_id} (${segmentIds.length} segments) ${modeStr}`);

        } catch (error) {
            console.error('[Timeline] Preview merge/setup failed:', error);
            console.error('[Timeline] Error stack:', error.stack);
            this.showPreviewError(error.message);
        }
    }

    /**
     * Poll for preview merge job status
     */
    startPreviewMergePolling() {
        this.previewMergePollingInterval = setInterval(async () => {
            try {
                const response = await fetch(
                    `/api/timeline/preview-merge/${this.currentPreviewMergeJobId}`
                );
                const data = await response.json();

                if (!data.success) throw new Error(data.error);

                const job = data.job;
                this.updateMergeProgress(job.progress_percent, this.getMergeStatusText(job.status));

                if (job.status === 'completed') {
                    this.onPreviewMergeComplete(job);
                } else if (job.status === 'failed') {
                    throw new Error(job.error_message || 'Merge failed');
                } else if (job.status === 'cancelled') {
                    this.onPreviewMergeCancelled();
                }

            } catch (error) {
                clearInterval(this.previewMergePollingInterval);
                this.previewMergePollingInterval = null;
                this.showPreviewError(error.message);
            }
        }, 500);  // Poll every 500ms for responsive feedback
    }

    /**
     * Get human-readable merge status text
     * Shows different messages for mobile (re-encoding takes longer)
     */
    getMergeStatusText(status) {
        if (this.isMobile) {
            // Mobile-specific messages (re-encoding for iOS/Android)
            const statusMap = {
                'pending': 'Preparing for mobile...',
                'processing': 'Converting video...',
                'merging': 'Encoding for mobile playback...',
                'completed': 'Complete!',
                'failed': 'Failed',
                'cancelled': 'Cancelled'
            };
            return statusMap[status] || status;
        }

        // Desktop messages (stream copy is fast)
        const statusMap = {
            'pending': 'Preparing...',
            'processing': 'Processing...',
            'merging': 'Merging segments...',
            'completed': 'Complete!',
            'failed': 'Failed',
            'cancelled': 'Cancelled'
        };
        return statusMap[status] || status;
    }

    /**
     * Update merge progress UI
     */
    updateMergeProgress(percent, statusText) {
        $('.merge-progress-fill').css('width', `${percent}%`);
        $('.merge-percent').text(`${Math.round(percent)}%`);
        $('.merge-status').text(statusText);
    }

    /**
     * Handle successful preview merge completion
     */
    onPreviewMergeComplete(job) {
        clearInterval(this.previewMergePollingInterval);
        this.previewMergePollingInterval = null;

        // Hide merge progress, show video
        this.showSection('previewMerge', false);
        $('#timeline-preview-video').show();
        $('.timeline-preview-info').show();
        $('.timeline-preview-controls').show();

        this.mergedPreviewReady = true;

        // Load merged video
        const video = document.getElementById('timeline-preview-video');
        video.src = `/api/timeline/preview-merge/${this.currentPreviewMergeJobId}/stream`;
        video.load();

        // Hide prev/next buttons (single merged file now)
        $('#preview-prev-btn').hide();
        $('#preview-next-btn').hide();

        // Update preview info to show total selection info
        this.updateMergedPreviewInfo();

        console.log(`[Timeline] Preview merge complete: ${this.currentPreviewMergeJobId}`);
    }

    /**
     * Handle preview merge cancellation
     */
    onPreviewMergeCancelled() {
        clearInterval(this.previewMergePollingInterval);
        this.previewMergePollingInterval = null;

        this.showSection('previewMerge', false);
        this.showSection('preview', false);
        this.currentPreviewMergeJobId = null;

        console.log('[Timeline] Preview merge cancelled');
    }

    /**
     * Update preview info for merged video
     */
    updateMergedPreviewInfo() {
        if (this.selectedSegments.length === 0) return;

        // Get time range from first and last segment
        const firstSeg = this.selectedSegments[0];
        const lastSeg = this.selectedSegments[this.selectedSegments.length - 1];

        const startStr = firstSeg.start_time.toLocaleTimeString();
        const endStr = lastSeg.end_time.toLocaleTimeString();
        $('#preview-segment-time').text(`${startStr} - ${endStr}`);

        // Show segment count as type
        const $badge = $('#preview-segment-type');
        $badge
            .text(`${this.selectedSegments.length} segments`)
            .removeClass('motion continuous manual')
            .addClass('merged');
    }

    /**
     * Show error in preview section
     */
    showPreviewError(message) {
        this.showSection('previewMerge', false);
        $('.timeline-preview-info').html(
            `<span class="preview-error"><i class="fas fa-exclamation-triangle"></i> ${message}</span>`
        ).show();
        $('.timeline-preview-controls').hide();
    }

    /**
     * Cancel the current preview merge job
     */
    async cancelCurrentPreviewMerge() {
        if (this.previewMergePollingInterval) {
            clearInterval(this.previewMergePollingInterval);
            this.previewMergePollingInterval = null;
        }

        if (!this.currentPreviewMergeJobId) return;

        try {
            await fetch(
                `/api/timeline/preview-merge/${this.currentPreviewMergeJobId}/cancel`,
                { method: 'POST' }
            );
            console.log(`[Timeline] Cancelled preview merge: ${this.currentPreviewMergeJobId}`);
        } catch (error) {
            console.warn('[Timeline] Cancel failed:', error);
        }

        this.currentPreviewMergeJobId = null;
        this.mergedPreviewReady = false;
    }

    /**
     * Cleanup preview merge (delete temp files)
     */
    async cleanupPreviewMerge() {
        // Cancel if still running
        await this.cancelCurrentPreviewMerge();

        if (!this.currentPreviewMergeJobId) return;

        try {
            await fetch(
                `/api/timeline/preview-merge/${this.currentPreviewMergeJobId}/cleanup`,
                { method: 'DELETE' }
            );
            console.log(`[Timeline] Cleaned up preview: ${this.currentPreviewMergeJobId}`);
        } catch (error) {
            console.warn('[Timeline] Cleanup failed:', error);
        }

        this.currentPreviewMergeJobId = null;
        this.mergedPreviewReady = false;
    }

    /**
     * Hide the preview section
     */
    async hidePreview() {
        this.showSection('preview', false);
        this.showSection('previewMerge', false);

        // Pause and reset video
        const video = document.getElementById('timeline-preview-video');
        if (video) {
            video.pause();
            video.src = '';
        }

        // Cleanup temp files
        await this.cleanupPreviewMerge();

        console.log('[Timeline] Preview closed');
    }

    /**
     * Handle video canplay event
     */
    onVideoCanPlay() {
        // Auto-play when merged preview is ready
        if (this.mergedPreviewReady && this._pendingAutoPlay) {
            this._pendingAutoPlay = false;
            const video = document.getElementById('timeline-preview-video');
            if (video) {
                video.play().catch(err => {
                    console.warn('[Timeline] Auto-play prevented:', err);
                });
            }
        }
    }

    /**
     * Handle video ended event
     */
    onPreviewEnded() {
        console.log('[Timeline] Preview playback ended');
        // For merged preview, nothing to advance to
    }

    /**
     * Update preview control buttons
     */
    updatePreviewControls() {
        // For merged preview, prev/next are hidden
        // Just update play button state if needed
    }

    /**
     * Legacy methods for backwards compatibility (no-ops for merged preview)
     */
    previewPrevious() {
        // No-op for merged preview
    }

    previewNext() {
        // No-op for merged preview
    }

    playAllSelected() {
        // For merged preview, just play the video
        const video = document.getElementById('timeline-preview-video');
        if (video && this.mergedPreviewReady) {
            if (video.paused) {
                video.play().catch(err => console.warn('[Timeline] Play prevented:', err));
            } else {
                video.pause();
            }
        }
    }
}

// Auto-initialize when document is ready
$(document).ready(() => {
    window.timelinePlaybackModal = new TimelinePlaybackModal();
    console.log('[Timeline] Module loaded');
});
