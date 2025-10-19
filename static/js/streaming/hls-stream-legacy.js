/**
 * HLS Stream Manager - Legacy Version for iOS 12.5.7
 * Non-ES6 module version using IIFE pattern
 */
(function (window, $) {
    'use strict';

    /**
     * Detect old iOS versions that have issues with modern HLS.js
     * Returns true if iOS version < 13
     */
    function isOldIOS() {
        var ua = navigator.userAgent;

        if (!/iPad|iPhone|iPod/.test(ua)) {
            return false;
        }

        var match = ua.match(/OS (\d+)_(\d+)/);
        if (!match) {
            return false;
        }

        var majorVersion = parseInt(match[1]);
        return majorVersion < 13;
    }

    /**
     * HLS Stream Manager Class
     */
    function HLSStreamManager() {
        this.hlsInstances = new Map();
        this.activeStreams = new Map();
        this.retryAttempts = new Map();
    }

    /**
     * Start HLS stream for a camera
     */
    HLSStreamManager.prototype.startStream = function (cameraId, videoElement, streamType) {
        var self = this;
        streamType = streamType || 'sub';

        return new Promise(function (resolve, reject) {
            try {
                var playlistUrl = '/streams/' + cameraId + '/playlist.m3u8?type=' + streamType + '&t=' + Date.now();

                // Use native HLS on old iOS even if HLS.js is supported
                var useNativeHLS = isOldIOS() && videoElement.canPlayType('application/vnd.apple.mpegurl');

                if (typeof Hls !== 'undefined' && Hls.isSupported() && !useNativeHLS) {
                    // Modern HLS.js path
                    var hls = new Hls({
                        debug: false,
                        enableWorker: true,
                        lowLatencyMode: true,
                        backBufferLength: 90,
                        maxBufferLength: 30,
                        xhrSetup: function (xhr, url) {
                            xhr.setRequestHeader('Cache-Control', 'no-cache, no-store, must-revalidate');
                            xhr.setRequestHeader('Pragma', 'no-cache');
                            xhr.setRequestHeader('Expires', '0');
                        }
                    });

                    hls.loadSource(playlistUrl);
                    hls.attachMedia(videoElement);

                    hls.on(Hls.Events.MANIFEST_PARSED, function () {
                        self.retryAttempts.delete(cameraId);

                        videoElement.play().catch(function (err) {
                            console.warn('Autoplay prevented:', err);
                        });

                        self.hlsInstances.set(cameraId, hls);
                        self.activeStreams.set(cameraId, {
                            element: videoElement,
                            hls: hls,
                            type: streamType,
                            startTime: Date.now()
                        });

                        resolve(true);
                    });

                    hls.on(Hls.Events.ERROR, function (event, data) {
                        if (data.fatal) {
                            console.error('HLS fatal error for ' + cameraId + ':', data);

                            if (data.details === 'manifestLoadError' && data.response && data.response.code === 404) {
                                var retries = self.retryAttempts.get(cameraId) || 0;
                                if (retries < 20) {
                                    console.log('[HLS] Playlist 404 for ' + cameraId + ', retry ' + (retries + 1) + '/20');
                                    self.retryAttempts.set(cameraId, retries + 1);
                                    setTimeout(function () {
                                        hls.loadSource(playlistUrl);
                                    }, 2000);
                                    return;
                                }
                            }
                            reject(new Error('HLS stream error: ' + data.type));
                        }
                    });

                } else if (videoElement.canPlayType('application/vnd.apple.mpegurl')) {
                    // Native HLS support (Safari, or forced on old iOS)
                    videoElement.src = playlistUrl;

                    var retryCount = 0;
                    var maxRetries = 10;

                    var errorHandler = function () {
                        retryCount++;
                        if (retryCount < maxRetries) {
                            // alert('Playlist not ready, retry ' + retryCount + '/' + maxRetries);
                            setTimeout(function () {
                                videoElement.src = playlistUrl + '&retry=' + retryCount;
                            }, 2000); // Wait 2 seconds before retry
                        } else {
                            // alert('Failed after ' + maxRetries + ' retries');
                            reject(new Error('Failed to load playlist after retries'));
                        }
                    };

                    var loadedHandler = function () {
                        alert('Playlist loaded! Playing...');
                        videoElement.removeEventListener('error', errorHandler);

                        videoElement.play().catch(function (err) {
                            console.warn('Autoplay prevented:', err);
                        });

                        self.activeStreams.set(cameraId, {
                            element: videoElement,
                            type: streamType,
                            startTime: Date.now()
                        });

                        resolve(true);
                    };

                    videoElement.addEventListener('loadedmetadata', loadedHandler);
                    videoElement.addEventListener('error', errorHandler);
                } else {
                    reject(new Error('HLS is not supported in this browser'));
                }

            } catch (error) {
                console.error('Failed to start HLS stream for ' + cameraId + ':', error);
                reject(error);
            }
        });
    };

    /**
     * Stop HLS stream for a camera
     */
    HLSStreamManager.prototype.stopStream = function (cameraId) {
        var self = this;

        return new Promise(function (resolve, reject) {
            $.ajax({
                url: '/api/stream/stop/' + cameraId,
                method: 'POST',
                contentType: 'application/json'
            })
                .done(function () {
                    var hls = self.hlsInstances.get(cameraId);
                    if (hls) {
                        hls.destroy();
                        self.hlsInstances.delete(cameraId);
                    }

                    var stream = self.activeStreams.get(cameraId);
                    if (stream) {
                        stream.element.src = '';
                        self.activeStreams.delete(cameraId);
                    }

                    resolve(true);
                })
                .fail(function (error) {
                    console.error('Failed to stop HLS stream for ' + cameraId + ':', error);

                    // Cleanup local state even if API call fails
                    var hls = self.hlsInstances.get(cameraId);
                    if (hls) {
                        hls.destroy();
                        self.hlsInstances.delete(cameraId);
                    }

                    var stream = self.activeStreams.get(cameraId);
                    if (stream) {
                        stream.element.src = '';
                        self.activeStreams.delete(cameraId);
                    }

                    resolve(false);
                });
        });
    };

    /**
     * Check if stream is currently active
     */
    HLSStreamManager.prototype.isStreamActive = function (cameraId) {
        return this.activeStreams.has(cameraId);
    };

    /**
     * Stop all active HLS streams
     */
    HLSStreamManager.prototype.stopAllStreams = function () {
        var self = this;

        return new Promise(function (resolve) {
            $.ajax({
                url: '/api/streams/stop-all',
                method: 'POST',
                contentType: 'application/json'
            })
                .always(function () {
                    // Cleanup all HLS instances
                    self.hlsInstances.forEach(function (hls) {
                        hls.destroy();
                    });
                    self.hlsInstances.clear();

                    // Clear all video elements
                    self.activeStreams.forEach(function (stream) {
                        stream.element.src = '';
                    });
                    self.activeStreams.clear();

                    resolve(true);
                });
        });
    };

    /**
     * Get stream information for a camera
     */
    HLSStreamManager.prototype.getStreamInfo = function (cameraId) {
        return this.activeStreams.get(cameraId) || null;
    };

    /**
     * Get all active stream IDs
     */
    HLSStreamManager.prototype.getActiveStreamIds = function () {
        return Array.from(this.activeStreams.keys());
    };

    // Expose to global scope
    window.HLSStreamManager = HLSStreamManager;

})(window, jQuery);
