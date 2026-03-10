/**
 * HLS Stream Manager - ES6 Module
 * Handles HLS streaming with cache busting
 */

export class HLSStreamManager {
    constructor() {
        this.hlsInstances = new Map();
        this.activeStreams = new Map();
        this.retryAttempts = new Map();
    }

    // --- latency overlay helpers (non-breaking additions) ---
    _ensureLatencyOverlay(videoEl) {
        if (videoEl._latencyOverlay) return videoEl._latencyOverlay;
        const badge = document.createElement('div');
        badge.className = 'latency-badge';
        Object.assign(badge.style, {
            position: 'absolute',
            left: '8px',
            bottom: '8px',
            padding: '2px 6px',
            fontSize: '12px',
            lineHeight: '16px',
            background: 'rgba(0,0,0,0.6)',
            color: '#fff',
            borderRadius: '6px',
            fontFamily: 'system-ui, -apple-system, Segoe UI, Roboto, sans-serif',
            pointerEvents: 'none',
            zIndex: 2,
        });
        // container: assume parent .stream-item is position:relative ( markup already uses cards)
        const parent = videoEl.parentElement || document.body;
        parent.style.position = parent.style.position || 'relative';
        parent.appendChild(badge);
        videoEl._latencyOverlay = badge;
        return badge;
    }

