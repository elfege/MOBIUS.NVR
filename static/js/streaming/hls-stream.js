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
            right: '8px',
            top: '8px',
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

    _attachLatencyMeter(hls, videoEl) {
        // keep last seen PDT in ms
        videoEl._lastFragPdtMs = null;

        const overlay = this._ensureLatencyOverlay(videoEl);

        const onFrag = (_, data) => {
            // programDateTime may be number or string; normalize to ms
            const pdt = data?.frag?.programDateTime;
            if (pdt != null) {
                videoEl._lastFragPdtMs = typeof pdt === 'number' ? pdt : new Date(pdt).getTime();
            }
        };

        hls.on(Hls.Events.FRAG_CHANGED, onFrag);

        // update text ~4×/sec
        if (videoEl._latencyTimer) clearInterval(videoEl._latencyTimer);
        videoEl._latencyTimer = setInterval(() => {
            if (!videoEl._lastFragPdtMs) return;
            const ms = Date.now() - videoEl._lastFragPdtMs;
            const s = (ms / 1000).toFixed(1);
            overlay.textContent = `${s}s`;
            overlay.style.display = ''; // ensure shown
        }, 250);

        // store for cleanup
        videoEl._latencyDetach = () => {
            hls.off(Hls.Events.FRAG_CHANGED, onFrag);
            if (videoEl._latencyTimer) { clearInterval(videoEl._latencyTimer); videoEl._latencyTimer = null; }
            if (videoEl._latencyOverlay) { videoEl._latencyOverlay.textContent = ''; }
            videoEl._lastFragPdtMs = null;
        };
    }


    /**
     * Force refresh a stream by destroying and recreating the HLS instance
     */
    async forceRefreshStream(cameraId, videoElement) {
        // 0) Remember current type before clearing map
        const current = this.activeStreams.get(cameraId);
        const streamType = current?.type ?? 'sub';

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
        return await this.startStream(cameraId, videoElement, streamType);
    }

    /**
     * Start HLS stream for a camera
     */
    async startStream(cameraId, videoElement, streamType = 'sub') {
        try {
            // Start stream on backend
            const response = await fetch(`/api/stream/start/${cameraId}`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ type: streamType })
            });

            if (!response.ok) throw new Error('Failed to start stream');
            const startInfo = await response.json().catch(() => ({}));

            // Choose playlist URL
            let playlistUrl;
            if (typeof startInfo?.stream_url === 'string' && startInfo.stream_url.startsWith('/hls/')) {
                playlistUrl = startInfo.stream_url;
            } else {
                const ts = Date.now();
                playlistUrl = `/api/streams/${cameraId}/playlist.m3u8?t=${ts}`;
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
                this._attachLatencyMeter(hls, videoElement);

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
            backBufferLength: 5
        } : {
            enableWorker: true,
            lowLatencyMode: false,
            liveSyncDurationCount: 3,
            liveMaxLatencyDurationCount: 5,
            maxLiveSyncPlaybackRate: 1.5,
            backBufferLength: 10
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
