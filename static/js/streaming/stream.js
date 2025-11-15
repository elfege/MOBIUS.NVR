/**
 * Multi-Stream Manager - ES6 + jQuery
 * Orchestrates HLS and MJPEG stream managers for unified camera viewing
 */

import { PTZController } from '../controllers/ptz-controller.js';
import { FLVStreamManager } from './flv-stream.js';
import { makeHealthMonitor } from './health.js';
import { HLSStreamManager } from './hls-stream.js';
import { MJPEGStreamManager } from './mjpeg-stream.js';


export class MultiStreamManager {
    constructor() {
        this.mjpegManager = new MJPEGStreamManager();
        this.hlsManager = new HLSStreamManager();
        this.flvManager = new FLVStreamManager();
        this.ptzController = new PTZController();
        // Arrow function preserves context
        this.getCameraConfig = (id) => this.hlsManager.getCameraConfig(id);
        this.buildHlsConfig = (config, isLL) => this.hlsManager.buildHlsConfig(config, isLL);

        // Cache jQuery selectors
        this.$container = $('#streams-container');
        this.$streamCount = $('#stream-count');


        this.restartAttempts = new Map(); // Track restart attempts per camera
        this.restartTimers = new Map();   // Track pending retry timers
        this.recentFailures = new Map();  // Track failure history for escalating recovery

        const H = window.UI_HEALTH || {};
        console.log(`UI HEALTH ENV: ${JSON.stringify(H)}`)
        // Only create health monitor if enabled
        if (H.uiHealthEnabled) {
            this.health = makeHealthMonitor({
                blankThreshold: (H.blankAvg != null && H.blankStd != null)
                    ? { avg: H.blankAvg, std: H.blankStd }  // cameras.json flat format
                    : (H.blankThreshold || { avg: 12, std: 5 }),  // .env nested format or default
                sampleIntervalMs: H.sampleIntervalMs ?? 6000,
                staleAfterMs: H.staleAfterMs ?? 20000,
                consecutiveBlankNeeded: H.consecutiveBlankNeeded ?? 10,
                cooldownMs: H.cooldownMs ?? 60000,
                warmupMs: H.warmupMs ?? 60000,
                onUnhealthy: async ({ cameraId, reason, metrics }) => {
                    console.warn(`[Health] Stream unhealthy: ${cameraId}, reason: ${reason}`, metrics);

                    const $streamItem = $(`.stream-item[data-camera-serial="${cameraId}"]`);
                    if (!$streamItem.length) return;

                    // Get camera metadata for nuclear recovery
                    const cameraType = $streamItem.data('camera-type');
                    const streamType = $streamItem.data('stream-type');

                    // Get restart attempt count
                    const attempts = this.restartAttempts.get(cameraId) || 0;
                    const maxAttempts = H.maxAttempts ?? 10;  // 0 = infinite

                    // Check if max attempts reached (skip check if maxAttempts is 0)
                    if (maxAttempts > 0 && attempts >= maxAttempts) {
                        console.error(`[Health] ${cameraId}: Max restart attempts (${maxAttempts}) reached`);
                        this.setStreamStatus($streamItem, 'failed', `Failed after ${maxAttempts} attempts`);
                        return;
                    }

                    // Track failure history for escalating recovery
                    const now = Date.now();
                    const history = this.recentFailures.get(cameraId) || {
                        timestamps: [],
                        lastMethod: null
                    };

                    // Clean old failures (>60s ago)
                    history.timestamps = history.timestamps.filter(t => now - t < 60000);
                    history.timestamps.push(now);

                    // Determine recovery method: first 3 tries = refresh, then nuclear
                    const recentFailureCount = history.timestamps.length;
                    const method = (recentFailureCount <= 3) ? 'refresh' : 'nuclear';
                    history.lastMethod = method;
                    this.recentFailures.set(cameraId, history);

                    // Exponential backoff: 5s, 10s, 20s, 40s, 60s (max)
                    const delay = Math.min(5000 * Math.pow(2, attempts), 60000);

                    const methodLabel = method === 'refresh' ? 'Refresh' : 'Nuclear Stop+Start';
                    console.log(`[Health] ${cameraId}: Scheduling ${methodLabel} restart ${attempts + 1}/${maxAttempts > 0 ? maxAttempts : '∞'} in ${delay / 1000}s (failures in 60s: ${recentFailureCount})`);
                    this.setStreamStatus($streamItem, 'loading', `${methodLabel} retry ${attempts + 1} in ${delay / 1000}s`);

                    // Increment counter
                    this.restartAttempts.set(cameraId, attempts + 1);

                    // Clear existing timer
                    if (this.restartTimers.has(cameraId)) {
                        clearTimeout(this.restartTimers.get(cameraId));
                    }

                    // Schedule restart with appropriate method
                    const timer = setTimeout(async () => {
                        this.restartTimers.delete(cameraId);
                        console.log(`[Health] ${cameraId}: Executing ${methodLabel} attempt ${attempts + 1}`);

                        if (method === 'refresh') {
                            // Standard refresh - works for transient issues
                            await this.restartStream(cameraId, $streamItem);
                        } else {
                            await this.nuclear(cameraId, streamItem, cameraType, streamType)
                        }
                    }, delay);

                    this.restartTimers.set(cameraId, timer);
                }
            });
            console.log("UI HEALTH CHECK ENABLED");
        } else {
            this.health = null;  // Set to null so we know it's disabled
            console.log("UI HEALTH CHECK DISABLED");
        }

        this.init();
    }

