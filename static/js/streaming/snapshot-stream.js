/**
 * Snapshot Stream Manager - ES6 + jQuery
 *
 * Lightweight snapshot polling for grid view on iOS/mobile devices.
 * Instead of continuous MJPEG streams, this polls individual snapshots
 * at a configurable interval. Much lighter on resources and more reliable
 * on iOS Safari than multipart MJPEG streams.
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

        // Ensure MJPEG capture is running for this camera so we have frames
        // This triggers the backend to start capturing if not already
        try {
            await fetch(`/api/stream/start/${cameraId}`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ type: 'sub' })
            });
        } catch (e) {
            console.warn(`[Snapshot] ${cameraId}: Stream start failed: ${e.message}`);
        }

        // Create polling timer
        const $element = $(streamElement);
        let consecutiveErrors = 0;
        const maxErrors = 5;

        // Load first snapshot immediately
        this.loadSnapshot(cameraId, $element, snapshotUrl);

        // Start polling interval
        const timerId = setInterval(() => {
            this.loadSnapshot(cameraId, $element, snapshotUrl)
                .then(() => {
                    consecutiveErrors = 0;  // Reset on success
                })
                .catch(() => {
                    consecutiveErrors++;
                    if (consecutiveErrors >= maxErrors) {
                        console.warn(`[Snapshot] ${cameraId}: ${maxErrors} consecutive errors, pausing`);
                        // Don't stop completely - just slow down polling
                        // The health monitor will handle recovery
                    }
                });
        }, interval);

        // Store stream info
        this.activeStreams.set(cameraId, {
            element: streamElement,
            url: snapshotUrl,
            timerId: timerId,
            intervalMs: interval,
            startTime: Date.now()
        });

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

            // Start new timer with updated interval
            const $element = $(stream.element);
            stream.intervalMs = intervalMs;
            stream.timerId = setInterval(() => {
                this.loadSnapshot(cameraId, $element, stream.url);
            }, intervalMs);

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
