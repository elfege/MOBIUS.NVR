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
        // this.$fullscreenOverlay = $('#fullscreen-overlay'); // DEPRECATED
        // this.$fullscreenVideo = $('#fullscreen-video'); // DEPRECATED
        // this.$fullscreenTitle = $('#fullscreen-title'); // DEPRECATED
        // this.$fullscreenClose = $('#fullscreen-close'); // DEPRECATED

        // this.fullscreenHls = null;

        this.restartAttempts = new Map(); // Track restart attempts per camera
        this.restartTimers = new Map();   // Track pending retry timers

        const H = window.UI_HEALTH || {};
        console.log(`UI HEALTH ENV: ${JSON.stringify(H)}`)
        // Only create health monitor if enabled
        if (H.uiHealthEnabled) {
            this.health = makeHealthMonitor({
                blankThreshold: H.blankThreshold || { avg: 12, std: 5 },
                sampleIntervalMs: H.sampleIntervalMs ?? 6000,
                staleAfterMs: H.staleAfterMs ?? 20000,
                consecutiveBlankNeeded: H.consecutiveBlankNeeded ?? 10,
                cooldownMs: H.cooldownMs ?? 60000,
                warmupMs: H.warmupMs ?? 60000,
                onUnhealthy: async ({ cameraId, reason, metrics }) => {
                    console.warn(`[Health] Stream unhealthy: ${cameraId}, reason: ${reason}`, metrics);

                    const $streamItem = $(`.stream-item[data-camera-serial="${cameraId}"]`);
                    if (!$streamItem.length) return;

                    // Get restart attempt count
                    const attempts = this.restartAttempts.get(cameraId) || 0;
                    const maxAttempts = 10;

                    if (attempts >= maxAttempts) {
                        console.error(`[Health] ${cameraId}: Max restart attempts (${maxAttempts}) reached`);
                        this.setStreamStatus($streamItem, 'failed', `Failed after ${maxAttempts} attempts`);
                        return;
                    }

                    // Exponential backoff: 5s, 10s, 20s, 40s, 60s (max)
                    const delay = Math.min(5000 * Math.pow(2, attempts), 60000);

                    console.log(`[Health] ${cameraId}: Scheduling restart ${attempts + 1}/${maxAttempts} in ${delay / 1000}s`);
                    this.setStreamStatus($streamItem, 'loading', `Retry ${attempts + 1} in ${delay / 1000}s`);

                    // Increment counter
                    this.restartAttempts.set(cameraId, attempts + 1);

                    // Clear existing timer
                    if (this.restartTimers.has(cameraId)) {
                        clearTimeout(this.restartTimers.get(cameraId));
                    }

                    // Schedule restart
                    const timer = setTimeout(async () => {
                        this.restartTimers.delete(cameraId);
                        console.log(`[Health] ${cameraId}: Executing restart attempt ${attempts + 1}`);
                        await this.restartStream(cameraId, $streamItem);
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

    // This method was only used for the custom overlay. 
    // The latency badge is now managed by hls-stream.js and will automatically appear in native fullscreen mode.Retry
    // _attachFullscreenLatencyMeter(hls, videoEl) {
    //     if (!hls || !videoEl) return;
    //     // reuse badge if created
    //     if (!videoEl._fsLatencyOverlay) {
    //         const badge = document.createElement('div');
    //         Object.assign(badge.style, {
    //             position: 'absolute',
    //             right: '16px',
    //             top: '16px',
    //             padding: '4px 8px',
    //             fontSize: '14px',
    //             lineHeight: '18px',
    //             background: 'rgba(0,0,0,0.6)',
    //             color: '#fff',
    //             borderRadius: '8px',
    //             fontFamily: 'system-ui, -apple-system, Segoe UI, Roboto, sans-serif',
    //             pointerEvents: 'none',
    //             zIndex: 20,
    //         });
    //         // fullscreen overlay is already a flex container; ensure relative
    //         const overlay = this.$fullscreenOverlay[0];
    //         overlay.style.position = overlay.style.position || 'relative';
    //         overlay.appendChild(badge);
    //         videoEl._fsLatencyOverlay = badge;
    //     }

    //     const overlay = videoEl._fsLatencyOverlay;
    //     videoEl._fsLastPdtMs = null;

    //     const onFrag = (_, data) => {
    //         const pdt = data?.frag?.programDateTime;
    //         if (pdt != null) {
    //             videoEl._fsLastPdtMs = typeof pdt === 'number' ? pdt : new Date(pdt).getTime();
    //         }
    //     };

    //     hls.on(Hls.Events.FRAG_CHANGED, onFrag);

    //     if (videoEl._fsLatencyTimer) clearInterval(videoEl._fsLatencyTimer);
    //     videoEl._fsLatencyTimer = setInterval(() => {
    //         if (!videoEl._fsLastPdtMs) return;
    //         const s = ((Date.now() - videoEl._fsLastPdtMs) / 1000).toFixed(1);
    //         overlay.textContent = `${s}s`;
    //         overlay.style.display = '';
    //     }, 250);

    //     videoEl._fsLatencyDetach = () => {
    //         hls.off(Hls.Events.FRAG_CHANGED, onFrag);
    //         if (videoEl._fsLatencyTimer) { clearInterval(videoEl._fsLatencyTimer); videoEl._fsLatencyTimer = null; }
    //         if (videoEl._fsLatencyOverlay) { videoEl._fsLatencyOverlay.textContent = ''; }
    //         videoEl._fsLastPdtMs = null;
    //     };
    // }

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

                const el = $streamItem.find('.stream-video')[0];

                if ((streamType === 'HLS' || streamType === 'LL_HLS' || streamType === 'NEOLINK' || streamType === 'NEOLINK_LL_HLS') && this.health) {
                    const hls = this.hlsManager?.hlsInstances?.get?.(cameraId) || null;
                    el._healthDetach = this.health.attachHls(cameraId, el, hls);
                } else if (streamType === 'RTMP' && this.health) {
                    const flv = this.flvManager?.flvInstances?.get?.(cameraId) || null;
                    el._healthDetach = this.health.attachRTMP(cameraId, el, flv);
                } else if (streamType === 'MJPEG' || streamType === 'mjpeg_proxy') {
                    el._healthDetach = this.health.attachMjpeg(cameraId, el);
                }
            }



        } catch (error) {
            $loadingIndicator.hide();
            this.setStreamStatus($streamItem, 'error', 'Failed');
            this.updateStreamButtons($streamItem, false);
            console.error(`Stream start failed for ${cameraId}:`, error);
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

    async restartStream(cameraId, $streamItem) {
        /** What this does:

        - For HLS streams: calls forceRefreshStream() which destroys the HLS.js instance and clears its cache
        - This fixes the "media sequence mismatch" error by forcing HLS.js to reload everything fresh
        - For MJPEG streams: keeps the old stop+start logic (no cache to worry about)
        - Properly detaches health monitor before restart to avoid duplicate monitoring
        */
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

            // Use the proper refresh method based on stream type
            if (streamType === 'HLS' || streamType === 'LL_HLS' || streamType === 'NEOLINK' || streamType === 'NEOLINK_LL_HLS') {
                // ← CHANGED: Call forceRefreshStream which destroys HLS.js cache
                await this.hlsManager.forceRefreshStream(cameraId, videoElement);
                this.setStreamStatus($streamItem, 'live', 'Live');
            } else if (streamType === 'MJPEG' || streamType === 'mjpeg_proxy') {
                // MJPEG doesn't have cache issues, just restart normally
                await this.stopIndividualStream(cameraId, $streamItem, cameraType, streamType);
                await new Promise(r => setTimeout(r, 1500));
                await this.startStream(cameraId, $streamItem, cameraType, streamType);
            } else if (streamType === 'RTMP') {
                // Fully tear down and recreate the flv.js player
                this.flvManager.stopStream(cameraId);
                await new Promise(r => setTimeout(r, 500));
                const ok = await this.startStream(cameraId, $streamItem, cameraType, streamType);

                // Explicitly reconcile UI status for RTMP
                const el = $streamItem.find('.stream-video')[0];
                // Give the element a brief moment to attach and begin
                await new Promise(r => setTimeout(r, 800));

                if (ok && el && el.readyState >= 2 && !el.paused) {
                    this.setStreamStatus($streamItem, 'live', 'Live');
                } else if (ok) {
                    // Fallback: start succeeded, but element not “playing” yet; still clear failure
                    this.setStreamStatus($streamItem, 'loading', 'Buffering…');
                }
            }

            console.log(`[Restart] ${cameraId}: Restart complete`);
        } catch (e) {
            console.error(`[Restart] ${cameraId}: Failed`, e);
            this.setStreamStatus($streamItem, 'error', 'Restart failed');
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
                        this.pausedStreams.push({ id, type: 'HLS' });
                    }
                }
                // Pause RTMP streams
                else if (streamType === 'RTMP') {
                    if (videoEl) {
                        console.log(`[Fullscreen] Pausing RTMP stream: ${id}`);
                        videoEl.pause();
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

                    if (stream.type === 'HLS') {
                        const hls = this.hlsManager.hlsInstances.get(stream.id);
                        if (hls && videoEl) {
                            console.log(`[Fullscreen] Resuming HLS stream: ${stream.id}`);
                            hls.startLoad(); // Resume fetching segments
                            videoEl.play().catch(e => console.log(`[Fullscreen] Play blocked for ${stream.id}:`, e));
                        }
                    }
                    else if (stream.type === 'RTMP') {
                        if (videoEl) {
                            console.log(`[Fullscreen] Resuming RTMP stream: ${stream.id}`);
                            videoEl.play().catch(e => console.log(`[Fullscreen] Play blocked for ${stream.id}:`, e));
                        }
                    }
                    else if (stream.type === 'MJPEG') {
                        const imgEl = $video[0];
                        if (imgEl && imgEl._pausedSrc) {
                            console.log(`[Fullscreen] Resuming MJPEG stream: ${stream.id}`);
                            imgEl.src = imgEl._pausedSrc; // Restore src to resume fetching
                            delete imgEl._pausedSrc;
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
