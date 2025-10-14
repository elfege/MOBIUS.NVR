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
    async startStream(cameraId, streamElement) {
        const mjpegUrl = `/api/unifi/${cameraId}/stream/mjpeg?t=${Date.now()}`;

        return new Promise((resolve, reject) => {
            const $streamElement = $(streamElement);

            // Set up jQuery event handlers
            $streamElement.on('load.mjpeg', () => {
                // Remove the event handler after success
                $streamElement.off('load.mjpeg error.mjpeg');

                this.activeStreams.set(cameraId, {
                    element: streamElement,
                    url: mjpegUrl,
                    startTime: Date.now()
                });

                resolve(true);
            });

            $streamElement.on('error.mjpeg', () => {
                // Remove the event handler after error
                $streamElement.off('load.mjpeg error.mjpeg');

                reject(new Error('MJPEG stream failed to load'));
            });

            // Set the source to start loading
            $streamElement.attr('src', mjpegUrl);
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
