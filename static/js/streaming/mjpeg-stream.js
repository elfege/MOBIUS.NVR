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

        // MJPEG requires <img> element - warn if we got a <video>
        if (streamElement && streamElement.tagName === 'VIDEO') {
            console.warn(`[MJPEG] WARNING: Got <video> element instead of <img>! MJPEG will not work.`);
        }

        // Build URL based on camera type
        // Cameras with native MJPEG endpoints use their specific API
        // All other cameras use mediaserver (taps MediaMTX RTSP output)
        let mjpegUrl;
        const normalizedType = cameraType ? cameraType.toLowerCase() : '';
        let usesMediaserver = false;

        // Check if this is a NEOLINK camera (Reolink E1 uses Neolink bridge, not native MJPEG)
        // NEOLINK cameras don't have native MJPEG endpoints - must use mediaserver
        const streamItem = document.querySelector(`[data-camera-serial="${cameraId}"]`);
        const originalStreamType = streamItem ? streamItem.dataset.streamType : '';
        const isNeolink = originalStreamType === 'NEOLINK' || originalStreamType === 'NEOLINK_LL_HLS';

        if (normalizedType === 'reolink' && !isNeolink) {
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
        } else {
            // All other camera types (eufy, sv3c, neolink, etc.) use mediaserver
            // This taps the existing MediaMTX RTSP stream and extracts JPEG frames
            // CRITICAL: MediaMTX must have the stream published first (via HLS FFmpeg)
            usesMediaserver = true;
            console.log(`[MJPEG] Using mediaserver endpoint for ${cameraType} camera ${cameraId}`);
            mjpegUrl = `/api/mediaserver/${cameraId}/stream/mjpeg?t=${Date.now()}`;
        }

        // For mediaserver cameras, ensure HLS stream is started first
        // MediaServer MJPEG taps the MediaMTX RTSP output from the HLS FFmpeg
        if (usesMediaserver) {
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
                    // Give FFmpeg time to start publishing to MediaMTX
                    // Eufy cameras can take 5-10s to establish RTSP connection
                    await new Promise(r => setTimeout(r, 5000));
                }
            } catch (e) {
                console.warn(`[MJPEG] ${cameraId}: Failed to start HLS: ${e.message}`);
                // Continue anyway - maybe HLS was already running
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