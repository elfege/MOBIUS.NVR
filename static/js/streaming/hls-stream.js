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
        // 0) Remember current type (default to 'sub') before we clear the map
        const current = this.activeStreams.get(cameraId);
        const streamType = current?.type ?? 'sub';

        // 1) Client-side teardown (no renames)
        try {
            const existingHls = this.hlsInstances.get(cameraId);
            if (existingHls) {
                existingHls.destroy();
                this.hlsInstances.delete(cameraId);
            }
        } catch (e) {
            console.warn(`[forceRefreshStream] HLS teardown warning for ${cameraId}:`, e);
        }

        const stream = this.activeStreams.get(cameraId);
        if (stream && stream.element) {
            try {
                stream.element.src = '';
                stream.element.load?.();
            } catch (e) {
                console.warn(`[forceRefreshStream] element reset warning for ${cameraId}:`, e);
            }
            this.activeStreams.delete(cameraId);
        }

        // 2) Tell backend to STOP (use singular /api/stream/*)
        try {
            const res = await fetch(`/api/stream/stop/${encodeURIComponent(cameraId)}`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' }
            });
            if (!res.ok) console.warn(`[forceRefreshStream] stop returned ${res.status} for ${cameraId}`);
        } catch (e) {
            console.warn(`[forceRefreshStream] stop failed for ${cameraId}:`, e);
        }

        // 3) Poll /status until backend reports fully down to avoid “already active”
        const deadline = Date.now() + 5000; // up to 5s
        while (Date.now() < deadline) {
            try {
                const r = await fetch(`/api/stream/status/${encodeURIComponent(cameraId)}`);
                if (r.ok) {
                    const s = await r.json();
                    if (!s.is_streaming) break;
                }
            } catch (_) { /* ignore transient errors */ }
            await new Promise(r => setTimeout(r, 200));
        }

        // 4) Small grace so ffmpeg releases sockets
        await new Promise(r => setTimeout(r, 250));

        // 5) START on backend (singular /api/stream/*); then reattach via  existing API
        try {
            const startRes = await fetch(`/api/stream/start/${encodeURIComponent(cameraId)}`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ type: streamType, reason: 'force-refresh' })
            });
            if (!startRes.ok) {
                console.warn(`[forceRefreshStream] start returned ${startRes.status} for ${cameraId}`);
            }
        } catch (e) {
            console.warn(`[forceRefreshStream] start failed for ${cameraId}:`, e);
        }

        // 6) Give the new playlist a moment, then reattach (HLS/MJPEG/RTMP handled by  startStream)
        await new Promise(r => setTimeout(r, 500));
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

            // Choose playlist URL:
            // - LL-HLS publishers: use server-provided same-origin URL verbatim (e.g., "/hls/<path>/index.m3u8")
            // - Classic HLS: keep existing /api/streams/... with cache-busting
            let playlistUrl;
            // Treat any server-provided /hls/... URL as LL-HLS
            if (typeof startInfo?.stream_url === 'string' && startInfo.stream_url.startsWith('/hls/')) {
                playlistUrl = startInfo.stream_url;            // ← DO NOT rewrite
            } else {
                // CACHE BUSTING: Add timestamp to URL
                const ts = Date.now();
                playlistUrl = `/api/streams/${cameraId}/playlist.m3u8?t=${ts}`;
                // tiny grace so the playlist appears without adding visible latency
                await new Promise(r => setTimeout(r, 200));
            }

            if (Hls.isSupported()) {
                const hls = new Hls({
                    enableWorker: true,
                    lowLatencyMode: true,
                    liveSyncDurationCount: 2,
                    liveMaxLatencyDurationCount: 3,
                    maxLiveSyncPlaybackRate: 1.5,
                    backBufferLength: 10,
                    ...(startInfo?.protocol !== 'll_hls' && {
                        xhrSetup: (xhr) => {
                            xhr.setRequestHeader('Cache-Control', 'no-cache, no-store, must-revalidate');
                            xhr.setRequestHeader('Pragma', 'no-cache');
                            xhr.setRequestHeader('Expires', '0');
                        }
                    })
                });

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
    async stopStream(cameraId) {
        try {
            const response = await fetch(`/api/stream/stop/${cameraId}`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' }
            });

            const hls = this.hlsInstances.get(cameraId);
            if (hls) {
                hls.destroy();
                this.hlsInstances.delete(cameraId);
            }

            const stream = this.activeStreams.get(cameraId);
            if (stream) {
                stream.element.src = '';
                if (stream.element._latencyDetach) {
                    stream.element._latencyDetach();
                    delete stream.element._latencyDetach;
                }

                this.activeStreams.delete(cameraId);
            }

            return response.ok;

        } catch (error) {
            console.error(`Failed to stop HLS stream for ${cameraId}:`, error);

            // Cleanup local state even if API call fails
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
    async stopAllStreams() {
        try {
            await fetch('/api/streams/stop-all', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' }
            });

            // Cleanup all HLS instances
            this.hlsInstances.forEach(hls => hls.destroy());
            this.hlsInstances.clear();

            // Clear all video elements
            this.activeStreams.forEach(stream => {
                stream.element.src = '';
            });
            this.activeStreams.clear();

            return true;

        } catch (error) {
            console.error('Failed to stop all HLS streams:', error);

            // Force cleanup even if API fails
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
}