    init() {
        console.log('========== MultiStreamManager INIT() CALLED ==========');
        this.setupLayout();
        this.setupEventListeners();
        this.updateStreamCount();

        // Start all streams (fire and forget - don't await)
        console.log('[Init] Starting streams in background...');
        this.startAllStreams().then(() => {
            console.log('[Init] All streams completed');
        }).catch(err => {
            console.error('[Init] Stream loading error:', err);
        });

        // Restore fullscreen independently after short delay
        // Just needs DOM to be ready, doesn't need streams loaded
        setTimeout(() => {
            console.log('[Init] Attempting fullscreen restore...');
            this.restoreFullscreenFromLocalStorage();
        }, 1000);  // Reduced from 3000ms - just needs DOM ready
    }

    async nuclear(cameraId, streamItem, cameraType, streamType) {
        // Nuclear option - forces backend to restart FFmpeg
        console.log(`[Health] ${cameraId}: Nuclear recovery - forcing UI stop+start cycle`);

        // Step 1: UI stop (client-side only, no backend call)
        await this.stopIndividualStream(cameraId, $streamItem, cameraType, streamType);

        // Step 2: Wait for backend to notice stream is gone
        await new Promise(r => setTimeout(r, 3000));

        // Step 3: UI start (forces backend to create new FFmpeg)
        this.setStreamStatus($streamItem, 'loading', 'Nuclear restart...');
        const success = await this.startStream(cameraId, $streamItem, cameraType, streamType);

        if (success) {
            // Success path already handled in startStream
            console.log(`[Health] ${cameraId}: Nuclear restart succeeded`);
            // Clear failure history on success
            this.recentFailures.delete(cameraId);
            this.restartAttempts.delete(cameraId);
        } else {
            console.error(`[Health] ${cameraId}: Nuclear restart failed`);
        }
    }

    setupLayout() {
        const $streamItems = this.$container.find('.stream-item');
        const count = $streamItems.length;

        // Calculate optimal grid layout
        let cols;
        if (count <= 1) cols = 1;
        else if (count <= 4) cols = 2;
        else if (count <= 9) cols = 3;
        else if (count <= 16) cols = 4;
        else cols = 5;

        this.$container.attr('class', `streams-container grid-${cols}`);

        // Set aspect ratio for each stream
        $streamItems.css('aspect-ratio', '16/9');
    }

