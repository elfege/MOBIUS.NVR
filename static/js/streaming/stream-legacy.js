/**
 * Multi-Stream Manager - Legacy Version for iOS 12.5.7
 * Non-ES6 module version using IIFE pattern
 */
(function (window, $) {
    'use strict';

    /**
     * Multi Stream Manager Class
     */
    function MultiStreamManager() {
        this.$container = $('#streams-container');
        this.$streamCount = $('#stream-count');
        this.$fullscreenOverlay = $('#fullscreen-overlay');
        this.$fullscreenVideo = $('#fullscreen-video');
        this.$fullscreenTitle = $('#fullscreen-title');
        this.$fullscreenClose = $('#fullscreen-close');

        this.hlsManager = new window.HLSStreamManager();
        this.mjpegManager = new window.MJPEGStreamManager();

        this.fullscreenHls = null;
        this.health = null;

        this.init();
    }

    MultiStreamManager.prototype.init = function () {

        this.setupLayout();
        this.setupEventListeners();
        this.startAllStreams();
        this.updateStreamCount();
    };

    MultiStreamManager.prototype.setupLayout = function () {
        var $streamItems = this.$container.find('.stream-item');
        var count = $streamItems.length;

        var cols;
        if (count <= 1) cols = 1;
        else if (count <= 4) cols = 2;
        else if (count <= 9) cols = 3;
        else if (count <= 16) cols = 4;
        else cols = 5;

        this.$container.attr('class', 'streams-container grid-' + cols);
        $streamItems.css('aspect-ratio', '16/9');
    };

    MultiStreamManager.prototype.setupEventListeners = function () {
        var self = this;

        // Fullscreen button click handler
        this.$container.on('click', '.stream-fullscreen-btn', function (e) {
            e.stopPropagation();
            var $streamItem = $(e.currentTarget).closest('.stream-item');
            if ($streamItem.length) {
                var serial = $streamItem.data('camera-serial');
                var name = $streamItem.data('camera-name');
                var streamType = $streamItem.data('stream-type');
                self.openFullscreen(serial, name, streamType);
            }
        });

        // Close fullscreen
        this.$fullscreenClose.on('click', function () {
            self.closeFullscreen();
        });

        this.$fullscreenOverlay.on('click', function (e) {
            if ($(e.target).is(self.$fullscreenOverlay)) {
                self.closeFullscreen();
            }
        });

        // Escape key closes fullscreen
        $(document).on('keydown', function (e) {
            if (e.key === 'Escape' && self.$fullscreenOverlay.hasClass('active')) {
                self.closeFullscreen();
            }
        });

        // Stream control buttons
        this.$container.on('click', '.start-stream-btn', function (e) {
            e.stopPropagation();
            var $item = $(e.currentTarget).closest('.stream-item');
            var serial = $item.data('camera-serial');
            var cameraType = $item.data('camera-type');
            var streamType = $item.data('stream-type');
            self.startStream(serial, $item, cameraType, streamType);
        });

        this.$container.on('click', '.stop-stream-btn', function (e) {
            e.stopPropagation();
            var $item = $(e.currentTarget).closest('.stream-item');
            var serial = $item.data('camera-serial');
            self.stopStream(serial, $item);
        });

        this.$container.on('click', '.refresh-stream-btn', function (e) {
            e.stopPropagation();
            var $item = $(e.currentTarget).closest('.stream-item');
            var serial = $item.data('camera-serial');
            var cameraType = $item.data('camera-type');
            var streamType = $item.data('stream-type');
            self.restartStream(serial, $item, cameraType, streamType);
        });
    };

    MultiStreamManager.prototype.startAllStreams = function () {
        var self = this;
        var $items = this.$container.find('.stream-item');

        $items.each(function () {
            var $item = $(this);
            var serial = $item.data('camera-serial');
            var cameraType = $item.data('camera-type');
            var streamType = $item.data('stream-type');

            self.startStream(serial, $item, cameraType, streamType).catch(function (error) {
                console.error('Failed to start stream for ' + serial + ':', error);
                self.setStreamStatus($item, 'error', 'Failed to load');
            });
        });
    };

    MultiStreamManager.prototype.startStream = function (serial, $streamItem, cameraType, streamType) {
        var self = this;
        var streamElement = $streamItem.find('.stream-video')[0];
        var $loadingIndicator = $streamItem.find('.loading-indicator');

        $loadingIndicator.show();
        this.setStreamStatus($streamItem, 'loading', 'Starting...');

        var promise;
        if (streamType === 'mjpeg_proxy') {
            promise = this.mjpegManager.startStream(serial, streamElement);
        } else if (streamType === 'HLS' || streamType === 'LL_HLS') {
            promise = this.hlsManager.startStream(serial, streamElement, 'sub');
        } else {
            return Promise.reject(new Error('Unknown stream type: ' + streamType));
        }

        return promise.then(function (success) {
            if (success) {
                $loadingIndicator.hide();
                self.setStreamStatus($streamItem, 'live', 'Live');
                self.updateStreamButtons($streamItem, true);
            }
            return success;
        });
    };

    MultiStreamManager.prototype.stopStream = function (serial, $streamItem) {
        var self = this;
        var streamType = $streamItem.data('stream-type');

        var promise;
        if (streamType === 'mjpeg_proxy') {
            promise = this.mjpegManager.stopStream(serial);
        } else if (streamType === 'HLS' || streamType === 'LL_HLS') {
            promise = this.hlsManager.stopStream(serial);
        } else {
            return Promise.resolve(false);
        }

        return promise.then(function () {
            self.setStreamStatus($streamItem, 'stopped', 'Stopped');
            self.updateStreamButtons($streamItem, false);
        });
    };

    MultiStreamManager.prototype.restartStream = function (serial, $streamItem, cameraType, streamType) {
        var self = this;
        return this.stopStream(serial, $streamItem).then(function () {
            return self.startStream(serial, $streamItem, cameraType, streamType);
        });
    };

    MultiStreamManager.prototype.setStreamStatus = function ($item, state, text) {
        var $indicator = $item.find('.stream-indicator');
        $indicator.attr('class', 'stream-indicator ' + state);
        $indicator.find('span').text(text);
    };

    MultiStreamManager.prototype.updateStreamButtons = function ($item, isPlaying) {
        $item.find('.start-stream-btn').prop('disabled', isPlaying);
        $item.find('.stop-stream-btn').prop('disabled', !isPlaying);
    };

    MultiStreamManager.prototype.updateStreamCount = function () {
        var count = this.$container.find('.stream-item').length;
        this.$streamCount.text(count + ' camera' + (count !== 1 ? 's' : ''));
    };

    MultiStreamManager.prototype.openFullscreen = function (serial, name, streamType) {
        this.$fullscreenTitle.text(name);
        this.$fullscreenOverlay.addClass('active');

        var playlistUrl = '/streams/' + serial + '/playlist.m3u8?type=main&t=' + Date.now();

        if (streamType === 'HLS' || streamType === 'LL_HLS') {
            this.destroyFullscreenHls();

            if (typeof Hls !== 'undefined' && Hls.isSupported()) {
                this.fullscreenHls = new Hls({
                    debug: false,
                    enableWorker: true,
                    lowLatencyMode: true
                });

                this.fullscreenHls.loadSource(playlistUrl);
                this.fullscreenHls.attachMedia(this.$fullscreenVideo[0]);

                var self = this;
                this.$fullscreenVideo.one('loadedmetadata.fullscreen', function () {
                    self.$fullscreenVideo[0].play().catch(function () { });
                });
            } else {
                this.$fullscreenVideo[0].src = playlistUrl;
                var self = this;
                this.$fullscreenVideo.one('loadedmetadata.fullscreen', function () {
                    self.$fullscreenVideo[0].play().catch(function () { });
                });
            }
        }
    };

    MultiStreamManager.prototype.closeFullscreen = function () {
        this.$fullscreenOverlay.removeClass('active');
        this.destroyFullscreenHls();
        this.$fullscreenVideo.attr('src', '');
        this.$fullscreenVideo.off('loadedmetadata.fullscreen');
    };

    MultiStreamManager.prototype.destroyFullscreenHls = function () {
        if (this.fullscreenHls) {
            this.fullscreenHls.destroy();
            this.fullscreenHls = null;
        }
    };

    // Initialize on document ready
    $(document).ready(function () {
        new MultiStreamManager();

        // Auto-collapse header after 5 seconds
        setTimeout(function () {
            var headerToggle = document.getElementById('header-toggle');
            if (headerToggle && headerToggle.checked) {
                headerToggle.checked = false;
            }
        }, 5000);
    });

    // Expose to global scope
    window.MultiStreamManager = MultiStreamManager;

})(window, jQuery);
