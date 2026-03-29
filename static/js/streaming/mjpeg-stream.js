/**
 * MJPEG Stream Manager - ES6 + jQuery
 * Handles MJPEG streaming for all camera types
 *
 * Supports three MJPEG sources based on camera configuration:
 * 1. Native endpoints (reolink, unifi, amcrest) - direct camera MJPEG
 * 2. MediaServer - taps MediaMTX RTSP output for single-connection cameras
 * 3. Snapshots - polls Snap API (handled by reolink endpoint)
 */

export class MJPEGStreamManager {
    constructor() {
        this.activeStreams = new Map();
    }

    /**
     * Start MJPEG stream for a camera
     *
     * For cameras with native MJPEG endpoints (reolink, unifi, amcrest),
     * uses the camera-specific endpoint. For all other cameras (eufy, sv3c,
     * neolink), uses the mediaserver endpoint which taps MediaMTX RTSP.
     *
     * @param {string} cameraId - Camera serial number
     * @param {HTMLElement} streamElement - IMG element to display stream
     * @param {string} cameraType - Camera type (eufy, reolink, unifi, etc.)
     * @param {string} stream - Stream quality ('sub' or 'main')
     */
    async startStream(cameraId, streamElement, cameraType, stream = 'sub') {
        console.log(`[MJPEG] startStream called: cameraId=${cameraId}, cameraType=${cameraType}, stream=${stream}`);
        console.log(`[MJPEG] Element type: ${streamElement ? streamElement.tagName : 'null'}`);

        // MJPEG requires <img> element — if we got a <video>, swap it
        if (streamElement && streamElement.tagName === 'VIDEO') {
            console.log(`[MJPEG] Got <video> element — creating <img> for MJPEG`);
            streamElement.style.display = 'none';
            const streamItem = streamElement.closest('.stream-item');
            let imgEl = streamItem ? streamItem.querySelector('img.mjpeg-stream') : null;
            if (!imgEl) {
                imgEl = document.createElement('img');
                imgEl.className = 'mjpeg-stream stream-video';
                imgEl.style.width = '100%';
                imgEl.style.height = '100%';
                imgEl.style.objectFit = 'contain';
                streamElement.parentNode.appendChild(imgEl);
            } else {
                imgEl.style.display = '';
            }
            streamElement = imgEl;
        }

        // Build URL based on mjpeg_source config (if set) or camera type
        // mjpeg_source: "snap" = direct HTTP snapshots, "mediaserver" = tap MediaMTX
        let mjpegUrl;
        const normalizedType = cameraType ? cameraType.toLowerCase() : '';
        let usesMediaserver = false;

        // Get stream item for data attributes
        const streamItem = document.querySelector(`[data-camera-serial="${cameraId}"]`);
        const originalStreamType = streamItem ? streamItem.dataset.streamType : '';
        const mjpegSource = streamItem ? streamItem.dataset.mjpegSource : '';
        const streamingHub = streamItem ? (streamItem.dataset.streamingHub || 'mediamtx') : 'mediamtx';
        const isNeolink = originalStreamType === 'NEOLINK' || originalStreamType === 'NEOLINK_LL_HLS';

        // Priority 1: Check explicit mjpeg_source config
        if (mjpegSource === 'snap') {
            // Use camera-type-specific snap endpoint
            if (normalizedType === 'reolink') {
                mjpegUrl = `/api/reolink/${cameraId}/stream/mjpeg?stream=${stream || 'sub'}&t=${Date.now()}`;
            } else if (normalizedType === 'sv3c') {
                mjpegUrl = `/api/sv3c/${cameraId}/stream/mjpeg?stream=${stream || 'sub'}&t=${Date.now()}`;
            } else if (normalizedType === 'unifi') {
                mjpegUrl = `/api/unifi/${cameraId}/stream/mjpeg?t=${Date.now()}`;
            } else if (normalizedType === 'amcrest') {
                mjpegUrl = `/api/amcrest/${cameraId}/stream/mjpeg?t=${Date.now()}`;
            } else {
                // Fallback to mediaserver if no snap endpoint exists for this type
                console.warn(`[MJPEG] ${cameraId}: mjpeg_source=snap but no snap endpoint for ${normalizedType}, using mediaserver`);
                usesMediaserver = true;
                mjpegUrl = `/api/mediaserver/${cameraId}/stream/mjpeg?t=${Date.now()}`;
            }
            console.log(`[MJPEG] ${cameraId}: Using snap endpoint (mjpeg_source=snap)`);
        } else if (mjpegSource === 'mediaserver') {
            // Explicit mediaserver tap
            usesMediaserver = true;
            console.log(`[MJPEG] ${cameraId}: Using mediaserver endpoint (mjpeg_source=mediaserver)`);
            mjpegUrl = `/api/mediaserver/${cameraId}/stream/mjpeg?t=${Date.now()}`;
        }
        // Priority 2: Legacy camera-type-based routing (when mjpeg_source not set)
        else if (normalizedType === 'reolink' && !isNeolink) {
            // Native Reolink cameras with direct RTSP access
            mjpegUrl = `/api/reolink/${cameraId}/stream/mjpeg?stream=${stream || 'sub'}&t=${Date.now()}`;
        } else if (isNeolink) {
            // NEOLINK cameras (Reolink E1 via Neolink bridge) - use mediaserver
            usesMediaserver = true;
            console.log(`[MJPEG] ${cameraId} is NEOLINK camera - using mediaserver endpoint`);
            mjpegUrl = `/api/mediaserver/${cameraId}/stream/mjpeg?t=${Date.now()}`;
        } else if (normalizedType === 'unifi') {
            mjpegUrl = `/api/unifi/${cameraId}/stream/mjpeg?t=${Date.now()}`;
        } else if (normalizedType === 'amcrest') {
            mjpegUrl = `/api/amcrest/${cameraId}/stream/mjpeg?t=${Date.now()}`;
        } else if (normalizedType === 'sv3c') {
            // SV3C cameras use hi3510 chipset with CGI snapshot endpoint
            mjpegUrl = `/api/sv3c/${cameraId}/stream/mjpeg?stream=${stream || 'sub'}&t=${Date.now()}`;
        } else {
            // All other camera types (eufy, neolink, etc.) use mediaserver
            usesMediaserver = true;
            console.log(`[MJPEG] Using mediaserver endpoint for ${cameraType} camera ${cameraId}`);
            mjpegUrl = `/api/mediaserver/${cameraId}/stream/mjpeg?t=${Date.now()}`;
        }

        // For mediaserver cameras, ensure the upstream stream is ready before connecting.
        // go2rtc cameras: MediaMTX pulls from go2rtc RTSP on demand — HLS is never started
        //   for these cameras, so we skip the HLS check and connect directly.
        // mediamtx cameras: FFmpeg must publish to MediaMTX first. Poll HLS readiness.
        if (usesMediaserver) {
            if (streamingHub === 'go2rtc') {
                // go2rtc cameras: MediaMTX is configured to pull from go2rtc RTSP (port 8555).
                // The mediaserver MJPEG endpoint connects to MediaMTX RTSP, which triggers the
                // go2rtc pull automatically. No HLS startup needed — connect immediately.
                console.log(`[MJPEG] ${cameraId}: go2rtc hub — skipping HLS poll, connecting to mediaserver directly`);
            } else {
                // mediamtx cameras: FFmpeg publishes HLS+RTSP to MediaMTX. Poll until ready.
                // NOTE: preWarmHLSStreams() in stream.js starts all HLS streams in parallel
                // before calling startStream(), so this is usually a no-op on page load.
                // Still needed for: manual restarts, fullscreen switches, error recovery.

                // Quick check if stream is already publishing (pre-warm succeeded or page reload)
                let streamAlreadyReady = false;
                try {
                    const checkResp = await fetch(`/hls/${cameraId}/index.m3u8`, {
                        method: 'HEAD',
                        cache: 'no-store'
                    });
                    streamAlreadyReady = checkResp.ok;
                } catch (e) {
                    // Network error - stream not ready
                }

                if (streamAlreadyReady) {
                    console.log(`[MJPEG] ${cameraId}: HLS already publishing, skipping start`);
                } else {
                    console.log(`[MJPEG] ${cameraId}: Starting HLS first (required for mediaserver MJPEG)`);
                    try {
                        const response = await fetch(`/api/stream/start/${cameraId}`, {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({ type: 'sub' })
                        });
                        if (!response.ok) {
                            console.warn(`[MJPEG] ${cameraId}: HLS start returned ${response.status}`);
                        } else {
                            const data = await response.json();
                            console.log(`[MJPEG] ${cameraId}: HLS started: ${data.stream_url}`);

                            // Adaptive wait: poll MediaMTX until stream is ready instead of fixed 5s
                            // If HLS already running (desktop keeps streams alive), this returns instantly
                            const streamReady = await this.waitForMediaMTXStream(cameraId, 10000);
                            if (streamReady) {
                                console.log(`[MJPEG] ${cameraId}: MediaMTX stream ready`);
                            } else {
                                console.warn(`[MJPEG] ${cameraId}: MediaMTX poll timeout, trying MJPEG anyway`);
                            }
                        }
                    } catch (e) {
                        console.warn(`[MJPEG] ${cameraId}: Failed to start HLS: ${e.message}`);
                        // Continue anyway - maybe HLS was already running
                    }
                }
            }
        }

        return new Promise((resolve, reject) => {
            const $streamElement = $(streamElement);
            let resolved = false;
            let checkInterval = null;

            // Cleanup function to remove handlers and intervals
            const cleanup = () => {
                $streamElement.off('load.mjpeg error.mjpeg');
                if (checkInterval) {
                    clearInterval(checkInterval);
                    checkInterval = null;
                }
            };

            // Success handler - register in activeStreams and resolve
            const onSuccess = () => {
                if (resolved) return;
                resolved = true;
                cleanup();

                this.activeStreams.set(cameraId, {
                    element: streamElement,
                    url: mjpegUrl,
                    startTime: Date.now()
                });

                console.log(`[MJPEG] ${cameraId}: Stream started successfully`);
                resolve(true);
            };

            // Set up jQuery event handlers
            $streamElement.on('load.mjpeg', onSuccess);

            // Track if we ever received a frame - used to distinguish real errors from startup noise
            let hadFrame = false;
            let errorCount = 0;

            $streamElement.on('error.mjpeg', () => {
                if (resolved) return;
                errorCount++;

                // iOS Safari can fire error events during multipart MJPEG connection establishment.
                // Only treat as fatal if we've never had a frame AND this is a repeated error.
                // The naturalWidth polling will detect actual frames arriving.
                console.log(`[MJPEG] ${cameraId}: Error event #${errorCount} (hadFrame=${hadFrame})`);

                // If we already had a frame, this is a real disconnection
                if (hadFrame) {
                    resolved = true;
                    cleanup();
                    reject(new Error('MJPEG stream disconnected'));
                }
                // Otherwise, don't reject yet - let timeout handle it
                // This allows time for the "Connecting..." frame to arrive
            });

            // Set the source to start loading
            $streamElement.attr('src', mjpegUrl);

            // MJPEG streams may not fire 'load' event reliably in all browsers.
            // Poll for naturalWidth/naturalHeight to detect when first frame arrives.
            // This handles cases where the multipart MJPEG response doesn't trigger load.
            checkInterval = setInterval(() => {
                if (resolved) return;

                const el = streamElement;
                if (el.naturalWidth > 0 && el.naturalHeight > 0) {
                    hadFrame = true;
                    console.log(`[MJPEG] ${cameraId}: Detected frame via naturalWidth check`);
                    onSuccess();
                }
            }, 200);

            // Timeout fallback - if no success or error after 30 seconds, fail
            // MediaServer MJPEG needs time for: HLS start + MediaMTX publish + MJPEG capture start
            setTimeout(() => {
                if (resolved) return;
                resolved = true;
                cleanup();
                reject(new Error('MJPEG stream timeout - no frames received'));
            }, 30000);
        });
    }

