/**
 * MJPEG Stream Manager - ES6 + jQuery
 * Handles MJPEG streaming for UniFi cameras
 */

export class MJPEGStreamManager {
    constructor() {
        this.activeStreams = new Map();
    }

    /**
     * Start MJPEG stream for a camera
     */
    async startStream(cameraId, streamElement, cameraType, stream = 'sub') {
        console.log(`[MJPEG] startStream called: cameraId=${cameraId}, cameraType=${cameraType}, stream=${stream}`);

        // Build URL based on camera type
        let mjpegUrl;
        if (cameraType === 'reolink') {
            mjpegUrl = `/api/reolink/${cameraId}/stream/mjpeg?stream=${stream || 'sub'}&t=${Date.now()}`;
        } else if (cameraType === 'unifi') {
            mjpegUrl = `/api/unifi/${cameraId}/stream/mjpeg?t=${Date.now()}`;
        } else if (cameraType === 'amcrest') {
            mjpegUrl = `/api/amcrest/${cameraId}/stream/mjpeg?t=${Date.now()}`;
        } else {
            throw new Error(`Unsupported camera type for MJPEG: ${cameraType}`);
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

            $streamElement.on('error.mjpeg', () => {
                if (resolved) return;
                resolved = true;
                cleanup();
                reject(new Error('MJPEG stream failed to load'));
            });

            // Set the source to start loading
            $streamElement.attr('src', mjpegUrl);

            // MJPEG streams may not fire 'load' event reliably in all browsers.
            // Poll for naturalWidth/naturalHeight to detect when first frame arrives.
            // This handles cases where the multipart MJPEG response doesn't trigger load.
            // Reduced polling interval from 200ms to 100ms for faster frame detection
            checkInterval = setInterval(() => {
                if (resolved) return;

                const el = streamElement;
                if (el.naturalWidth > 0 && el.naturalHeight > 0) {
                    console.log(`[MJPEG] ${cameraId}: Detected frame via naturalWidth check`);
                    onSuccess();
                }
            }, 100);

            // Timeout fallback - if no success or error after 10 seconds, fail
            setTimeout(() => {
                if (resolved) return;
                resolved = true;
                cleanup();
                reject(new Error('MJPEG stream timeout - no frames received'));
            }, 10000);
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