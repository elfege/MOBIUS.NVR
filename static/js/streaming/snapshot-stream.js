/**
 * Snapshot Stream Manager - ES6 + jQuery
 *
 * Lightweight snapshot polling for grid view on iOS/mobile devices.
 * Instead of continuous MJPEG streams, this polls individual snapshots
 * at a configurable interval. Much lighter on resources and more reliable
 * on iOS Safari than multipart MJPEG streams.
 *
 * Visibility Gating (IntersectionObserver):
 * Only polls snapshots for cameras currently visible in the viewport.
 * Off-screen cameras are paused automatically, reducing server load from
 * ~1200 req/min (20 cameras x 1/sec) to ~300 req/min (4-6 visible cameras).
 *
 * Benefits over MJPEG:
 * - Single HTTP request per snapshot (no long-lived connections)
 * - Works reliably on iOS Safari (no multipart parsing issues)
 * - Lower bandwidth (only transfers when polling)
 * - All cameras load simultaneously (no connection queuing)
 */

export class SnapshotStreamManager {
    constructor() {
        this.activeStreams = new Map();
        this.defaultIntervalMs = 1000;  // 1 second between snapshots
        this._observer = null;
        this._initVisibilityObserver();
    }

    /**
     * Initialize IntersectionObserver for viewport-based visibility gating.
     * Cameras scrolled off-screen have their polling paused automatically.
     */
    _initVisibilityObserver() {
        if (typeof IntersectionObserver === 'undefined') {
            console.warn('[Snapshot] IntersectionObserver not available — all cameras will poll continuously');
            return;
        }

        this._observer = new IntersectionObserver((entries) => {
            for (const entry of entries) {
                const cameraId = entry.target.dataset?.cameraSerial ||
                                 entry.target.closest('[data-camera-serial]')?.dataset?.cameraSerial;
                if (!cameraId) continue;

                const stream = this.activeStreams.get(cameraId);
                if (!stream) continue;

                if (entry.isIntersecting && stream.paused) {
                    this._resumePolling(cameraId, stream);
                } else if (!entry.isIntersecting && !stream.paused) {
                    this._pausePolling(cameraId, stream);
                }
            }
        }, {
            // Trigger when any part of the camera tile enters/exits the viewport
            // Add 100px margin so polling starts just before the camera scrolls into view
            rootMargin: '100px',
            threshold: 0
        });
    }

    /**
     * Pause snapshot polling for an off-screen camera
     */
    _pausePolling(cameraId, stream) {
        if (stream.timerId) {
            clearInterval(stream.timerId);
            stream.timerId = null;
        }
        stream.paused = true;
        console.log(`[Snapshot] ${cameraId}: Paused (off-screen)`);
    }

    /**
     * Resume snapshot polling for a camera that scrolled back into view
     */
    _resumePolling(cameraId, stream) {
        stream.paused = false;

        // Load one snapshot immediately for instant visual feedback
        const $element = $(stream.element);
        this.loadSnapshot(cameraId, $element, stream.url);

        // Restart polling interval
        stream.timerId = setInterval(() => {
            this.loadSnapshot(cameraId, $element, stream.url);
        }, stream.intervalMs);

        console.log(`[Snapshot] ${cameraId}: Resumed (visible)`);
    }

