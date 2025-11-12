/**
 * MJPEG Stream Manager - Legacy Version for iOS 12.5.7
 * Non-ES6 module version using IIFE pattern
 */
(function(window, $) {
    'use strict';

    /**
     * MJPEG Stream Manager Class
     */
    function MJPEGStreamManager() {
        this.activeStreams = new Map();
    }

    /**
     * Start MJPEG stream for a camera
     */
    MJPEGStreamManager.prototype.startStream = function(cameraId, imgElement) {
        var self = this;

        return new Promise(function(resolve, reject) {
            try {
                var streamUrl = '/api/unifi/' + cameraId + '/stream/mjpeg?t=' + Date.now();

                imgElement.src = streamUrl;

                var loadHandler = function() {
                    self.activeStreams.set(cameraId, {
                        element: imgElement,
                        startTime: Date.now()
                    });
                    resolve(true);
                };

                var errorHandler = function() {
                    reject(new Error('Failed to load MJPEG stream'));
                };

                $(imgElement).one('load', loadHandler);
                $(imgElement).one('error', errorHandler);

            } catch (error) {
                console.error('Failed to start MJPEG stream for ' + cameraId + ':', error);
                reject(error);
            }
        });
    };

    /**
     * Stop MJPEG stream for a camera
     */
    MJPEGStreamManager.prototype.stopStream = function(cameraId) {
        var stream = this.activeStreams.get(cameraId);
        if (stream) {
            stream.element.src = '';
            this.activeStreams.delete(cameraId);
        }
        return Promise.resolve(true);
    };

    /**
     * Check if stream is currently active
     */
    MJPEGStreamManager.prototype.isStreamActive = function(cameraId) {
        return this.activeStreams.has(cameraId);
    };

    /**
     * Stop all active MJPEG streams
     */
    MJPEGStreamManager.prototype.stopAllStreams = function() {
        var self = this;
        this.activeStreams.forEach(function(stream) {
            stream.element.src = '';
        });
        this.activeStreams.clear();
        return Promise.resolve(true);
    };

    /**
     * Get stream information for a camera
     */
    MJPEGStreamManager.prototype.getStreamInfo = function(cameraId) {
        return this.activeStreams.get(cameraId) || null;
    };

    /**
     * Get all active stream IDs
     */
    MJPEGStreamManager.prototype.getActiveStreamIds = function() {
        return Array.from(this.activeStreams.keys());
    };

    // Expose to global scope
    window.MJPEGStreamManager = MJPEGStreamManager;

})(window, jQuery);
