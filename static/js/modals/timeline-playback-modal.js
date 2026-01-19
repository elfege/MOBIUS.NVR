/**
 * Timeline Playback Modal
 * Location: ~/0_NVR/static/js/modals/timeline-playback-modal.js
 *
 * Provides timeline visualization of recordings with:
 * - Drag-select time range for export
 * - Zoom in/out for granular selection
 * - Export to MP4 with iOS compatibility option
 * - Progress tracking for long exports
 */

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

        // Preview state
        this.selectedSegments = [];
        this.currentPreviewIndex = 0;
        this.isPlayingAll = false;

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

        // Video ended event - auto-advance when playing all
        const video = document.getElementById('timeline-preview-video');
        if (video) {
            video.addEventListener('ended', () => this.onPreviewEnded());
        }
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

        // Reset UI
        this.showSection('empty', false);
        this.showSection('loading', false);
        this.showSection('canvas', false);
        this.showSection('zoom', false);
        this.showSection('export', false);
        this.showSection('progress', false);
        this.showSection('download', false);
        $('#timeline-export-btn').prop('disabled', true);

        // Show modal
        this.$modal.show();

        // Auto-load last 24 hours
        this.setPresetRange(24);
    }

    /**
     * Hide modal
     */
    hide() {
        this.$modal.hide();

        // Cancel any ongoing export
        if (this.currentExportJobId) {
            this.cancelExport();
        }

        // Clear poll interval
        if (this.exportPollInterval) {
            clearInterval(this.exportPollInterval);
            this.exportPollInterval = null;
        }
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
        if (this.selectedSegments.length > 0) {
            this.showPreview();
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
     */
    async startExport() {
        if (!this.selection.start || !this.selection.end) {
            alert('Please select a time range first');
            return;
        }

        const start = this.selection.start < this.selection.end ?
            this.selection.start : this.selection.end;
        const end = this.selection.start < this.selection.end ?
            this.selection.end : this.selection.start;

        const iosCompatible = $('#export-ios-compatible').is(':checked');

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
     */
    downloadExport() {
        if (!this.currentExportJobId) return;

        // Create download link
        const downloadUrl = `/api/timeline/export/${this.currentExportJobId}/download`;

        // For iOS, we might need special handling
        const isIOS = /iPad|iPhone|iPod/.test(navigator.userAgent);

        if (isIOS) {
            // On iOS, open in new tab to trigger download/share sheet
            window.open(downloadUrl, '_blank');
        } else {
            // On desktop, trigger download
            const a = document.createElement('a');
            a.href = downloadUrl;
            a.download = ''; // Let server set filename
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
        }

        // Reset UI after brief delay
        setTimeout(() => {
            this.showSection('download', false);
            this.showSection('export', true);
            this.currentExportJobId = null;
        }, 2000);
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
            'preview': '.timeline-preview-section'
        };

        const selector = sectionMap[section];
        if (selector) {
            this.$modal.find(selector).toggle(show);
        }
    }

    // =========================================================================
    // Preview Methods
    // =========================================================================

    /**
     * Show the preview section and load first segment
     */
    showPreview() {
        if (this.selectedSegments.length === 0) {
            console.log('[Timeline] No segments to preview');
            return;
        }

        this.currentPreviewIndex = 0;
        this.isPlayingAll = false;
        this.showSection('preview', true);
        this.loadPreviewSegment(0);
        this.updatePreviewControls();

        console.log(`[Timeline] Preview opened with ${this.selectedSegments.length} segments`);
    }

    /**
     * Hide the preview section
     */
    hidePreview() {
        this.showSection('preview', false);
        this.isPlayingAll = false;

        // Pause and reset video
        const video = document.getElementById('timeline-preview-video');
        if (video) {
            video.pause();
            video.src = '';
        }

        console.log('[Timeline] Preview closed');
    }

    /**
     * Load a specific segment for preview
     * @param {number} index - Index in selectedSegments array
     */
    loadPreviewSegment(index) {
        if (index < 0 || index >= this.selectedSegments.length) {
            console.warn('[Timeline] Invalid segment index:', index);
            return;
        }

        const segment = this.selectedSegments[index];
        this.currentPreviewIndex = index;

        // Build preview URL using recording ID
        const previewUrl = `/api/timeline/preview/${segment.recording_id}`;

        // Get video element
        const video = document.getElementById('timeline-preview-video');
        if (!video) {
            console.error('[Timeline] Preview video element not found');
            return;
        }

        // Update video source
        video.src = previewUrl;
        video.load();

        // Auto-play if in "play all" mode
        if (this.isPlayingAll) {
            video.play().catch(err => {
                console.warn('[Timeline] Auto-play prevented:', err);
            });
        }

        // Update preview info display
        this.updatePreviewInfo(segment);
        this.updatePreviewControls();

        console.log(`[Timeline] Loading preview segment ${index + 1}/${this.selectedSegments.length}: ${segment.recording_id}`);
    }

    /**
     * Update the preview info display
     * @param {object} segment - Current segment being previewed
     */
    updatePreviewInfo(segment) {
        // Format time range
        const startStr = segment.start_time.toLocaleTimeString();
        const endStr = segment.end_time.toLocaleTimeString();
        $('#preview-segment-time').text(`${startStr} - ${endStr}`);

        // Update type badge with appropriate class
        const $badge = $('#preview-segment-type');
        $badge
            .text(segment.recording_type)
            .removeClass('motion continuous manual')
            .addClass(segment.recording_type);
    }

    /**
     * Update preview control buttons (prev/next enable state)
     */
    updatePreviewControls() {
        const hasPrev = this.currentPreviewIndex > 0;
        const hasNext = this.currentPreviewIndex < this.selectedSegments.length - 1;

        $('#preview-prev-btn').prop('disabled', !hasPrev);
        $('#preview-next-btn').prop('disabled', !hasNext);

        // Update play all button text
        const $playBtn = $('#preview-play-all-btn');
        if (this.isPlayingAll) {
            $playBtn.html('<i class="fas fa-stop"></i> Stop');
        } else {
            $playBtn.html('<i class="fas fa-play"></i> Play Selection');
        }
    }

    /**
     * Go to previous segment in preview
     */
    previewPrevious() {
        if (this.currentPreviewIndex > 0) {
            this.loadPreviewSegment(this.currentPreviewIndex - 1);
        }
    }

    /**
     * Go to next segment in preview
     */
    previewNext() {
        if (this.currentPreviewIndex < this.selectedSegments.length - 1) {
            this.loadPreviewSegment(this.currentPreviewIndex + 1);
        }
    }

    /**
     * Start playing all selected segments in sequence
     */
    playAllSelected() {
        if (this.isPlayingAll) {
            // Stop playback
            this.isPlayingAll = false;
            const video = document.getElementById('timeline-preview-video');
            if (video) video.pause();
            this.updatePreviewControls();
            console.log('[Timeline] Stopped play-all mode');
            return;
        }

        // Start playing from first segment
        this.isPlayingAll = true;
        this.loadPreviewSegment(0);

        // Ensure video starts playing
        const video = document.getElementById('timeline-preview-video');
        if (video) {
            video.play().catch(err => {
                console.warn('[Timeline] Auto-play prevented:', err);
                this.isPlayingAll = false;
                this.updatePreviewControls();
            });
        }

        this.updatePreviewControls();
        console.log('[Timeline] Started play-all mode');
    }

    /**
     * Handle video ended event - advance to next segment if playing all
     */
    onPreviewEnded() {
        console.log('[Timeline] Preview segment ended');

        if (this.isPlayingAll && this.currentPreviewIndex < this.selectedSegments.length - 1) {
            // Auto-advance to next segment
            this.loadPreviewSegment(this.currentPreviewIndex + 1);
        } else if (this.isPlayingAll) {
            // All segments played
            this.isPlayingAll = false;
            this.updatePreviewControls();
            console.log('[Timeline] All segments played');
        }
    }
}

// Auto-initialize when document is ready
$(document).ready(() => {
    window.timelinePlaybackModal = new TimelinePlaybackModal();
    console.log('[Timeline] Module loaded');
});