    setupEventListeners() {
        // Fullscreen button click handler - uses event namespace for clean binding
        // IMPORTANT: Ensure only ONE MultiStreamManager instance exists. This class auto-instantiates
        // at the bottom of this file via $(document).ready(). Do NOT create additional instances in
        // HTML inline scripts or other modules.
        // 
        // To verify single instance: console.log($._data($('#streams-container')[0], 'events'))
        // should show only ONE handler per selector.
        // 
        // HISTORICAL NOTE: Previously used .off() as safety net to remove duplicate handlers:
        // this.$container.off('click.fullscreen', '.stream-fullscreen-btn');
        // This was masking the root cause (duplicate instantiation in streams.html inline scripts).
        // Now fixed - module handles its own instantiation, HTML just imports it.

        // Add single handler with namespace
        this.$container.on('click.fullscreen', '.stream-fullscreen-btn', async (e) => {
            e.stopPropagation();
            e.preventDefault();

            // // Global lock to prevent ANY instance from processing simultaneously
            // if (window._fullscreenLocked) {
            //     console.log('[Fullscreen] BLOCKED - global lock active');
            //     return;
            // }

            // Simple debounce - ignore rapid clicks
            if (this._fullscreenProcessing) {
                console.log('[Fullscreen] Processing - ignoring rapid click');
                return;
            }

            this._fullscreenProcessing = true;

            window._fullscreenLocked = true;
            console.log('[Fullscreen] Lock acquired');

            try {
                // Check current state
                const isCurrentlyFullscreen = $('.stream-item.css-fullscreen').length > 0;
                console.log(`[Fullscreen] Button clicked - state: ${isCurrentlyFullscreen ? 'FULLSCREEN' : 'GRID'}`);

                if (isCurrentlyFullscreen) {
                    // Exit
                    await this.closeFullscreen();
                    console.log('[Fullscreen] Exit complete');
                } else {
                    // Enter
                    const $streamItem = $(e.target).closest('.stream-item');
                    if (!$streamItem.length) return;

                    const cameraId = $streamItem.data('camera-serial');
                    const name = $streamItem.data('camera-name');
                    const cameraType = $streamItem.data('camera-type');
                    const streamType = $streamItem.data('stream-type');

                    await this.openFullscreen(cameraId, name, cameraType, streamType);
                    console.log('[Fullscreen] Enter complete');
                }

            } catch (error) {
                console.error('[Fullscreen] Toggle error:', error);
            } finally {
                // // Release lock after 1 second delay
                // setTimeout(() => {
                //     window._fullscreenLocked = false;
                //     console.log('[Fullscreen] Lock released');
                // }, 1000);
                // Release processing flag
                setTimeout(() => {
                    this._fullscreenProcessing = false;
                }, 300);  // Shorter delay since no race conditions
            }
        });

        // Global control handlers
        $('#start-all-streams').on('click', () => {
            this.startAllStreams();
        });

        $('#stop-all-streams').on('click', () => {
            this.stopAllStreams();
        });

        // Individual stream control handlers
        this.$container.on('click', '.start-stream-btn', (e) => {
            e.stopPropagation();
            const $streamItem = $(e.target).closest('.stream-item');
            const cameraId = $streamItem.data('camera-serial');
            const cameraType = $streamItem.data('camera-type');
            const streamType = $streamItem.data('stream-type');
            this.startStream(cameraId, $streamItem, cameraType, streamType);
        });

        this.$container.on('click', '.stop-stream-btn', (e) => {
            e.stopPropagation();
            const $streamItem = $(e.target).closest('.stream-item');
            const cameraId = $streamItem.data('camera-serial');
            const cameraType = $streamItem.data('camera-type');
            const streamType = $streamItem.data('stream-type');
            this.stopIndividualStream(cameraId, $streamItem, cameraType, streamType);
        });

        // PTZ control handlers
        this.$container.on('click', '.ptz-btn', (e) => {
            e.stopPropagation();
            const $button = $(e.target);
            const direction = $button.data('direction');
            const $streamItem = $button.closest('.stream-item');

            if (direction && $streamItem.length) {
                const cameraSerial = $streamItem.data('camera-serial');
                this.executePTZ(cameraSerial, direction, $button);
            }
        });

        // Refresh stream handler
        this.$container.on('click', '.refresh-stream-btn', (e) => {
            e.stopPropagation();
            const $streamItem = $(e.target).closest('.stream-item');
            const cameraId = $streamItem.data('camera-serial');
            const streamType = $streamItem.data('stream-type');
            const videoElement = $streamItem.find('.stream-video')[0];

            // Only HLS streams can be force-refreshed (for now)
            // not adding this condition will make the system 
            // create a new rtsp stream while rtmp witll still 
            // be running. 
            if (streamType === 'HLS' || streamType === 'LL_HLS' || streamType === 'NEOLINK' || streamType === 'NEOLINK_LL_HLS') {
                this.hlsManager.forceRefreshStream(cameraId, videoElement);
            }
        });

        // ESC key to exit CSS fullscreen
        $(document).on('keydown', (e) => {
            if (e.key === 'Escape') {
                const $fullscreenItem = $('.stream-item.css-fullscreen');
                if ($fullscreenItem.length > 0) {
                    console.log('[Fullscreen] ESC key pressed, exiting fullscreen');
                    this.closeFullscreen();
                }
            }
        });
    }

