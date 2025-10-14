/**
 * FLV Stream Manager - ES6 + jQuery
 * Handles FLV/RTMP streaming for Reolink cameras using flv.js
 */

export class FLVStreamManager {
    constructor() {
        this.flvInstances = new Map();

        // Check if flv.js is loaded
        if (typeof flvjs === 'undefined') {
            console.warn('flv.js library not loaded - FLV streaming unavailable');
        }
    }

    /**
     * Start FLV stream for a camera
     */
    async startStream(cameraSerial, streamElement) {
        try {
            // Check if flv.js is available
            if (typeof flvjs === 'undefined') {
                throw new Error('flv.js library not loaded');
            }

            if (!flvjs.isSupported()) {
                throw new Error('FLV is not supported in this browser');
            }

            // Create FLV player
            const flvPlayer = flvjs.createPlayer({
                type: 'flv',
                url: `/api/camera/${cameraSerial}/flv`,
                isLive: true,
                hasAudio: true,
                hasVideo: true
            }, {
                enableWorker: false,
                enableStashBuffer: false,
                stashInitialSize: 128,
                lazyLoad: false,
                autoCleanupSourceBuffer: true
            });

            flvPlayer.attachMediaElement(streamElement);
            flvPlayer.load();

            // Wait for player to be ready
            await new Promise((resolve, reject) => {
                flvPlayer.on(flvjs.Events.METADATA_ARRIVED, () => {
                    resolve();
                });

                flvPlayer.on(flvjs.Events.ERROR, (errorType, errorDetail, errorInfo) => {
                    console.error('FLV Error:', errorType, errorDetail, errorInfo);
                    reject(new Error(`FLV Error: ${errorType} - ${errorDetail}`));
                });

                // Timeout after 10 seconds
                setTimeout(() => reject(new Error('FLV stream timeout')), 10000);
            });

            // Start playback
            await flvPlayer.play();

            this.flvInstances.set(cameraSerial, flvPlayer);

            console.log(`FLV stream started for ${cameraSerial}`);
            return true;

        } catch (error) {
            console.error(`Failed to start FLV stream for ${cameraSerial}:`, error);
            throw error;
        }
    }

    /**
     * Stop FLV stream for a camera
     */
    stopStream(cameraSerial) {
        const player = this.flvInstances.get(cameraSerial);
        if (player) {
            try {
                player.pause();
                player.unload();
                player.detachMediaElement();
                player.destroy();
                this.flvInstances.delete(cameraSerial);
                console.log(`FLV stream stopped for ${cameraSerial}`);
                return true;
            } catch (error) {
                console.error(`Error stopping FLV stream for ${cameraSerial}:`, error);
                return false;
            }
        }
        return false;
    }

    /**
     * Check if stream is currently active
     */
    isStreamActive(cameraSerial) {
        return this.flvInstances.has(cameraSerial);
    }

    /**
     * Get player instance for a camera
     */
    getPlayer(cameraSerial) {
        return this.flvInstances.get(cameraSerial) || null;
    }

    /**
     * Stop all active FLV streams
     */
    stopAllStreams() {
        this.flvInstances.forEach((player, cameraSerial) => {
            this.stopStream(cameraSerial);
        });
        return true;
    }

    /**
     * Get all active stream IDs
     */
    getActiveStreamIds() {
        return Array.from(this.flvInstances.keys());
    }
}
