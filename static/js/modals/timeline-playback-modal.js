/**
 * Timeline Playback Modal
 * Location: ~/0_MOBIUS.NVR/static/js/modals/timeline-playback-modal.js
 * Version: 2026-01-27-v4 (download files feature)
 *
 * Provides timeline visualization of recordings with:
 * - Drag-select time range for export
 * - Zoom in/out for granular selection
 * - Export to MP4 with iOS compatibility option
 * - Progress tracking for long exports
 * - Download individual recording files (v4)
 */

console.log('[Timeline] JS file loaded - version 2026-01-27-v4');

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

        // Download files state
        this.downloadFilesSelected = new Set();
        this.isDownloadFilesVisible = false;

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

        // Auto-check iOS compatible checkbox on mobile devices
        // Desktop users can still check it manually if needed
        if (this.isMobile) {
            $('#export-ios-compatible').prop('checked', true);
        }

        console.log('[Timeline] Modal initialized, isMobile:', this.isMobile);
    }

    /**
     * Attach modal control events.
     *
     * Backdrop clicks do NOT close the modal — by design. Closing is intentional
     * only via the X button or the Escape key. Any other interaction (drag-select,
     * pan, accidental click anywhere) must be safe.
     */
    attachModalEvents() {
        // Close button
        this.$modal.find('.timeline-modal-close').on('click', () => this.hide());

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
            const $btn = $(e.target).closest('.timeline-preset-btn');
            const hours = parseInt($btn.data('hours'));
            if (!isNaN(hours)) {
                this.setPresetRange(hours);
            }
        });

        // Zoom controls
        $('.timeline-zoom-btn').on('click', (e) => {
            const action = $(e.target).closest('button').data('zoom');
            this.handleZoom(action);
        });

        $('#timeline-zoom-slider').on('input', (e) => {
            this.zoomLevel = parseInt(e.target.value);
            this.renderTimeline();
            this._savePersistedState();
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

        // Download files controls
        $('#timeline-download-files-btn').on('click', () => this.showDownloadFiles());
        $('.timeline-download-files-close').on('click', () => this.hideDownloadFiles());
        $('#download-files-select-all').on('change', (e) => this.toggleSelectAllFiles(e.target.checked));
        $('#download-files-btn').on('click', () => this.downloadSelectedFiles());

        // File checkbox change handler (delegated)
        $('#download-files-list').on('change', '.file-checkbox', (e) => {
            e.stopPropagation();
            const $checkbox = $(e.target);
            const $item = $checkbox.closest('li');
            const filePath = $item.data('file-path');

            if ($checkbox.is(':checked')) {
                this.downloadFilesSelected.add(filePath);
                $item.addClass('checked');
            } else {
                this.downloadFilesSelected.delete(filePath);
                $item.removeClass('checked');
            }

            this.updateDownloadFilesUI();
        });

        // File row click to toggle checkbox
        $('#download-files-list').on('click', 'li', (e) => {
            if ($(e.target).hasClass('file-checkbox')) return;
            const $checkbox = $(e.currentTarget).find('.file-checkbox');
            $checkbox.prop('checked', !$checkbox.is(':checked')).trigger('change');
        });

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
     * Attach canvas interaction events for drag selection + pan + zoom.
     *
     * Gestures:
     *   - Left-click + drag       → range selection (existing behavior)
     *   - Right-click + drag      → pan timeline (new)
     *   - Mouse wheel             → zoom centered on cursor (new)
     *   - Shift + wheel           → pan horizontally (new)
     *   - Trackpad horizontal swipe (wheel deltaX) → pan (new)
     *
     * When pan/zoom takes the visible window outside the currently loaded
     * timeRange, _extendRangeIfPannedPast() refetches the surrounding data.
     */
    attachCanvasEvents() {
        if (!this.$canvas.length) return;

        const canvas = this.$canvas[0];
        canvas.style.cursor = 'grab';

        // Pan-on-right-click state (kept in closures so it doesn't pollute `this`).
        let _panActive = false;
        let _panStartClientX = 0;
        let _panStartOffset = 0;

        // Disable the default context menu on the canvas — we use right-click for pan.
        canvas.addEventListener('contextmenu', (e) => e.preventDefault());

        // Mouse down — branch on button:
        //   button 0 (left)  → start range-select drag
        //   button 2 (right) → start pan
        canvas.addEventListener('mousedown', (e) => {
            const rect = canvas.getBoundingClientRect();
            const x = e.clientX - rect.left;

            if (e.button === 2) {
                e.preventDefault();
                _panActive = true;
                _panStartClientX = e.clientX;
                _panStartOffset = this.panOffset;
                canvas.style.cursor = 'grabbing';
                return;
            }
            if (e.button !== 0) return;

            this.isDragging = true;
            this.dragStart = x;
            this.selection.start = this.xToTime(x);
            this.selection.end = null;
        });

        // Mouse move — pan if right-button-down, otherwise update selection.
        canvas.addEventListener('mousemove', (e) => {
            if (_panActive) {
                const rect      = canvas.getBoundingClientRect();
                const totalMs   = this.timeRange ? (this.timeRange.end - this.timeRange.start) : 0;
                if (totalMs <= 0) return;
                const visibleMs = totalMs / this.zoomLevel;
                const msPerPx   = visibleMs / rect.width;
                const dxPx      = e.clientX - _panStartClientX;
                // Drag right → reveal earlier time → panOffset decreases.
                this.panOffset = _panStartOffset - dxPx * msPerPx;
                this.renderTimeline();
                return;
            }
            if (!this.isDragging) return;

            const rect = canvas.getBoundingClientRect();
            const x = e.clientX - rect.left;
            this.selection.end = this.xToTime(x);
            this.renderTimeline();
            this.updateSelectionInfo();
        });

        // Mouse up — finish whichever gesture was active.
        canvas.addEventListener('mouseup', (e) => {
            if (_panActive) {
                _panActive = false;
                canvas.style.cursor = 'grab';
                this._savePersistedState();
                this._extendRangeIfPannedPast();
                return;
            }
            if (this.isDragging) {
                this.isDragging = false;
                this.finalizeSelection();
            }
        });

        // Mouse leave — cancel any in-progress gesture cleanly.
        canvas.addEventListener('mouseleave', () => {
            if (_panActive) {
                _panActive = false;
                canvas.style.cursor = 'grab';
                this._savePersistedState();
                this._extendRangeIfPannedPast();
            }
            if (this.isDragging) {
                this.isDragging = false;
            }
        });

        // Wheel — pan if Shift or horizontal trackpad swipe; otherwise zoom on cursor.
        canvas.addEventListener('wheel', (e) => {
            if (!this.timeRange) return;
            e.preventDefault();

            const rect      = canvas.getBoundingClientRect();
            const totalMs   = this.timeRange.end - this.timeRange.start;
            const visibleMs = totalMs / this.zoomLevel;

            const isHorizontalGesture = Math.abs(e.deltaX) > Math.abs(e.deltaY);
            const isPan = e.shiftKey || isHorizontalGesture;

            if (isPan) {
                const delta   = isHorizontalGesture ? e.deltaX : e.deltaY;
                const msPerPx = visibleMs / rect.width;
                this.panOffset += delta * msPerPx;
                this.renderTimeline();
                clearTimeout(this._wheelSettleTimer);
                this._wheelSettleTimer = setTimeout(() => {
                    this._savePersistedState();
                    this._extendRangeIfPannedPast();
                }, 200);
                return;
            }

            // Zoom centered on cursor: keep the timestamp under the cursor fixed.
            const cursorX  = e.clientX - rect.left;
            const cursorMs = this.timeRange.start.getTime() + this.panOffset
                             + (cursorX / rect.width) * visibleMs;

            const oldZoom  = this.zoomLevel;
            const step     = e.deltaY < 0 ? 1 : -1;  // wheel-up = zoom in
            const newZoom  = Math.max(1, Math.min(10, oldZoom + step));
            if (newZoom === oldZoom) return;

            this.zoomLevel = newZoom;
            const newVisibleMs = totalMs / this.zoomLevel;
            // Solve for new panOffset so cursorMs sits at the same cursor x.
            this.panOffset = cursorMs - this.timeRange.start.getTime()
                             - (cursorX / rect.width) * newVisibleMs;

            const $slider = $('#timeline-zoom-slider');
            if ($slider.length) $slider.val(this.zoomLevel);
            this.renderTimeline();

            clearTimeout(this._wheelSettleTimer);
            this._wheelSettleTimer = setTimeout(() => {
                this._savePersistedState();
                this._extendRangeIfPannedPast();
            }, 200);
        }, { passive: false });

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
     * localStorage key holding the last-viewed time range per camera.
     * Bump the version suffix to invalidate cached state on schema changes.
     */
    _persistKey() { return 'nvr_timeline_state_v1'; }

    /**
     * Read the persisted state map ({cameraId: {date, startTime, endTime, zoomLevel}}).
     */
    _loadPersistedState() {
        try {
            const raw = localStorage.getItem(this._persistKey());
            return raw ? JSON.parse(raw) : {};
        } catch (e) {
            return {};
        }
    }

    /**
     * Persist the current camera's range/zoom for restore on next open.
     * Called on every load + zoom change so the user never loses their position.
     */
    _savePersistedState() {
        if (!this.currentCameraId) return;
        try {
            const all = this._loadPersistedState();
            all[this.currentCameraId] = {
                date:      $('#timeline-date').val(),
                startTime: $('#timeline-start-time').val(),
                endTime:   $('#timeline-end-time').val(),
                zoomLevel: this.zoomLevel,
                savedAt:   Date.now()
            };
            localStorage.setItem(this._persistKey(), JSON.stringify(all));
        } catch (e) {
            // Quota or serialization error — silent, this is a UX nicety not a contract.
        }
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

        // Reset download files state
        this.downloadFilesSelected.clear();
        this.isDownloadFilesVisible = false;

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
        $('.timeline-download-files').hide();
        $('#timeline-export-btn').prop('disabled', true);
        $('#timeline-download-files-btn').prop('disabled', true);

        // Show modal
        this.$modal.show();

        // Ensure video event listeners are attached (may not have been if modal wasn't in DOM during init)
        this.attachVideoEventListeners();

        // Restore last-viewed range for this camera if we have one (and it's not stale).
        // Otherwise fall back to the default "last 24 hours" preset.
        const persisted = this._loadPersistedState()[cameraId];
        const STALE_AFTER_MS = 7 * 24 * 60 * 60 * 1000; // 7 days
        const isUsable = persisted
            && persisted.date && persisted.startTime && persisted.endTime
            && (Date.now() - (persisted.savedAt || 0) < STALE_AFTER_MS);

        if (isUsable) {
            $('#timeline-date').val(persisted.date);
            $('#timeline-start-time').val(persisted.startTime);
            $('#timeline-end-time').val(persisted.endTime);
            this.zoomLevel = persisted.zoomLevel || 1;
            const $slider = $('#timeline-zoom-slider');
            if ($slider.length) $slider.val(this.zoomLevel);
            this.loadTimeline();
        } else {
            this.setPresetRange(24);
        }
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

        // Persist this range so reopening the modal restores where we were.
        this._savePersistedState();

        await this._fetchSegments(startDate, endDate, /*showLoading=*/true);
    }

    /**
     * Low-level: fetch segments for an arbitrary range and update state.
     * Does not touch the date/time input fields — used by both loadTimeline()
     * (which reads inputs) and the pan-extend path (which doesn't).
     */
    async _fetchSegments(startDate, endDate, showLoading = true) {
        if (showLoading) {
            this.showSection('loading', true);
            this.showSection('canvas', false);
            this.showSection('empty', false);
        }

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
     * After right-click pan ends or wheel scroll lands, check whether the
     * visible window now extends past the currently-loaded `timeRange`. If
     * so, expand `timeRange` outward (with a small pad), refetch the segments,
     * and adjust `panOffset` so the user's visual position stays put.
     *
     * IMPORTANT: this function does NOT touch the date/time input fields
     * and does NOT call `_savePersistedState()`. Those fields represent the
     * user's *explicit* choice (last "Load Timeline" they hit). Overwriting
     * them with extended bounds — especially when extension wraps across
     * midnight — flips AM/PM and corrupts the saved memoization.
     *
     * The internal `timeRange` (loaded data window) is allowed to grow past
     * the user's chosen bounds; the inputs stay frozen at what they typed.
     */
    async _extendRangeIfPannedPast() {
        if (!this.timeRange) return;

        const totalMs   = this.timeRange.end - this.timeRange.start;
        const visibleMs = totalMs / this.zoomLevel;
        const visStart  = this.timeRange.start.getTime() + this.panOffset;
        const visEnd    = visStart + visibleMs;

        const loadedStart = this.timeRange.start.getTime();
        const loadedEnd   = this.timeRange.end.getTime();

        // Pad by half a visible window outward so the next pan in the same
        // direction doesn't immediately re-trigger a fetch.
        const pad = visibleMs * 0.5;
        let newStart = loadedStart;
        let newEnd   = loadedEnd;
        let extend   = false;

        if (visStart < loadedStart) { newStart = visStart - pad; extend = true; }
        if (visEnd   > loadedEnd)   { newEnd   = visEnd   + pad; extend = true; }

        if (!extend) return;

        // Translate panOffset so the visible window stays where the user sees it.
        this.panOffset = visStart - newStart;
        this.timeRange = { start: new Date(newStart), end: new Date(newEnd) };

        await this._fetchSegments(this.timeRange.start, this.timeRange.end, /*showLoading=*/false);
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

        // Enable export and download buttons if segments selected
        const hasSegments = this.selectedSegments.length > 0;
        $('#timeline-export-btn').prop('disabled', !hasSegments);
        $('#timeline-download-files-btn').prop('disabled', !hasSegments);

        // Hide download files section if no segments selected
        if (!hasSegments && this.isDownloadFilesVisible) {
            this.hideDownloadFiles();
        }

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
        this._savePersistedState();
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

        console.log('[Timeline] startExport called - mergedPreviewReady:', this.mergedPreviewReady,
            'currentPreviewMergeJobId:', this.currentPreviewMergeJobId);

        // If merged preview is ready, promote it instead of re-merging
        if (this.mergedPreviewReady && this.currentPreviewMergeJobId) {
            console.log('[Timeline] Using promote path (preview already merged)');
            await this.promotePreviewToExport(iosCompatible);
            return;
        }

        console.log('[Timeline] Using full export path (no merged preview ready)');
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
     * On iOS, shows video inline with instructions to long-press and save
     * On Android, opens in new tab for native handling
     * On desktop, triggers direct download
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
            // On iOS, load video into the preview player and show save instructions
            // User can long-press video or use share sheet to save
            this.showIOSInlineDownload(downloadUrl);
        } else if (this.isMobile) {
            // Android and other mobile - open in new tab for native handling
            window.open(downloadUrl, '_blank');
            this.resetDownloadUI(2000);
        } else {
            // On desktop, trigger direct download
            const a = document.createElement('a');
            a.href = downloadUrl;
            a.download = this.completedJob?.filename || '';
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
            this.resetDownloadUI(2000);
        }
    }

    /**
     * Show video inline for iOS download with Share button
     * Provides two options:
     * 1. Share button using Web Share API (if available)
     * 2. Open in new tab button for native Safari video player
     */
    showIOSInlineDownload(downloadUrl) {
        // Convert download URL to stream URL for inline viewing
        const streamUrl = downloadUrl.replace('/download', '/stream');

        // Show preview section with video and save options
        this.showSection('download', false);
        this.showSection('preview', true);
        this.showSection('previewMerge', false);

        // Load video into preview player
        const $video = this.$modal.find('#timeline-preview-video');
        const video = $video[0];
        video.src = streamUrl;
        video.load();
        $video.show();

        // Hide the regular preview info (absolute positioned overlay)
        this.$modal.find('.timeline-preview-info').hide();

        // Build save options HTML with Share button and Open in Tab button
        const hasShareAPI = navigator.share && navigator.canShare;

        // Create a NEW container for iOS save buttons OUTSIDE the video container
        // This prevents z-index issues with the HTML5 video player
        let $iosSaveContainer = this.$modal.find('.ios-save-container');
        if (!$iosSaveContainer.length) {
            // Insert after the preview container, before the controls
            $iosSaveContainer = $('<div class="ios-save-container"></div>');
            this.$modal.find('.timeline-preview-container').after($iosSaveContainer);
        }

        let buttonsHtml = '';
        if (hasShareAPI) {
            buttonsHtml += `
                <button class="ios-share-btn timeline-btn timeline-btn-primary">
                    <i class="fas fa-share-square"></i> Share / Save
                </button>
            `;
        }
        buttonsHtml += `
            <button class="ios-open-tab-btn timeline-btn">
                <i class="fas fa-external-link-alt"></i> Open in New Tab
            </button>
        `;

        $iosSaveContainer.html(`
            <div class="ios-save-instructions">
                <strong>Save video to Photos:</strong>
            </div>
            <div class="ios-save-buttons">
                ${buttonsHtml}
            </div>
            <div class="ios-save-hint">
                ${hasShareAPI ? 'Tap Share, then "Save Video"' : 'Open in tab, then tap Share → Save'}
            </div>
        `).show();

        // Attach share button handler
        if (hasShareAPI) {
            $iosSaveContainer.find('.ios-share-btn').on('click', async () => {
                try {
                    // Fetch the video as a blob for sharing
                    const response = await fetch(streamUrl);
                    const blob = await response.blob();
                    const file = new File([blob], this.completedJob?.filename || 'video.mp4', { type: 'video/mp4' });

                    await navigator.share({
                        files: [file],
                        title: 'NVR Recording'
                    });
                    console.log('[Timeline] Share completed');
                } catch (err) {
                    if (err.name !== 'AbortError') {
                        console.error('[Timeline] Share failed:', err);
                        // Fall back to opening in new tab
                        window.open(streamUrl, '_blank');
                    }
                }
            });
        }

        // Attach open in tab handler
        $iosSaveContainer.find('.ios-open-tab-btn').on('click', () => {
            window.open(streamUrl, '_blank');
        });

        // Hide preview controls (don't need prev/next/play for iOS download)
        this.$modal.find('.timeline-preview-controls').hide();

        // Add Done button to the iOS save container
        let $doneBtn = $iosSaveContainer.find('.ios-done-btn');
        if (!$doneBtn.length) {
            $doneBtn = $('<button class="ios-done-btn timeline-btn timeline-btn-success"><i class="fas fa-check"></i> Done</button>');
            $iosSaveContainer.append($doneBtn);
        }

        $doneBtn.off('click').on('click', () => {
            $iosSaveContainer.hide();
            this.showSection('preview', false);
            this.showSection('export', true);
            this.currentExportJobId = null;
            this.completedJob = null;
        });
    }

    /**
     * Reset download UI after delay
     */
    resetDownloadUI(delay) {
        setTimeout(() => {
            this.showSection('download', false);
            this.showSection('export', true);
            this.currentExportJobId = null;
            this.completedJob = null;
        }, delay);
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
            await this.cancelCurrentPreviewMerge();

            // Show preview section with merge progress
            this.showSection('preview', true);
            this.showSection('previewMerge', true);

            // Disable export button while encoding
            $('#timeline-export-btn').prop('disabled', true);

            // Get preview section reference
            const $previewSection = this.$modal.find('.timeline-preview-section');
            const $modalBody = this.$modal.find('.timeline-modal-body');

            // On mobile/narrow viewports, scroll to make preview section visible
            // This ensures user can see the merge progress and video player
            if (this.isMobile || window.innerWidth < 768) {
                // Small delay to allow DOM to update before scrolling
                setTimeout(() => {
                    const previewOffset = $previewSection.position();
                    if (previewOffset && previewOffset.top > 100) {
                        // Scroll preview into view with some padding at top
                        $modalBody.animate({
                            scrollTop: $modalBody.scrollTop() + previewOffset.top - 50
                        }, 300);
                    }
                }, 100);
            }

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

        // Show video and controls - use scoped selectors for reliability
        const $video = this.$modal.find('#timeline-preview-video');
        const $controls = this.$modal.find('.timeline-preview-controls');

        $video.show();
        this.$modal.find('.timeline-preview-info').show();
        $controls.show();

        this.mergedPreviewReady = true;

        // Re-enable export button now that merge is complete
        $('#timeline-export-btn').prop('disabled', false);

        // Load merged video
        const video = document.getElementById('timeline-preview-video');
        video.src = `/api/timeline/preview-merge/${this.currentPreviewMergeJobId}/stream`;
        video.load();

        // Hide prev/next buttons (single merged file now)
        this.$modal.find('#preview-prev-btn').hide();
        this.$modal.find('#preview-next-btn').hide();

        // Update preview info to show total selection info
        this.updateMergedPreviewInfo();

        // On mobile/narrow viewports, scroll to ensure video is visible
        if (this.isMobile || window.innerWidth < 768) {
            const $previewSection = this.$modal.find('.timeline-preview-section');
            const $modalBody = this.$modal.find('.timeline-modal-body');
            const previewOffset = $previewSection.position();

            if (previewOffset && previewOffset.top > 50) {
                $modalBody.animate({
                    scrollTop: $modalBody.scrollTop() + previewOffset.top - 30
                }, 300);
            }
        }

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

    // ========================================
    // Download Files Methods
    // ========================================

    /**
     * Show download files section with list of selected segments
     */
    showDownloadFiles() {
        if (this.selectedSegments.length === 0) {
            console.warn('[Timeline] No segments selected for download');
            return;
        }

        this.isDownloadFilesVisible = true;
        this.downloadFilesSelected.clear();

        // Populate the file list
        const $list = $('#download-files-list');
        $list.empty();

        this.selectedSegments.forEach(seg => {
            // Extract filename from file_path
            const fileName = seg.file_path.split('/').pop();
            const fileSize = this.formatFileSize(seg.file_size_bytes);
            const startTime = new Date(seg.start_time).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
            const duration = this.formatDuration(seg.duration_seconds);

            const $item = $(`
                <li data-file-path="${this.escapeHtml(seg.file_path)}" data-recording-id="${seg.recording_id}">
                    <input type="checkbox" class="file-checkbox" title="Select for download">
                    <i class="fas fa-file-video file-icon"></i>
                    <div class="file-info">
                        <span class="file-name">${this.escapeHtml(fileName)}</span>
                        <div class="file-details">
                            <span class="file-size">${fileSize}</span>
                            <span class="file-time">${startTime} (${duration})</span>
                            <span class="file-type ${seg.recording_type}">${seg.recording_type}</span>
                        </div>
                    </div>
                </li>
            `);
            $list.append($item);
        });

        // Reset UI state
        $('#download-files-select-all').prop('checked', false).prop('indeterminate', false);
        this.updateDownloadFilesUI();

        // Show section
        $('.timeline-download-files').show();

        console.log(`[Timeline] Download files shown with ${this.selectedSegments.length} files`);
    }

    /**
     * Hide download files section
     */
    hideDownloadFiles() {
        this.isDownloadFilesVisible = false;
        this.downloadFilesSelected.clear();
        $('.timeline-download-files').hide();
    }

    /**
     * Toggle select all files
     * @param {boolean} selectAll - Whether to select or deselect all
     */
    toggleSelectAllFiles(selectAll) {
        const $items = $('#download-files-list li');

        $items.each((_, el) => {
            const $item = $(el);
            const filePath = $item.data('file-path');
            const $checkbox = $item.find('.file-checkbox');

            $checkbox.prop('checked', selectAll);
            if (selectAll) {
                this.downloadFilesSelected.add(filePath);
                $item.addClass('checked');
            } else {
                this.downloadFilesSelected.delete(filePath);
                $item.removeClass('checked');
            }
        });

        this.updateDownloadFilesUI();
    }

    /**
     * Update download files UI state (button, count, select all checkbox)
     */
    updateDownloadFilesUI() {
        const count = this.downloadFilesSelected.size;
        const totalCount = this.selectedSegments.length;

        // Update selection count display
        const $countDisplay = $('#download-files-selection-count');
        if (count === 0) {
            $countDisplay.text('').removeClass('has-selection');
            $('#download-files-btn').prop('disabled', true);
        } else {
            // Calculate total size of selected files
            let totalSize = 0;
            this.downloadFilesSelected.forEach(filePath => {
                const seg = this.selectedSegments.find(s => s.file_path === filePath);
                if (seg) totalSize += seg.file_size_bytes;
            });

            $countDisplay
                .text(`${count} file${count > 1 ? 's' : ''} selected (${this.formatFileSize(totalSize)})`)
                .addClass('has-selection');
            $('#download-files-btn').prop('disabled', false);
        }

        // Update select all checkbox state
        const $selectAll = $('#download-files-select-all');
        if (count === 0) {
            $selectAll.prop('checked', false).prop('indeterminate', false);
        } else if (count === totalCount) {
            $selectAll.prop('checked', true).prop('indeterminate', false);
        } else {
            $selectAll.prop('checked', false).prop('indeterminate', true);
        }
    }

    /**
     * Download selected files
     */
    async downloadSelectedFiles() {
        if (this.downloadFilesSelected.size === 0) return;

        const files = Array.from(this.downloadFilesSelected);
        console.log(`[Timeline] Downloading ${files.length} files`);

        for (const filePath of files) {
            await this.downloadFile(filePath);
            // Small delay between downloads to avoid overwhelming browser
            await new Promise(resolve => setTimeout(resolve, 300));
        }
    }

    /**
     * Download a single file
     * @param {string} filePath - Full file path on server
     */
    async downloadFile(filePath) {
        // Remove leading /recordings/ to get relative path for download API
        // The file_path from segments looks like: /recordings/motion/SERIAL/file.mp4
        let relativePath = filePath;
        if (relativePath.startsWith('/recordings/')) {
            relativePath = relativePath.substring('/recordings/'.length);
        }

        // Encode each path segment separately to preserve slashes for Flask routing
        const downloadUrl = `/api/recordings/download/${relativePath.split('/').map(encodeURIComponent).join('/')}`;

        const fileName = filePath.split('/').pop();

        const link = document.createElement('a');
        link.href = downloadUrl;
        link.download = fileName;
        link.style.display = 'none';
        document.body.appendChild(link);
        link.click();
        document.body.removeChild(link);

        console.log(`[Timeline] Started download: ${fileName}`);
    }

    /**
     * Format file size for display
     * @param {number} bytes - Size in bytes
     * @returns {string} Formatted size
     */
    formatFileSize(bytes) {
        if (bytes === 0) return '0 B';
        const k = 1024;
        const sizes = ['B', 'KB', 'MB', 'GB', 'TB'];
        const i = Math.floor(Math.log(bytes) / Math.log(k));
        return parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + ' ' + sizes[i];
    }

    /**
     * Format duration for display
     * @param {number} seconds - Duration in seconds
     * @returns {string} Formatted duration (e.g., "2:30")
     */
    formatDuration(seconds) {
        const minutes = Math.floor(seconds / 60);
        const secs = Math.floor(seconds % 60);
        return `${minutes}:${secs.toString().padStart(2, '0')}`;
    }

    /**
     * Escape HTML for safe insertion
     * @param {string} text - Text to escape
     * @returns {string} Escaped text
     */
    escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }
}

// Auto-initialize when document is ready
$(document).ready(() => {
    window.timelinePlaybackModal = new TimelinePlaybackModal();
    console.log('[Timeline] Module loaded');
});