    /**
     * Attach PDT-based latency meter to a video element.
     *
     * Displays live stream lag (Date.now - last fragment PDT) as a badge overlay.
     * Also drives two-stage frozen stream recovery:
     *
     *   Stage 1 — UI restart (no backend involvement):
     *     If lag > LAG_THRESHOLD_MS for > FREEZE_CONFIRM_MS continuously,
     *     destroy and reinitialise HLS.js (forceRefreshStream).
     *     This resolves most freezes caused by transient network glitches or
     *     HLS.js buffer stalls without touching the backend pipeline.
     *
     *   Stage 2 — Backend pipeline check (informational only):
     *     POST_RESTART_CHECK_MS after the UI restart, if lag is still above
     *     threshold we query /api/camera/state/<id> and log whether the backend
     *     pipeline is broken.  We deliberately do NOT force a backend restart
     *     here — the StreamWatchdog owns that decision.
     *
     * A UI_RESTART_COOLDOWN_MS prevents restart loops.
     *
     * @param {Hls}             hls      - hls.js instance
     * @param {HTMLVideoElement} videoEl  - video element being driven
     * @param {string}           cameraId - camera serial number
     */
    _attachLatencyMeter(hls, videoEl, cameraId) {
        const LAG_THRESHOLD_MS       = 30_000;   // lag above this = frozen
        const FREEZE_CONFIRM_MS      = 10_000;   // must be frozen this long before acting
        const POST_RESTART_CHECK_MS  = 60_000;   // check backend state this long after UI restart
        const UI_RESTART_COOLDOWN_MS = 120_000;  // minimum gap between consecutive UI restarts

        // Keep last seen PDT in ms
        videoEl._lastFragPdtMs = null;

        // Frozen-stream recovery state (persists across hls.js reinits on the same element)
        videoEl._lagStartMs           = null;   // when lag first exceeded threshold
        videoEl._frozenUiRestarted    = false;  // UI restart triggered for this freeze episode
        videoEl._frozenLastRestartMs  = videoEl._frozenLastRestartMs || 0;  // last UI restart timestamp (preserved across reinits for cooldown)

        // Cancel any pending post-restart check from a prior hls.js instance on this element
        if (videoEl._frozenPostRestartTimer) {
            clearTimeout(videoEl._frozenPostRestartTimer);
            videoEl._frozenPostRestartTimer = null;
        }

        const overlay = this._ensureLatencyOverlay(videoEl);

        const onFrag = (_, data) => {
            const pdt = data?.frag?.programDateTime;
            if (pdt != null) {
                const pdtMs = typeof pdt === 'number' ? pdt : new Date(pdt).getTime();
                // Only advance on genuinely new fragment PDT to avoid duplicates confusing recovery
                if (!videoEl._lastFragPdtMs || pdtMs > videoEl._lastFragPdtMs) {
                    videoEl._lastFragPdtMs = pdtMs;
                    // Fresh fragment: reset lag-tracking (stream is producing again)
                    videoEl._lagStartMs        = null;
                    videoEl._frozenUiRestarted = false;
                    if (videoEl._frozenPostRestartTimer) {
                        clearTimeout(videoEl._frozenPostRestartTimer);
                        videoEl._frozenPostRestartTimer = null;
                    }
                }
            }
        };

        hls.on(Hls.Events.FRAG_CHANGED, onFrag);

        // Update badge ~4×/sec and drive frozen-stream recovery
        if (videoEl._latencyTimer) clearInterval(videoEl._latencyTimer);
        videoEl._latencyTimer = setInterval(() => {
            if (!videoEl._lastFragPdtMs) return;

            const now = Date.now();
            const ms  = now - videoEl._lastFragPdtMs;
            const s   = (ms / 1000).toFixed(1);
            overlay.textContent = `${s}s`;
            overlay.style.display = '';

            // --- Stage 1: UI restart ---
            if (ms > LAG_THRESHOLD_MS) {
                if (!videoEl._lagStartMs) videoEl._lagStartMs = now;
                const frozenDuration  = now - videoEl._lagStartMs;
                const cooldownExpired = (now - videoEl._frozenLastRestartMs) > UI_RESTART_COOLDOWN_MS;

                if (frozenDuration > FREEZE_CONFIRM_MS && !videoEl._frozenUiRestarted && cooldownExpired) {
                    videoEl._frozenUiRestarted   = true;
                    videoEl._frozenLastRestartMs = now;

                    console.warn(`[HLS] ${cameraId}: frozen (${(frozenDuration / 1000).toFixed(0)}s lag) — triggering UI restart`);

                    // Destroy and reinitialise HLS.js client-side only
                    this.forceRefreshStream(cameraId, videoEl).catch(e => {
                        console.warn(`[HLS] ${cameraId}: forceRefreshStream failed:`, e);
                    });

                    // --- Stage 2: backend pipeline check after grace period ---
                    videoEl._frozenPostRestartTimer = setTimeout(async () => {
                        videoEl._frozenPostRestartTimer = null;

                        // If lag recovered (lagStartMs was reset by fresh PDT), nothing to do
                        if (!videoEl._lagStartMs) {
                            console.log(`[HLS] ${cameraId}: UI restart resolved frozen stream`);
                            return;
                        }
                        const currentLag = Date.now() - videoEl._lastFragPdtMs;
                        if (currentLag <= LAG_THRESHOLD_MS) {
                            console.log(`[HLS] ${cameraId}: UI restart resolved frozen stream`);
                            return;
                        }

                        // Still frozen — check backend pipeline state (informational)
                        console.warn(`[HLS] ${cameraId}: still frozen ${(currentLag / 1000).toFixed(0)}s after UI restart — checking backend pipeline`);
                        try {
                            const resp = await fetch(`/api/camera/state/${cameraId}`);
                            if (!resp.ok) return;
                            const state = await resp.json();
                            const avail = state?.availability;
                            if (avail === 'ONLINE') {
                                console.log(`[HLS] ${cameraId}: backend reports ONLINE — transient player issue; watchdog will handle if pipeline is actually broken`);
                            } else {
                                console.warn(`[HLS] ${cameraId}: backend reports ${avail} — pipeline may be broken; StreamWatchdog should restart it`);
                            }
                        } catch (e) {
                            console.warn(`[HLS] ${cameraId}: backend state check failed:`, e);
                        }
                    }, POST_RESTART_CHECK_MS);
                }
            }
            // lag < threshold: _lagStartMs is reset in onFrag when a fresh fragment arrives.
            // If no new frags arrive at all, lagStartMs stays set and drives recovery above.
        }, 250);

        // Cleanup hook — called by stopStream / _attachLatencyMeter reinit
        videoEl._latencyDetach = () => {
            hls.off(Hls.Events.FRAG_CHANGED, onFrag);
            if (videoEl._latencyTimer) { clearInterval(videoEl._latencyTimer); videoEl._latencyTimer = null; }
            if (videoEl._latencyOverlay) { videoEl._latencyOverlay.textContent = ''; }
            if (videoEl._frozenPostRestartTimer) { clearTimeout(videoEl._frozenPostRestartTimer); videoEl._frozenPostRestartTimer = null; }
            videoEl._lastFragPdtMs     = null;
            videoEl._lagStartMs        = null;
            videoEl._frozenUiRestarted = false;
            // NOTE: _frozenLastRestartMs intentionally NOT cleared — preserves cooldown across detach/reinit
        };
    }