    async startAllStreams() {
        console.log('[StartAll] Beginning startAllStreams...');
        const $streamItems = this.$container.find('.stream-item');
        console.log(`[StartAll] Found ${$streamItems.length} stream items`);

        // Start all streams in parallel instead of sequential
        const startPromises = $streamItems.toArray().map(async (item, index) => {
            const $item = $(item);
            const cameraId = $item.data('camera-serial');
            const cameraType = $item.data('camera-type');
            const streamType = $item.data('stream-type');

            console.log(`[StartAll] Starting stream ${index + 1}/${$streamItems.length}: ${cameraId}`);

            try {
                await this.startStream(cameraId, $item, cameraType, streamType);
                console.log(`[StartAll] ✓ Stream ${index + 1} started: ${cameraId}`);
            } catch (error) {
                console.error(`[StartAll] ✗ Stream ${index + 1} failed: ${cameraId}`, error);
                this.setStreamStatus($item, 'error', 'Failed to load');
            }
        });

        console.log(`[StartAll] Waiting for ${startPromises.length} promises...`);
        await Promise.allSettled(startPromises);
        console.log('[StartAll] ✓✓✓ ALL STREAMS COMPLETE ✓✓✓');
    }

    async startStream(cameraId, $streamItem, cameraType, streamType) {
        const streamElement = $streamItem.find('.stream-video')[0];
        const $loadingIndicator = $streamItem.find('.loading-indicator');

        try {
            $loadingIndicator.show();
            this.setStreamStatus($streamItem, 'loading', 'Starting...');

            let success;

            // Use streamType to determine which manager to use
            // NOTE: mjpeg_proxy is only for direct access to UNIFI MJPEG streams (when not using Protect)
            if (streamType === 'MJPEG' || streamType === 'mjpeg_proxy') {
                success = await this.mjpegManager.startStream(cameraId, streamElement, cameraType);
            } else if (streamType === 'HLS' || streamType === 'LL_HLS' || streamType === 'NEOLINK' || streamType === 'NEOLINK_LL_HLS') {
                success = await this.hlsManager.startStream(cameraId, streamElement, 'sub');
            } else if (streamType === 'RTMP') {
                success = await this.flvManager.startStream(cameraId, streamElement);
            } else {
                throw new Error(`Unknown stream type: ${streamType}`);
            }

            if (success) {
                $loadingIndicator.hide();
                this.setStreamStatus($streamItem, 'live', 'Live');
                this.updateStreamButtons($streamItem, true);

                // Reset restart counter on successful start
                this.restartAttempts.delete(cameraId);
                if (this.restartTimers.has(cameraId)) {
                    clearTimeout(this.restartTimers.get(cameraId));
                    this.restartTimers.delete(cameraId);
                }

                // Attach health monitor (refactored)
                this.attachHealthMonitor(cameraId, $streamItem, streamType);
            }
        } catch (error) {
            $loadingIndicator.hide();
            this.setStreamStatus($streamItem, 'error', 'Failed');
            this.updateStreamButtons($streamItem, false);
            console.error(`Stream start failed for ${cameraId}:`, error);

            // CRITICAL: Attach health monitor even on initial failure so it can retry
            this.attachHealthMonitor(cameraId, $streamItem, streamType);
        }
    }