    /**
     * Start snapshot polling for a camera
     *
     * @param {string} cameraId - Camera serial number
     * @param {HTMLElement} streamElement - IMG element to display snapshots
     * @param {string} cameraType - Camera type (reolink, eufy, etc.)
     * @param {number} intervalMs - Polling interval in milliseconds
     */
    async startStream(cameraId, streamElement, cameraType, intervalMs = null) {
        const interval = intervalMs || this.defaultIntervalMs;
        console.log(`[Snapshot] Starting polling for ${cameraId} at ${interval}ms interval`);

        // Stop existing stream if any
        if (this.activeStreams.has(cameraId)) {
            this.stopStream(cameraId);
        }

        // Universal snap endpoint - works for all camera types
        // Backend checks reolink, unifi, mediaserver frame buffers automatically
        const snapshotUrl = `/api/snap/${cameraId}`;

        // Only start HLS/RTSP for cameras that need it (mediaserver-based MJPEG)
        // Skip for cameras with native MJPEG endpoints (reolink, sv3c, unifi, amcrest)
        // These cameras have their own snap-polling services that don't require HLS
        const normalizedType = cameraType ? cameraType.toLowerCase() : '';
        const hasNativeMJPEG = ['reolink', 'sv3c', 'unifi', 'amcrest'].includes(normalizedType);

        // Also check data attribute for stream_type - if MJPEG, camera has native support
        const streamItem = document.querySelector(`[data-camera-serial="${cameraId}"]`);
        const configStreamType = streamItem ? streamItem.dataset.streamType : '';
        const isConfiguredMJPEG = configStreamType === 'MJPEG';

        if (!hasNativeMJPEG && !isConfiguredMJPEG) {
            // Only trigger HLS start for cameras that need mediaserver MJPEG
            // (eufy, neolink, etc. - these tap MediaMTX RTSP output)
            console.log(`[Snapshot] ${cameraId}: Starting HLS for mediaserver-based snapshots`);
            try {
                await fetch(`/api/stream/start/${cameraId}`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ type: 'sub' })
                });
            } catch (e) {
                console.warn(`[Snapshot] ${cameraId}: Stream start failed: ${e.message}`);
            }
        } else {
            console.log(`[Snapshot] ${cameraId}: Native MJPEG camera (${normalizedType}) - skipping HLS start`);
        }

        // Create polling timer
        const $element = $(streamElement);

        // Load first snapshot immediately
        this.loadSnapshot(cameraId, $element, snapshotUrl);

        // Start polling interval
        const timerId = setInterval(() => {
            this.loadSnapshot(cameraId, $element, snapshotUrl);
        }, interval);

        // Store stream info
        this.activeStreams.set(cameraId, {
            element: streamElement,
            url: snapshotUrl,
            timerId: timerId,
            intervalMs: interval,
            startTime: Date.now(),
            paused: false
        });

        // Observe the stream element's container for viewport visibility
        if (this._observer) {
            const observeTarget = streamItem || streamElement;
            this._observer.observe(observeTarget);
        }

        console.log(`[Snapshot] ${cameraId}: Polling started`);
        return true;
    }

    /**
     * Load a single snapshot into the element
     */
    async loadSnapshot(cameraId, $element, snapshotUrl) {
        return new Promise((resolve, reject) => {
            // Cache-bust the URL
            const url = `${snapshotUrl}?t=${Date.now()}`;

            // Create a temporary image to preload
            const img = new Image();

            img.onload = () => {
                // Only update if element still exists and stream is active
                if (this.activeStreams.has(cameraId) && $element[0]) {
                    $element.attr('src', url);
                }
                resolve();
            };

            img.onerror = () => {
                console.log(`[Snapshot] ${cameraId}: Failed to load snapshot`);
                reject(new Error('Snapshot load failed'));
            };

            // Start loading
            img.src = url;

            // Timeout after 5 seconds
            setTimeout(() => {
                if (!img.complete) {
                    reject(new Error('Snapshot timeout'));
                }
            }, 5000);
        });
    }

    /**
     * Stop snapshot polling for a camera
     */
    stopStream(cameraId) {
        const stream = this.activeStreams.get(cameraId);
        if (stream) {
            if (stream.timerId) {
                clearInterval(stream.timerId);
            }

            // Stop observing visibility
            if (this._observer && stream.element) {
                const streamItem = document.querySelector(`[data-camera-serial="${cameraId}"]`);
                const observeTarget = streamItem || stream.element;
                this._observer.unobserve(observeTarget);
            }

            this.activeStreams.delete(cameraId);
            console.log(`[Snapshot] ${cameraId}: Polling stopped`);
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
     * Stop all active snapshot streams
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
     * Update polling interval for a camera
     */
    setInterval(cameraId, intervalMs) {
        const stream = this.activeStreams.get(cameraId);
        if (stream) {
            // Stop current timer
            if (stream.timerId) {
                clearInterval(stream.timerId);
            }

            // Start new timer with updated interval (only if not paused)
            const $element = $(stream.element);
            stream.intervalMs = intervalMs;

            if (!stream.paused) {
                stream.timerId = setInterval(() => {
                    this.loadSnapshot(cameraId, $element, stream.url);
                }, intervalMs);
            }

            console.log(`[Snapshot] ${cameraId}: Interval updated to ${intervalMs}ms`);
            return true;
        }
        return false;
    }

    /**
     * Set default polling interval for new streams
     */
    setDefaultInterval(intervalMs) {
        this.defaultIntervalMs = intervalMs;
        console.log(`[Snapshot] Default interval set to ${intervalMs}ms`);
    }
}