    /**
     * Force refresh a stream by destroying and recreating the HLS instance.
     *
     * Preserves the last frame during refresh to avoid visible black flash:
     * 1. Capture current video frame to canvas overlay
     * 2. Destroy existing HLS instance
     * 3. Restart stream
     * 4. Remove canvas when new stream is playing
     */
    async forceRefreshStream(cameraId, videoElement) {
        // 0) Remember current type before clearing map
        const current = this.activeStreams.get(cameraId);
        const streamType = current?.type ?? 'sub';

        // Capture last frame to canvas overlay for seamless transition
        let frameCanvas = null;
        if (videoElement && videoElement.readyState >= 2 && videoElement.videoWidth > 0) {
            frameCanvas = this._captureFrameOverlay(cameraId, videoElement);
        }

        // 1) Client-side teardown
        try {
            const existingHls = this.hlsInstances.get(cameraId);
            if (existingHls) {
                existingHls.stopLoad(); // Stop fetching
                existingHls.destroy();
                this.hlsInstances.delete(cameraId);
            }
        } catch (e) {
            console.warn(`[forceRefreshStream] HLS teardown warning for ${cameraId}:`, e);
        }

        const stream = this.activeStreams.get(cameraId);
        if (stream && stream.element) {
            try {
                stream.element.pause(); // Stop decoder
                stream.element.src = '';
                stream.element.load?.();
            } catch (e) {
                console.warn(`[forceRefreshStream] element reset warning for ${cameraId}:`, e);
            }
            this.activeStreams.delete(cameraId);
        }

        // 2) Restart stream via startStream (handles backend + frontend)
        const result = await this.startStream(cameraId, videoElement, streamType);

        // Remove frame overlay after short delay (stream should be displaying by now)
        if (frameCanvas) {
            setTimeout(() => {
                if (frameCanvas.parentNode) {
                    frameCanvas.remove();
                }
            }, 2000);
        }

        return result;
    }

    /**
     * Capture current video frame to a canvas overlay positioned over the video.
     *
     * Creates a canvas element that shows the last frame, positioned exactly
     * over the video element. This prevents the black flash during stream refresh.
     *
     * @param {string} cameraId - Camera identifier for logging
     * @param {HTMLVideoElement} videoElement - Video element to capture from
     * @returns {HTMLCanvasElement} Canvas element with captured frame
     */
    _captureFrameOverlay(cameraId, videoElement) {
        const canvas = document.createElement('canvas');
        canvas.width = videoElement.videoWidth;
        canvas.height = videoElement.videoHeight;
        canvas.className = 'hls-frame-overlay';

        // Draw current frame
        const ctx = canvas.getContext('2d');
        ctx.drawImage(videoElement, 0, 0);

        // Position canvas exactly over video element
        canvas.style.cssText = `
            position: absolute;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            object-fit: cover;
            z-index: 10;
            pointer-events: none;
        `;

        // Insert canvas as sibling after video (video parent should have position:relative)
        const parent = videoElement.parentElement;
        if (parent) {
            // Ensure parent has relative positioning for absolute canvas
            const parentStyle = window.getComputedStyle(parent);
            if (parentStyle.position === 'static') {
                parent.style.position = 'relative';
            }
            parent.appendChild(canvas);
            console.log(`[HLS] ${cameraId}: Frame overlay created for seamless refresh`);
        }

        return canvas;
    }