    async stopIndividualStream(cameraId, $streamItem, cameraType, streamType) {
        try {
            let success;

            // Use streamType to determine which manager to use
            if (streamType === 'MJPEG' || streamType === 'mjpeg_proxy') {
                success = this.mjpegManager.stopStream(cameraId);
            } else if (streamType === 'HLS' || streamType === 'LL_HLS' || streamType === 'NEOLINK' || streamType === 'NEOLINK_LL_HLS') {
                success = await this.hlsManager.stopStream(cameraId);
            } else if (streamType === 'RTMP') {
                success = this.flvManager.stopStream(cameraId);
            }

            if (success) {
                const $streamElement = $streamItem.find('.stream-video');
                $streamElement.attr('src', '');
                this.setStreamStatus($streamItem, 'loading', 'Stopped');
                this.updateStreamButtons($streamItem, false);
                const el = $streamItem.find('.stream-video')[0];
                if (el && el._healthDetach) { el._healthDetach(); delete el._healthDetach; }

            }
        } catch (error) {
            console.error(`Failed to stop stream for ${cameraId}:`, error);
        }
    }

    async stopAllStreams() {
        try {
            // Stop both manager types in parallel
            const stopPromises = [
                this.mjpegManager.stopAllStreams(),
                this.hlsManager.stopAllStreams(),
                this.flvManager.stopAllStreams()
            ];

            await Promise.allSettled(stopPromises);

            // Update UI for all streams
            const $streamItems = this.$container.find('.stream-item');
            $streamItems.each((index, item) => {
                const $item = $(item);
                const $streamElement = $item.find('.stream-video');
                $streamElement.attr('src', '');
                this.setStreamStatus($item, 'loading', 'Stopped');
                this.updateStreamButtons($item, false);

            });
            // also detach any existing monitors:
            this.$container.find('.stream-video').each((_, v) => {
                if (v._healthDetach) { v._healthDetach(); delete v._healthDetach; }
            });

        } catch (error) {
            console.error('Failed to stop all streams:', error);
        }
    }

    /**
     * Restart a stream that has become unhealthy or frozen
     * 
     * This method is typically called by the health monitor when a stream is detected
     * as stale (no new frames) or displaying a black screen. It handles the complete
     * restart lifecycle:
     * 
     * 1. Detaches health monitor to prevent duplicate monitoring during restart
     * 2. Dispatches to stream-type-specific restart method (HLS/MJPEG/RTMP)
     * 3. Updates UI status to 'live' on success
     * 4. Reattaches health monitor (whether success or failure)
     * 
     * The health monitor is ALWAYS reattached after restart (success or failure) to
     * ensure continuous monitoring and automatic retry attempts. Without reattachment,
     * a failed restart would leave the stream permanently stuck with no recovery path.
     * 
     * @param {string} cameraId - Camera serial number (e.g., 'T8416P0023352DA9')
     * @param {jQuery} $streamItem - jQuery-wrapped DOM element for the stream container
     * 
     * @throws {Error} Caught internally - errors logged but health monitor reattached
     * 
     * @example
     * // Called automatically by health monitor
     * await this.restartStream('REOLINK_LAUNDRY', $('.stream-item[data-camera-serial="REOLINK_LAUNDRY"]'));
     */
    async restartStream(cameraId, $streamItem) {
        try {
            console.log(`[Restart] ${cameraId}: Beginning restart sequence`);
            this.updateStreamButtons($streamItem, true);
            this.setStreamStatus($streamItem, 'loading', 'Restarting...');

            const cameraType = $streamItem.data('camera-type');
            const streamType = $streamItem.data('stream-type');
            const videoElement = $streamItem.find('.stream-video')[0];

            // Detach health monitor during restart
            if (videoElement && videoElement._healthDetach) {
                videoElement._healthDetach();
                delete videoElement._healthDetach;
            }

            // Dispatch to appropriate restart method
            if (streamType === 'HLS' || streamType === 'LL_HLS' || streamType === 'NEOLINK' || streamType === 'NEOLINK_LL_HLS') {
                await this.restartHLSStream(cameraId, videoElement);
            } else if (streamType === 'MJPEG' || streamType === 'mjpeg_proxy') {
                await this.restartMJPEGStream(cameraId, $streamItem, cameraType, streamType);
            } else if (streamType === 'RTMP') {
                await this.restartRTMPStream(cameraId, $streamItem, cameraType, streamType);
            }



            // Success: update status and reattach health
            this.setStreamStatus($streamItem, 'live', 'Live');
            this.attachHealthMonitor(cameraId, $streamItem, streamType);

            console.log(`[Restart] ${cameraId}: Restart complete`);

        } catch (e) {
            console.error(`[Restart] ${cameraId}: Failed`, e);
            this.setStreamStatus($streamItem, 'error', 'Restart failed');
            // const cameraType = $streamItem.data('camera-type');
            // const streamType = $streamItem.data('stream-type');
            // await this.nuclear(cameraId, streamItem, cameraType, streamType)

            // Reattach health even on failure so it can retry
            this.attachHealthMonitor(cameraId, $streamItem, streamType);
        }
    }