    /**
     * Poll MediaMTX until HLS stream is available
     * Returns immediately if stream already exists (e.g., desktop was viewing)
     *
     * @param {string} cameraId - Camera serial number
     * @param {number} maxWaitMs - Maximum time to wait (default 10s)
     * @returns {Promise<boolean>} - true if stream ready, false if timeout
     */
    async waitForMediaMTXStream(cameraId, maxWaitMs = 10000) {
        const startTime = Date.now();
        const pollInterval = 500;  // Check every 500ms

        while (Date.now() - startTime < maxWaitMs) {
            try {
                // HEAD request to HLS playlist - 200 means stream is publishing
                const resp = await fetch(`/hls/${cameraId}/index.m3u8`, {
                    method: 'HEAD',
                    cache: 'no-store'
                });
                if (resp.ok) {
                    const elapsed = Date.now() - startTime;
                    console.log(`[MJPEG] ${cameraId}: MediaMTX ready after ${elapsed}ms`);
                    return true;
                }
            } catch (e) {
                // Network error - keep polling
            }
            await new Promise(r => setTimeout(r, pollInterval));
        }

        console.log(`[MJPEG] ${cameraId}: MediaMTX poll timed out after ${maxWaitMs}ms`);
        return false;
    }

    /**
     * Stop MJPEG stream for a camera
     */
    stopStream(cameraId) {
        const stream = this.activeStreams.get(cameraId);
        if (stream) {
            const $element = $(stream.element);

            // Clear the source
            $element.attr('src', '');

            // Remove any lingering event handlers
            $element.off('load.mjpeg error.mjpeg');

            this.activeStreams.delete(cameraId);
            return true;
        }
        return false;
    }

    /**
     * Check if stream is currently active
     */
    isStreamActive(cameraId) {
        return this.activeStreams.has(cameraId);
    }

    /**
     * Get stream information for a camera
     */
    getStreamInfo(cameraId) {
        return this.activeStreams.get(cameraId) || null;
    }

    /**
     * Stop all active MJPEG streams
     */
    stopAllStreams() {
        this.activeStreams.forEach((stream, cameraId) => {
            this.stopStream(cameraId);
        });
        return true;
    }

    /**
     * Get all active stream IDs
     */
    getActiveStreamIds() {
        return Array.from(this.activeStreams.keys());
    }

    /**
     * Refresh a stream by reloading with cache-busting
     */
    async refreshStream(cameraId) {
        const stream = this.activeStreams.get(cameraId);
        if (!stream) {
            throw new Error(`No active stream found for camera ${cameraId}`);
        }

        // Stop and restart with new timestamp
        this.stopStream(cameraId);

        // Brief delay before restart
        await new Promise(resolve => setTimeout(resolve, 200));

        return this.startStream(cameraId, stream.element);
    }
}