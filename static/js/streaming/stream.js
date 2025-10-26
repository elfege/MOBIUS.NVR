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
                const cameraId = $streamItem.data('camera-serial');
                const name = $streamItem.data('camera-name');
                const cameraType = $streamItem.data('camera-type');
                const streamType = $streamItem.data('stream-type');
                this.openFullscreen(cameraId, name, cameraType, streamType);
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
            const cameraId = $item.data('camera-serial');
            const cameraType = $item.data('camera-type');
            const streamType = $item.data('stream-type');

            try {
                await this.startStream(cameraId, $item, cameraType, streamType);
            } catch (error) {
                console.error(`Failed to start stream for ${cameraId}:`, error);
                this.setStreamStatus($item, 'error', 'Failed to load');
            }
        });

        // Wait for all to complete
        await Promise.allSettled(startPromises);
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
            this.$fullscreenTitle.text(name);
            this.$fullscreenOverlay.css('display', 'flex');

            // Show/hide PTZ controls based on camera capabilities
            const config = await this.getCameraConfig(cameraId);
            console.log('[FULLSCREEN] Camera config:', config);
            const hasPTZ = config?.capabilities?.includes('ptz');
            console.log('[FULLSCREEN] Has PTZ:', hasPTZ);
            const fullScreenPTZ = $('#fullscreen-ptz');
            console.log('[FULLSCREEN] PTZ element found:', fullScreenPTZ.length);

            if (hasPTZ) {
                console.log('[FULLSCREEN] Showing PTZ controls');
                fullScreenPTZ.show();
                this.ptzController.setCurrentCamera(cameraId, name);
                this.ptzController.setBridgeReady(true);
            } else {
                console.log('[FULLSCREEN] Hiding PTZ controls');
                fullScreenPTZ.hide();
            }

            // Use streamType to determine fullscreen rendering method
            if (streamType === 'HLS' || streamType === 'LL_HLS' || streamType === 'NEOLINK' || streamType === 'NEOLINK_LL_HLS') {
                // HLS fullscreen (works for both Eufy and UniFi in HLS mode)
                const response = await fetch(`/api/stream/start/${cameraId}`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ type: 'main' })
                });

                if (!response.ok) throw new Error('Failed to start fullscreen stream');

                // Fetch stream metadata from backend after starting the stream.
                // Returns: { protocol: 'll_hls'|'hls'|'rtmp', stream_url: '/hls/...' or '/api/streams/...', camera_name: '...' }
                // This tells us what the backend ACTUALLY started (vs what's configured in cameras.json)
                // Used to determine the correct playlist URL and verify the stream type matches expectations.
                const startInfo = await response.json().catch(() => ({}));

                // Wait briefly for playlist to appear (keep very small to avoid startup lag)
                await new Promise(resolve => setTimeout(resolve, 200));

                // Choose playlist URL based on stream type

                let playlistUrl;
                if (typeof startInfo?.stream_url === 'string' && startInfo.stream_url.startsWith('/hls/')) {
                    // LL-HLS: use MediaMTX URL from backend
                    playlistUrl = startInfo.stream_url;
                } else {
                    // Regular HLS: use app-generated playlist
                    playlistUrl = `/api/streams/${cameraId}/playlist.m3u8?t=${Date.now()}`;
                }

                if (Hls.isSupported()) {
                    this.destroyFullscreenHls();

                    // Get camera config and build player settings
                    const cameraConfig = await this.getCameraConfig(cameraId);
                    const isLLHLS = cameraConfig?.stream_type === 'LL_HLS';
                    const hlsConfig = this.buildHlsConfig(cameraConfig, isLLHLS);

                    this.fullscreenHls = new Hls(hlsConfig);
                    this.fullscreenHls.loadSource(playlistUrl);
                    this.fullscreenHls.attachMedia(this.$fullscreenVideo[0]);
                    this._attachFullscreenLatencyMeter(this.fullscreenHls, this.$fullscreenVideo[0]);

                    this.fullscreenHls.on(Hls.Events.MANIFEST_PARSED, () => {
                        this.$fullscreenVideo[0].play().catch(() => { });
                    });

                } else if (this.$fullscreenVideo[0].canPlayType('application/vnd.apple.mpegurl')) {
                    // Native HLS support (Safari)
                    this.$fullscreenVideo.attr('src', playlistUrl);
                    this.$fullscreenVideo.on('loadedmetadata.fullscreen', () => {
                        this.$fullscreenVideo[0].play().catch(() => { });
                    });
                }

            } else if (streamType === 'MJPEG' || streamType === 'mjpeg_proxy') {
                // MJPEG fullscreen - route based on camera type
                let mjpegUrl;
                if (cameraType === 'reolink') {
                    mjpegUrl = `/api/reolink/${cameraId}/stream/mjpeg/main?t=${Date.now()}`;
                } else if (cameraType === 'unifi') {
                    mjpegUrl = `/api/unifi/${cameraId}/stream/mjpeg?t=${Date.now()}`;
                } else if (cameraType === 'amcrest') {
                    // mjpegUrl = `/api/amcrest/${cameraId}/stream/mjpeg/main?t=${Date.now()}`;
                    mjpegUrl = `/api/amcrest/${cameraId}/stream/mjpeg?t=${Date.now()}`;  // Same as grid view for now
                } else {
                    throw new Error(`Unsupported camera type for MJPEG fullscreen: ${cameraType}`);
                }

                this.$fullscreenVideo.hide();

                let $mjpegImg = $('#fullscreen-mjpeg');
                if ($mjpegImg.length === 0) {
                    $mjpegImg = $('<img>', {
                        id: 'fullscreen-mjpeg',
                        alt: 'Fullscreen MJPEG Stream'
                    });
                    this.$fullscreenOverlay.append($mjpegImg);
                }

                this.$fullscreenOverlay.addClass('mjpeg-active');
                $mjpegImg.attr('src', mjpegUrl).show();

                $mjpegImg.attr('src', mjpegUrl).show();
            } else if (streamType === 'RTMP') {
                // RTMP fullscreen using FLV.js
                const response = await fetch(`/api/stream/start/${cameraId}`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ type: 'main' })
                });

                if (!response.ok) throw new Error('Failed to start fullscreen stream');

                await new Promise(resolve => setTimeout(resolve, 200));

                const flvUrl = `/api/camera/${cameraId}/flv?t=${Date.now()}`;

                if (flvjs.isSupported()) {
                    this.destroyFullscreenFlv(); // Need to add this method

                    this.fullscreenFlv = flvjs.createPlayer({
                        type: 'flv',
                        url: flvUrl,
                        isLive: true,
                        hasAudio: false
                    });

                    this.fullscreenFlv.attachMediaElement(this.$fullscreenVideo[0]);
                    this.fullscreenFlv.load();
                    this.fullscreenFlv.play().catch(() => { });
                }

            }

        } catch (error) {
            console.error('Failed to open fullscreen:', error);
            this.closeFullscreen();
        }
    }

    closeFullscreen() {
        this.$fullscreenOverlay.hide();
        this.$fullscreenOverlay.removeClass('mjpeg-active');
        this.destroyFullscreenHls();
        this.$fullscreenVideo.attr('src', '').show();
        this.$fullscreenVideo.off('loadedmetadata.fullscreen');
        $('#fullscreen-ptz').hide();

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