    /**
 * Restart HLS/LL-HLS stream by destroying and recreating HLS.js instance
 */
    async restartHLSStream(cameraId, videoElement) {
        await this.hlsManager.forceRefreshStream(cameraId, videoElement);
    }

    /**
     * Restart MJPEG stream by stopping and restarting
     */
    async restartMJPEGStream(cameraId, $streamItem, cameraType, streamType) {
        await this.stopIndividualStream(cameraId, $streamItem, cameraType, streamType);
        await new Promise(r => setTimeout(r, 1500));
        await this.startStream(cameraId, $streamItem, cameraType, streamType);
    }

    /**
     * Restart RTMP stream by destroying and recreating FLV player
     */
    async restartRTMPStream(cameraId, $streamItem, cameraType, streamType) {
        this.flvManager.stopStream(cameraId);
        await new Promise(r => setTimeout(r, 500));
        const success = await this.startStream(cameraId, $streamItem, cameraType, streamType);

        // Give element time to start playing
        await new Promise(r => setTimeout(r, 800));

        const el = $streamItem.find('.stream-video')[0];
        if (success && el && el.readyState >= 2 && !el.paused) {
            return true;
        } else if (success) {
            this.setStreamStatus($streamItem, 'loading', 'Buffering…');
            return false;
        }
        return false;
    }

    /**
     * Attach health monitor to a stream element
     * Centralizes health attachment logic to avoid repetition
     */
    attachHealthMonitor(cameraId, $streamItem, streamType) {
        if (!this.health) {
            console.log(`[Health] Monitoring disabled for ${cameraId}`);
            return;
        }

        const el = $streamItem.find('.stream-video')[0];
        if (!el) {
            console.warn(`[Health] No video element found for ${cameraId}`);
            return;
        }

        console.log(`[Health] Attaching monitor for ${cameraId} (${streamType})`);

        if (streamType === 'HLS' || streamType === 'LL_HLS' || streamType === 'NEOLINK' || streamType === 'NEOLINK_LL_HLS') {
            const hls = this.hlsManager?.hlsInstances?.get?.(cameraId) || null;
            el._healthDetach = this.health.attachHls(cameraId, el, hls);
        } else if (streamType === 'RTMP') {
            const flv = this.flvManager?.flvInstances?.get?.(cameraId) || null;
            el._healthDetach = this.health.attachRTMP(cameraId, el, flv);
        } else if (streamType === 'MJPEG' || streamType === 'mjpeg_proxy') {
            el._healthDetach = this.health.attachMjpeg(cameraId, el);
        }
    }


    updateStreamButtons($streamItem, isStreaming) {
        const $startBtn = $streamItem.find('.start-stream-btn');
        const $stopBtn = $streamItem.find('.stop-stream-btn');
        const $ptzBtns = $streamItem.find('.ptz-btn');

        $startBtn.prop('disabled', isStreaming);
        $stopBtn.prop('disabled', !isStreaming);

        // PTZ works regardless of streaming
        $ptzBtns.prop('disabled', false);
    }

