/**
 * Multi-Stream Manager - ES6 + jQuery
 * Orchestrates HLS and MJPEG stream managers for unified camera viewing
 */

import { FLVStreamManager } from './flv-stream.js';
import { makeHealthMonitor } from './health.js';
import { HLSStreamManager } from './hls-stream.js';
import { MJPEGStreamManager } from './mjpeg-stream.js';

export class MultiStreamManager {
    constructor() {
        this.mjpegManager = new MJPEGStreamManager();
        this.hlsManager = new HLSStreamManager();
        this.flvManager = new FLVStreamManager();

        // Cache jQuery selectors
        this.$container = $('#streams-container');
        this.$streamCount = $('#stream-count');
        this.$fullscreenOverlay = $('#fullscreen-overlay');
        this.$fullscreenVideo = $('#fullscreen-video');
        this.$fullscreenTitle = $('#fullscreen-title');
        this.$fullscreenClose = $('#fullscreen-close');

        this.fullscreenHls = null;

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
                onUnhealthy: async ({ serial, reason, metrics }) => {
                    console.warn(`[Health] Stream unhealthy: ${serial}, reason: ${reason}`, metrics);

                    const $streamItem = $(`.stream-item[data-camera-serial="${serial}"]`);
                    if (!$streamItem.length) return;

                    // Get restart attempt count
                    const attempts = this.restartAttempts.get(serial) || 0;
                    const maxAttempts = 10;

                    if (attempts >= maxAttempts) {
                        console.error(`[Health] ${serial}: Max restart attempts (${maxAttempts}) reached`);
                        this.setStreamStatus($streamItem, 'failed', `Failed after ${maxAttempts} attempts`);
                        return;
                    }

                    // Exponential backoff: 5s, 10s, 20s, 40s, 60s (max)
                    const delay = Math.min(5000 * Math.pow(2, attempts), 60000);

                    console.log(`[Health] ${serial}: Scheduling restart ${attempts + 1}/${maxAttempts} in ${delay / 1000}s`);
                    this.setStreamStatus($streamItem, 'loading', `Retry ${attempts + 1} in ${delay / 1000}s`);

                    // Increment counter
                    this.restartAttempts.set(serial, attempts + 1);

                    // Clear existing timer
                    if (this.restartTimers.has(serial)) {
                        clearTimeout(this.restartTimers.get(serial));
                    }

                    // Schedule restart
                    const timer = setTimeout(async () => {
                        this.restartTimers.delete(serial);
                        console.log(`[Health] ${serial}: Executing restart attempt ${attempts + 1}`);
                        await this.restartStream(serial, $streamItem);
                    }, delay);

                    this.restartTimers.set(serial, timer);
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
        this.setupLayout();
        this.setupEventListeners();
        this.startAllStreams();
        this.updateStreamCount();

    }

    _attachFullscreenLatencyMeter(hls, videoEl) {
        if (!hls || !videoEl) return;
        // reuse badge if created
        if (!videoEl._fsLatencyOverlay) {
            const badge = document.createElement('div');
            Object.assign(badge.style, {
                position: 'absolute',
                right: '16px',
                top: '16px',
                padding: '4px 8px',
                fontSize: '14px',
                lineHeight: '18px',
                background: 'rgba(0,0,0,0.6)',
                color: '#fff',
                borderRadius: '8px',
                fontFamily: 'system-ui, -apple-system, Segoe UI, Roboto, sans-serif',
                pointerEvents: 'none',
                zIndex: 20,
            });
            // fullscreen overlay is already a flex container; ensure relative
            const overlay = this.$fullscreenOverlay[0];
            overlay.style.position = overlay.style.position || 'relative';
            overlay.appendChild(badge);
            videoEl._fsLatencyOverlay = badge;
        }

        const overlay = videoEl._fsLatencyOverlay;
        videoEl._fsLastPdtMs = null;

        const onFrag = (_, data) => {
            const pdt = data?.frag?.programDateTime;
            if (pdt != null) {
                videoEl._fsLastPdtMs = typeof pdt === 'number' ? pdt : new Date(pdt).getTime();
            }
        };

        hls.on(Hls.Events.FRAG_CHANGED, onFrag);

        if (videoEl._fsLatencyTimer) clearInterval(videoEl._fsLatencyTimer);
        videoEl._fsLatencyTimer = setInterval(() => {
            if (!videoEl._fsLastPdtMs) return;
            const s = ((Date.now() - videoEl._fsLastPdtMs) / 1000).toFixed(1);
            overlay.textContent = `${s}s`;
            overlay.style.display = '';
        }, 250);

        videoEl._fsLatencyDetach = () => {
            hls.off(Hls.Events.FRAG_CHANGED, onFrag);
            if (videoEl._fsLatencyTimer) { clearInterval(videoEl._fsLatencyTimer); videoEl._fsLatencyTimer = null; }
            if (videoEl._fsLatencyOverlay) { videoEl._fsLatencyOverlay.textContent = ''; }
            videoEl._fsLastPdtMs = null;
        };
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
        // Fullscreen button click handler
        this.$container.on('click', '.stream-fullscreen-btn', (e) => {
            e.stopPropagation(); // Prevent event bubbling
            const $streamItem = $(e.target).closest('.stream-item');
            if ($streamItem.length) {
                const serial = $streamItem.data('camera-serial');
                const name = $streamItem.data('camera-name');
                const cameraType = $streamItem.data('camera-type');
                const streamType = $streamItem.data('stream-type');
                this.openFullscreen(serial, name, cameraType, streamType);
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
            const serial = $streamItem.data('camera-serial');
            const cameraType = $streamItem.data('camera-type');
            const streamType = $streamItem.data('stream-type');
            this.startStream(serial, $streamItem, cameraType, streamType);
        });

        this.$container.on('click', '.stop-stream-btn', (e) => {
            e.stopPropagation();
            const $streamItem = $(e.target).closest('.stream-item');
            const serial = $streamItem.data('camera-serial');
            const cameraType = $streamItem.data('camera-type');
            const streamType = $streamItem.data('stream-type');
            this.stopIndividualStream(serial, $streamItem, cameraType, streamType);
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
            const serial = $streamItem.data('camera-serial');
            const streamType = $streamItem.data('stream-type');
            const videoElement = $streamItem.find('.stream-video')[0];

            // Only HLS streams can be force-refreshed (for now)
            // not adding this condition will make the system 
            // create a new rtsp stream while rtmp witll still 
            // be running. 
            if (streamType === 'HLS' || streamType === 'LL_HLS') {
                this.hlsManager.forceRefreshStream(serial, videoElement);
            }
        });

        // Fullscreen close handlers
        this.$fullscreenClose.on('click', () => {
            this.closeFullscreen();
        });

        this.$fullscreenOverlay.on('click', (e) => {
            if ($(e.target).is(this.$fullscreenOverlay)) {
                this.closeFullscreen();
            }
        });

        // Escape key to close fullscreen
        $(document).on('keydown', (e) => {
            if (e.key === 'Escape' && this.$fullscreenOverlay.css('display') === 'flex') {
                this.closeFullscreen();
            }
        });
    }

    async startAllStreams() {
        const $streamItems = this.$container.find('.stream-item');

        // Start all streams in parallel instead of sequential
        const startPromises = $streamItems.toArray().map(async (item) => {
            const $item = $(item);
            const serial = $item.data('camera-serial');
            const cameraType = $item.data('camera-type');
            const streamType = $item.data('stream-type');

            try {
                await this.startStream(serial, $item, cameraType, streamType);
            } catch (error) {
                console.error(`Failed to start stream for ${serial}:`, error);
                this.setStreamStatus($item, 'error', 'Failed to load');
            }
        });

        // Wait for all to complete
        await Promise.allSettled(startPromises);
    }

    async startStream(serial, $streamItem, cameraType, streamType) {
        const streamElement = $streamItem.find('.stream-video')[0];
        const $loadingIndicator = $streamItem.find('.loading-indicator');

        try {
            $loadingIndicator.show();
            this.setStreamStatus($streamItem, 'loading', 'Starting...');

            let success;

            // Use streamType to determine which manager to use
            if (streamType === 'mjpeg_proxy') {
                success = await this.mjpegManager.startStream(serial, streamElement);
            } else if (streamType === 'HLS' || streamType === 'LL_HLS') {
                success = await this.hlsManager.startStream(serial, streamElement, 'sub');
            } else if (streamType === 'RTMP') {
                success = await this.flvManager.startStream(serial, streamElement);
            } else {
                throw new Error(`Unknown stream type: ${streamType}`);
            }

            if (success) {
                $loadingIndicator.hide();
                this.setStreamStatus($streamItem, 'live', 'Live');
                this.updateStreamButtons($streamItem, true);

                // Reset restart counter on successful start
                this.restartAttempts.delete(serial);
                if (this.restartTimers.has(serial)) {
                    clearTimeout(this.restartTimers.get(serial));
                    this.restartTimers.delete(serial);
                }

                const el = $streamItem.find('.stream-video')[0];

                if (streamType === 'HLS' && this.health) {
                    const hls = this.hlsManager?.hlsInstances?.get?.(serial) || null;
                    el._healthDetach = this.health.attachHls(serial, el, hls);
                } else if (streamType === 'RTMP' && this.health) {
                    const flv = this.flvManager?.flvInstances?.get?.(serial) || null;
                    el._healthDetach = this.health.attachRTMP(serial, el, flv);
                } else if (streamType === 'mjpeg_proxy' && this.health) {
                    el._healthDetach = this.health.attachMjpeg(serial, el);
                }
            }



        } catch (error) {
            $loadingIndicator.hide();
            this.setStreamStatus($streamItem, 'error', 'Failed');
            this.updateStreamButtons($streamItem, false);
            console.error(`Stream start failed for ${serial}:`, error);
        }
    }

    async stopIndividualStream(serial, $streamItem, cameraType, streamType) {
        try {
            let success;

            // Use streamType to determine which manager to use
            if (streamType === 'mjpeg_proxy') {
                success = this.mjpegManager.stopStream(serial);
            } else if (streamType === 'HLS' || streamType === 'LL_HLS') {
                success = await this.hlsManager.stopStream(serial);
            } else if (streamType === 'RTMP') {
                success = this.flvManager.stopStream(serial);
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
            console.error(`Failed to stop stream for ${serial}:`, error);
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

    async restartStream(serial, $streamItem) {
        /** What this does:

        - For HLS streams: calls forceRefreshStream() which destroys the HLS.js instance and clears its cache
        - This fixes the "media sequence mismatch" error by forcing HLS.js to reload everything fresh
        - For MJPEG streams: keeps the old stop+start logic (no cache to worry about)
        - Properly detaches health monitor before restart to avoid duplicate monitoring
        */
        try {
            console.log(`[Restart] ${serial}: Beginning restart sequence`);
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
            if (streamType === 'HLS' || streamType === 'LL_HLS') {
                // ← CHANGED: Call forceRefreshStream which destroys HLS.js cache
                await this.hlsManager.forceRefreshStream(serial, videoElement);
                this.setStreamStatus($streamItem, 'live', 'Live');
            } else if (streamType === 'mjpeg_proxy') {
                // MJPEG doesn't have cache issues, just restart normally
                await this.stopIndividualStream(serial, $streamItem, cameraType, streamType);
                await new Promise(r => setTimeout(r, 1500));
                await this.startStream(serial, $streamItem, cameraType, streamType);
            } else if (streamType === 'RTMP') {
                // Fully tear down and recreate the flv.js player
                this.flvManager.stopStream(serial);
                await new Promise(r => setTimeout(r, 500));
                const ok = await this.startStream(serial, $streamItem, cameraType, streamType);

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

            console.log(`[Restart] ${serial}: Restart complete`);
        } catch (e) {
            console.error(`[Restart] ${serial}: Failed`, e);
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

    async openFullscreen(serial, name, cameraType, streamType) {
        try {
            this.$fullscreenTitle.text(name);
            this.$fullscreenOverlay.css('display', 'flex');


            // Use streamType to determine fullscreen rendering method
            if (streamType === 'HLS' || streamType === 'LL_HLS') {
                // HLS fullscreen (works for both Eufy and UniFi in HLS mode)
                const response = await fetch(`/api/stream/start/${serial}`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ type: 'main' })
                });

                if (!response.ok) throw new Error('Failed to start fullscreen stream');

                // Wait for stream
                // await new Promise(resolve => setTimeout(resolve, 2000));
                // Wait briefly for playlist to appear (keep very small to avoid startup lag)
                await new Promise(resolve => setTimeout(resolve, 200));

                // Load in fullscreen video
                // const playlistUrl = `/api/streams/${serial}/playlist.m3u8`;
                const playlistUrl = `/api/streams/${serial}/playlist.m3u8?t=${Date.now()}`;

                if (Hls.isSupported()) {
                    this.destroyFullscreenHls();

                    this.fullscreenHls = new Hls({
                        enableWorker: true,
                        lowLatencyMode: true,
                        // keep backBufferLength but don’t hoard 90s; 10s is ample for scrubbing
                        backBufferLength: 10,
                        // hug the live edge: ~1 part target behind, cap max drift to ~2
                        liveSyncDurationCount: 1,
                        liveMaxLatencyDurationCount: 2,
                        // allow gentle catch-up when we fall behind without causing stalls
                        maxLiveSyncPlaybackRate: 1.5
                    });

                    this.fullscreenHls.loadSource(playlistUrl);
                    this.fullscreenHls.attachMedia(this.$fullscreenVideo[0]);
                    this._attachFullscreenLatencyMeter(this.fullscreenHls, this.$fullscreenVideo[0]);

                    this.fullscreenHls.on(Hls.Events.MANIFEST_PARSED, () => {
                        this.$fullscreenVideo[0].play().catch(() => { });
                    });

                } else if (this.$fullscreenVideo[0].canPlayType('application/vnd.apple.mpegurl')) {
                    this.$fullscreenVideo.attr('src', playlistUrl);
                    this.$fullscreenVideo.on('loadedmetadata.fullscreen', () => {
                        this.$fullscreenVideo[0].play().catch(() => { });
                    });
                }

            } else if (streamType === 'mjpeg_proxy') {
                // MJPEG fullscreen (works for cameras using MJPEG mode)
                const mjpegUrl = `/api/unifi/${serial}/stream/mjpeg?t=${Date.now()}`;
                this.$fullscreenVideo.hide();

                // Create or reuse img element for MJPEG fullscreen
                let $mjpegImg = $('#fullscreen-mjpeg');
                if ($mjpegImg.length === 0) {
                    $mjpegImg = $('<img>', {
                        id: 'fullscreen-mjpeg',
                        class: 'fullscreen-video',
                        css: {
                            maxWidth: '95%',
                            maxHeight: '95%',
                            objectFit: 'contain'
                        }
                    });
                    this.$fullscreenOverlay.append($mjpegImg);
                }

                $mjpegImg.attr('src', mjpegUrl).show();
            }

        } catch (error) {
            console.error('Failed to open fullscreen:', error);
            this.closeFullscreen();
        }
    }

    closeFullscreen() {
        this.$fullscreenOverlay.hide();
        this.destroyFullscreenHls();
        this.$fullscreenVideo.attr('src', '').show();
        this.$fullscreenVideo.off('loadedmetadata.fullscreen');

        // Hide MJPEG fullscreen if exists
        const $mjpegImg = $('#fullscreen-mjpeg');
        if ($mjpegImg.length) {
            $mjpegImg.attr('src', '').hide();
        }
    }

    destroyFullscreenHls() {
        if (this.fullscreenHls) {
            if (this.$fullscreenVideo[0]?._fsLatencyDetach) {
                this.$fullscreenVideo[0]._fsLatencyDetach();
                delete this.$fullscreenVideo[0]._fsLatencyDetach;
            }
            this.fullscreenHls.destroy();
            this.fullscreenHls = null;
        }
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