    /**
     * Start HLS stream for a camera
     */
    async startStream(cameraId, videoElement, streamType = 'sub') {
        try {
            console.log(`[HLS] Starting ${streamType} stream for ${cameraId}...`);

            // Start stream on backend
            const response = await fetch(`/api/stream/start/${cameraId}`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ type: streamType })
            });

            if (!response.ok) throw new Error('Failed to start stream');
            const startInfo = await response.json().catch(() => ({}));

            console.log(`[HLS] Backend response for ${cameraId}:`, startInfo);

            // Choose playlist URL
            let playlistUrl;
            if (typeof startInfo?.stream_url === 'string' && (startInfo.stream_url.startsWith('/hls/') || startInfo.stream_url.startsWith('/api/'))) {
                // Backend provided a valid URL - use it directly
                // Handles both /hls/{serial}_main/index.m3u8 (MediaMTX) and /api/streams/{serial}_main/playlist.m3u8
                playlistUrl = startInfo.stream_url;
                console.log(`[HLS] Using backend stream_url: ${playlistUrl}`);
            } else {
                // Fallback: construct URL manually (should rarely happen)
                // IMPORTANT: Include _main suffix for main stream requests
                const ts = Date.now();
                const suffix = streamType === 'main' ? '_main' : '';
                playlistUrl = `/api/streams/${cameraId}${suffix}/playlist.m3u8?t=${ts}`;
                console.log(`[HLS] Using fallback URL: ${playlistUrl}`);
                await new Promise(r => setTimeout(r, 200));
            }

            if (Hls.isSupported()) {
                // Get camera config for player settings
                const cameraConfig = await this.getCameraConfig(cameraId);
                const isLLHLS = startInfo?.protocol === 'll_hls' || cameraConfig?.stream_type === 'LL_HLS';

                // Build hls.js config with smart defaults + camera overrides
                const hlsConfig = this.buildHlsConfig(cameraConfig, isLLHLS);

                const hls = new Hls(hlsConfig);

                hls.loadSource(playlistUrl);
                hls.attachMedia(videoElement);
                this._attachLatencyMeter(hls, videoElement, cameraId);

                return new Promise((resolve, reject) => {
                    hls.on(Hls.Events.MANIFEST_PARSED, () => {
                        this.retryAttempts.delete(cameraId);
                        // Use vanilla JS play() which returns a Promise
                        videoElement.play().catch(err => {
                            console.warn('Autoplay prevented:', err);
                        });

                        this.hlsInstances.set(cameraId, hls);
                        this.activeStreams.set(cameraId, {
                            element: videoElement,
                            hls: hls,
                            type: streamType,
                            startTime: Date.now()
                        });

                        // CRITICAL: Resolve with true so stream.js knows stream started
                        resolve(true);
                    });

                    // Listen for first frame to update UI status
                    hls.on(Hls.Events.FRAG_CHANGED, () => {
                        // Dispatch custom event when first fragment plays
                        if (!videoElement._firstFragReceived) {
                            videoElement._firstFragReceived = true;
                            console.log(`[HLS] ${cameraId}: First fragment received, stream is live`);
                            videoElement.dispatchEvent(new CustomEvent('streamlive', { detail: { cameraId } }));
                        }
                    });

                    hls.on(Hls.Events.ERROR, (event, data) => {
                        const timeout = 30000
                        const maxretries = 20
                        if (data.fatal) {
                            console.error(`HLS fatal error for ${cameraId}:`, data);

                            if (data.details === 'manifestLoadError' && data.response?.code === 404) {
                                const retries = this.retryAttempts.get(cameraId) || 0;
                                if (retries < maxretries) {
                                    console.log(`[HLS] Playlist 404 for ${cameraId}, retrying every ${timeout / 1000} seconds; ${retries + 1}/${maxretries}`);
                                    this.retryAttempts.set(cameraId, retries + 1);
                                    // Dispatch event to update UI during retry
                                    videoElement.dispatchEvent(new CustomEvent('streamretrying', {
                                        detail: { cameraId, retry: retries + 1, maxRetries: maxretries }
                                    }));
                                    setTimeout(() => {
                                        hls.loadSource(playlistUrl);
                                    }, timeout); // Wait N seconds for FFmpeg to create playlist
                                    return;
                                }
                            }
                            reject(new Error(`HLS stream error: ${data.type}`));
                        }
                    });
                });
            } else if (videoElement.canPlayType('application/vnd.apple.mpegurl')) {
                // Native HLS support (Safari)
                videoElement.src = playlistUrl;

                return new Promise((resolve) => {
                    videoElement.addEventListener('loadedmetadata', () => {
                        videoElement.play().catch(err => {
                            console.warn('Autoplay prevented:', err);
                        });

                        this.activeStreams.set(cameraId, {
                            element: videoElement,
                            type: streamType,
                            startTime: Date.now()
                        });

                        // CRITICAL: Resolve with true
                        resolve(true);
                    });
                });
            } else {
                throw new Error('HLS is not supported in this browser');
            }

        } catch (error) {
            console.error(`Failed to start HLS stream for ${cameraId}:`, error);
            throw error;
        }
    }

    /**
     * Stop HLS stream for a camera
     */
    stopStream(cameraId) {
        try {
            // Client-side cleanup only - no backend stop call needed
            const hls = this.hlsInstances.get(cameraId);
            if (hls) {
                hls.stopLoad(); // Stop fetching segments
                hls.destroy();
                this.hlsInstances.delete(cameraId);
            }

            const stream = this.activeStreams.get(cameraId);
            if (stream && stream.element) {
                stream.element.pause(); // Stop video decoder
                stream.element.src = '';

                if (stream.element._latencyDetach) {
                    stream.element._latencyDetach();
                    delete stream.element._latencyDetach;
                }

                this.activeStreams.delete(cameraId);
            }

            return true;

        } catch (error) {
            console.error(`Failed to stop HLS stream for ${cameraId}:`, error);

            // Force cleanup even on error
            const hls = this.hlsInstances.get(cameraId);
            if (hls) {
                hls.destroy();
                this.hlsInstances.delete(cameraId);
            }

            this.activeStreams.delete(cameraId);
            return false;
        }
    }

    /**
     * Check if stream is currently active
     */
    isStreamActive(cameraId) {
        return this.activeStreams.has(cameraId);
    }

    /**
     * Stop all active HLS streams
     */
    stopAllStreams() {
        try {
            // Client-side cleanup only - no backend stop call needed

            // Stop and destroy all HLS instances
            this.hlsInstances.forEach(hls => {
                hls.stopLoad(); // Stop fetching segments
                hls.destroy();
            });
            this.hlsInstances.clear();

            // Pause and clear all video elements
            this.activeStreams.forEach(stream => {
                if (stream.element) {
                    stream.element.pause(); // Stop video decoder
                    stream.element.src = '';

                    if (stream.element._latencyDetach) {
                        stream.element._latencyDetach();
                        delete stream.element._latencyDetach;
                    }
                }
            });
            this.activeStreams.clear();

            return true;

        } catch (error) {
            console.error('Failed to stop all HLS streams:', error);

            // Force cleanup even on error
            this.hlsInstances.forEach(hls => hls.destroy());
            this.hlsInstances.clear();
            this.activeStreams.clear();

            return false;
        }
    }

    /**
     * Get stream information for a camera
     */
    getStreamInfo(cameraId) {
        return this.activeStreams.get(cameraId) || null;
    }

    /**
     * Get all active stream IDs
     */
    getActiveStreamIds() {
        return Array.from(this.activeStreams.keys());
    }

    /**
     * Get camera configuration from API
     */
    async getCameraConfig(cameraId) {
        try {
            const response = await fetch(`/api/cameras/${cameraId}`);
            if (!response.ok) return null;
            return await response.json();
        } catch (error) {
            console.warn(`Failed to fetch camera config for ${cameraId}:`, error);
            return null;
        }
    }

    /**
     * Build hls.js configuration with smart defaults + camera overrides
     */
    buildHlsConfig(cameraConfig, isLLHLS) {
        // Smart defaults based on stream type
        const defaults = isLLHLS ? {
            enableWorker: true,
            lowLatencyMode: true,
            liveSyncDurationCount: 1,
            liveMaxLatencyDurationCount: 2,
            maxLiveSyncPlaybackRate: 1.5,
            backBufferLength: 5,
            // Disable ABR - stay at highest quality (fixes fullscreen degradation)
            abrEnabled: false,
            startLevel: -1  // -1 = auto-select highest available
        } : {
            enableWorker: true,
            lowLatencyMode: false,
            liveSyncDurationCount: 3,
            liveMaxLatencyDurationCount: 5,
            maxLiveSyncPlaybackRate: 1.5,
            backBufferLength: 10,
            // Disable ABR - stay at highest quality (fixes fullscreen degradation)
            abrEnabled: false,
            startLevel: -1  // -1 = auto-select highest available
        };

        // Merge with camera-specific overrides
        const cameraOverrides = cameraConfig?.player_settings?.hls_js || {};
        const config = { ...defaults, ...cameraOverrides };

        // Add cache control for non-LL-HLS
        if (!isLLHLS) {
            config.xhrSetup = (xhr) => {
                xhr.setRequestHeader('Cache-Control', 'no-cache, no-store, must-revalidate');
                xhr.setRequestHeader('Pragma', 'no-cache');
                xhr.setRequestHeader('Expires', '0');
            };
        }

        return config;
    }
}