    async executePTZ(cameraSerial, direction, $button) {
        try {
            // Visual feedback
            $button.css('background', 'rgba(37, 99, 235, 1)');
            $button.prop('disabled', true);

            const response = await fetch(`/api/ptz/${cameraSerial}/${direction}`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' }
            });

            const result = await response.json();

            if (!result.success) {
                console.error('PTZ command failed:', result.error);
            }

        } catch (error) {
            console.error('PTZ command error:', error);
        } finally {
            // Reset button after delay
            setTimeout(() => {
                $button.css('background', '');
                $button.prop('disabled', false);
            }, 1000);
        }
    }

    setStreamStatus($streamItem, status, text) {
        const $indicator = $streamItem.find('.stream-indicator');
        const $statusText = $indicator.find('span');

        if ($indicator.length) {
            $indicator.attr('class', `stream-indicator ${status}`);
        }
        if ($statusText.length) {
            $statusText.text(text);
        }
    }

    async openFullscreen(cameraId, name, cameraType, streamType) {
        try {
            // Prevent opening if already in fullscreen
            if ($('.stream-item.css-fullscreen').length > 0) {
                console.log('[Fullscreen] Already in fullscreen mode, ignoring');
                return;
            }

            console.log(`[Fullscreen] Opening CSS fullscreen for camera: ${name} (${cameraId})`);

            // Find the stream-item element for this camera
            const $streamItem = $(`.stream-item[data-camera-serial="${cameraId}"]`);

            if ($streamItem.length === 0) {
                throw new Error(`Stream item not found for camera: ${cameraId}`);
            }

            // Apply CSS fullscreen class IMMEDIATELY
            $streamItem.addClass('css-fullscreen');
            localStorage.setItem('fullscreenCameraSerial', cameraId);
            console.log('[Fullscreen] CSS fullscreen activated immediately');

            // Pause (not stop) all other streams
            this.pausedStreams = [];
            const $allStreamItems = $('.stream-item');

            for (let i = 0; i < $allStreamItems.length; i++) {
                const $item = $($allStreamItems[i]);
                const id = $item.data('camera-serial');

                if (id === cameraId) continue; // Skip the fullscreen camera

                const $video = $item.find('.stream-video');
                const videoEl = $video[0];
                const streamType = $item.data('stream-type');

                // Pause HLS streams using HLS.js API
                if (streamType === 'HLS' || streamType === 'LL_HLS' || streamType === 'NEOLINK' || streamType === 'NEOLINK_LL_HLS') {
                    const hls = this.hlsManager.hlsInstances.get(id);
                    if (hls && videoEl) {
                        console.log(`[Fullscreen] Pausing HLS stream: ${id}`);
                        hls.stopLoad(); // Stop fetching segments
                        videoEl.pause(); // Stop video decoder

                        // Detach health monitor for paused stream
                        if (videoEl._healthDetach) {
                            videoEl._healthDetach();
                            delete videoEl._healthDetach;
                        }

                        this.pausedStreams.push({ id, type: 'HLS' });
                    }
                }
                // Pause RTMP streams
                else if (streamType === 'RTMP') {
                    if (videoEl) {
                        console.log(`[Fullscreen] Pausing RTMP stream: ${id}`);
                        videoEl.pause();

                        // Detach health monitor for paused stream
                        if (videoEl._healthDetach) {
                            videoEl._healthDetach();
                            delete videoEl._healthDetach;
                        }

                        this.pausedStreams.push({ id, type: 'RTMP' });
                    }
                }
                // Pause MJPEG by stopping image updates
                else if (streamType === 'MJPEG' || streamType === 'mjpeg_proxy') {
                    const imgEl = $video[0];
                    if (imgEl && imgEl.src) {
                        console.log(`[Fullscreen] Pausing MJPEG stream: ${id}`);
                        imgEl._pausedSrc = imgEl.src; // Store src
                        imgEl.src = ''; // Clear to stop fetching

                        // Detach health monitor for paused stream
                        if (imgEl._healthDetach) {
                            imgEl._healthDetach();
                            delete imgEl._healthDetach;
                        }

                        this.pausedStreams.push({ id, type: 'MJPEG' });
                    }
                }
            }

            console.log(`[Fullscreen] Paused ${this.pausedStreams.length} streams (still alive, no backend impact)`);

        } catch (error) {
            console.error('[Fullscreen] Failed to open fullscreen:', error);
        }
    }

    async closeFullscreen() {
        try {
            console.log('[Fullscreen] Closing CSS fullscreen...');

            // Find the fullscreen stream item
            const $fullscreenItem = $('.stream-item.css-fullscreen');

            if ($fullscreenItem.length === 0) {
                console.log('[Fullscreen] No fullscreen stream found');
                return;
            }

            // Remove CSS fullscreen class
            $fullscreenItem.removeClass('css-fullscreen');
            console.log('[Fullscreen] CSS fullscreen class removed');

            // Clear localStorage
            localStorage.removeItem('fullscreenCameraSerial');
            console.log('[Fullscreen] Cleared localStorage');

            // Resume previously paused streams
            if (this.pausedStreams && this.pausedStreams.length > 0) {
                console.log(`[Fullscreen] Resuming ${this.pausedStreams.length} paused streams...`);

                for (const stream of this.pausedStreams) {
                    const $item = $(`.stream-item[data-camera-serial="${stream.id}"]`);
                    if (!$item.length) continue;

                    const $video = $item.find('.stream-video');
                    const videoEl = $video[0];
                    const streamType = $item.data('stream-type');

                    if (stream.type === 'HLS') {
                        const hls = this.hlsManager.hlsInstances.get(stream.id);
                        if (hls && videoEl) {
                            console.log(`[Fullscreen] Resuming HLS stream: ${stream.id}`);
                            hls.startLoad(); // Resume fetching segments
                            videoEl.play().catch(e => console.log(`[Fullscreen] Play blocked for ${stream.id}:`, e));

                            // Reattach health monitor
                            this.attachHealthMonitor(stream.id, $item, streamType);
                        }
                    }
                    else if (stream.type === 'RTMP') {
                        if (videoEl) {
                            console.log(`[Fullscreen] Resuming RTMP stream: ${stream.id}`);
                            videoEl.play().catch(e => console.log(`[Fullscreen] Play blocked for ${stream.id}:`, e));

                            // Reattach health monitor
                            this.attachHealthMonitor(stream.id, $item, streamType);
                        }
                    }
                    else if (stream.type === 'MJPEG') {
                        const imgEl = $video[0];
                        if (imgEl && imgEl._pausedSrc) {
                            console.log(`[Fullscreen] Resuming MJPEG stream: ${stream.id}`);
                            imgEl.src = imgEl._pausedSrc; // Restore src to resume fetching
                            delete imgEl._pausedSrc;

                            // Reattach health monitor
                            this.attachHealthMonitor(stream.id, $item, streamType);
                        }
                    }
                }

                this.pausedStreams = [];
                console.log('[Fullscreen] All streams resumed');
            }

        } catch (error) {
            console.error('[Fullscreen] Error closing fullscreen:', error);
        }
    }

    async restoreFullscreenFromLocalStorage() {
        const savedCameraId = localStorage.getItem('fullscreenCameraSerial');

        if (!savedCameraId) {
            console.log('[Fullscreen] No saved fullscreen camera found');
            return;
        }

        console.log(`[Fullscreen] Found saved camera in localStorage: ${savedCameraId}`);
        console.log('[Fullscreen] Restoring CSS fullscreen (no user interaction required)...');

        // Find the stream-item
        const $streamItem = $(`.stream-item[data-camera-serial="${savedCameraId}"]`);

        if ($streamItem.length === 0) {
            console.warn(`[Fullscreen] Saved camera ${savedCameraId} not found in DOM`);
            localStorage.removeItem('fullscreenCameraSerial');
            return;
        }

        const name = $streamItem.data('camera-name');
        const cameraType = $streamItem.data('camera-type');
        const streamType = $streamItem.data('stream-type');

        // Restore CSS fullscreen immediately (no user gesture needed!)
        await this.openFullscreen(savedCameraId, name, cameraType, streamType);

        console.log('[Fullscreen] CSS fullscreen restored successfully');
    }

    updateStreamCount() {
        const count = this.$container.find('.stream-item').length;
        this.$streamCount.text(`${count} camera${count !== 1 ? 's' : ''}`);
    }
}

// Initialize when page loads
$(document).ready(() => {
    new MultiStreamManager();
});
