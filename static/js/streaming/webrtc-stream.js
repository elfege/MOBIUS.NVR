/**
 * WebRTC Stream Manager - ES6 Module
 * Handles WebRTC streaming via MediaMTX WHEP protocol for sub-second latency
 *
 * WHEP (WebRTC-HTTP Egress Protocol) flow:
 * 1. Browser creates RTCPeerConnection with recvonly video transceiver
 * 2. Browser sends SDP offer to MediaMTX /camera_id/whep endpoint
 * 3. MediaMTX responds with SDP answer
 * 4. ICE candidates are exchanged (Trickle ICE or bundled)
 * 5. Browser receives RTP video directly from MediaMTX
 *
 * Latency: ~200-500ms (vs 2-4s for LL-HLS)
 */

export class WebRTCStreamManager {
    constructor() {
        // Map: cameraId -> { pc: RTCPeerConnection, element: HTMLVideoElement, startTime: number }
        this.activeStreams = new Map();
        this.retryAttempts = new Map();

        // MediaMTX WebRTC port (configured in mediamtx.yml)
        this.webrtcPort = 8889;
    }

    /**
     * Create latency overlay badge (same pattern as HLS manager)
     */
    _ensureLatencyOverlay(videoEl) {
        if (videoEl._latencyOverlay) return videoEl._latencyOverlay;

        const badge = document.createElement('div');
        badge.className = 'latency-badge webrtc-badge';
        Object.assign(badge.style, {
            position: 'absolute',
            left: '8px',
            bottom: '8px',
            padding: '2px 6px',
            fontSize: '12px',
            lineHeight: '16px',
            background: 'rgba(0,128,0,0.7)',  // Green for WebRTC (low latency)
            color: '#fff',
            borderRadius: '6px',
            fontFamily: 'system-ui, -apple-system, Segoe UI, Roboto, sans-serif',
            pointerEvents: 'none',
            zIndex: 2,
        });

        const parent = videoEl.parentElement || document.body;
        parent.style.position = parent.style.position || 'relative';
        parent.appendChild(badge);
        videoEl._latencyOverlay = badge;
        return badge;
    }

    /**
     * Attach latency meter for WebRTC
     * WebRTC doesn't have programDateTime like HLS, so we estimate based on
     * video.currentTime progression and connection stats
     */
    _attachLatencyMeter(pc, videoEl) {
        const overlay = this._ensureLatencyOverlay(videoEl);

        // Update latency display from WebRTC stats
        if (videoEl._latencyTimer) clearInterval(videoEl._latencyTimer);
        videoEl._latencyTimer = setInterval(async () => {
            try {
                const stats = await pc.getStats();
                let jitter = 0;
                let packetsLost = 0;
                let packetsReceived = 0;

                stats.forEach(report => {
                    if (report.type === 'inbound-rtp' && report.kind === 'video') {
                        jitter = report.jitter || 0;
                        packetsLost = report.packetsLost || 0;
                        packetsReceived = report.packetsReceived || 0;
                    }
                });

                // Display estimated latency (jitter * 1000 for ms, plus base ~200ms)
                const estimatedLatency = Math.round(200 + (jitter * 1000));
                const lossRate = packetsReceived > 0
                    ? ((packetsLost / (packetsLost + packetsReceived)) * 100).toFixed(1)
                    : 0;

                overlay.textContent = `~${estimatedLatency}ms`;
                overlay.title = `WebRTC | Loss: ${lossRate}%`;
                overlay.style.display = '';
            } catch (e) {
                // Stats not available yet
                overlay.textContent = 'WebRTC';
            }
        }, 1000);  // Update every second (stats polling is heavier than HLS)

        videoEl._latencyDetach = () => {
            if (videoEl._latencyTimer) {
                clearInterval(videoEl._latencyTimer);
                videoEl._latencyTimer = null;
            }
            if (videoEl._latencyOverlay) {
                videoEl._latencyOverlay.textContent = '';
            }
        };
    }

    /**
     * Start WebRTC stream for a camera via WHEP protocol
     *
     * @param {string} cameraId - Camera identifier (used as MediaMTX path)
     * @param {HTMLVideoElement} videoElement - Video element to attach stream
     * @param {string} streamType - 'sub' or 'main' (sub is default for grid view)
     * @returns {Promise<boolean>} - Resolves true on success
     */
    async startStream(cameraId, videoElement, streamType = 'sub') {
        console.log(`[WebRTC] Starting stream for ${cameraId} (${streamType})`);

        try {
            // Build WHEP URL - MediaMTX serves WebRTC on separate port
            // Path format: camera_id for sub stream, camera_id_main for main stream
            const streamPath = streamType === 'main' ? `${cameraId}_main` : cameraId;
            const whepUrl = `http://${window.location.hostname}:${this.webrtcPort}/${streamPath}/whep`;

            // Create RTCPeerConnection with minimal config (LAN-only, no STUN needed)
            const pc = new RTCPeerConnection({
                iceServers: []  // No STUN/TURN for LAN-only
            });

            // Add receive-only video transceiver
            pc.addTransceiver('video', { direction: 'recvonly' });
            pc.addTransceiver('audio', { direction: 'recvonly' });

            // Handle incoming track (video/audio)
            pc.ontrack = (event) => {
                console.log(`[WebRTC] ${cameraId}: Received track: ${event.track.kind}`);
                if (event.track.kind === 'video') {
                    videoElement.srcObject = event.streams[0];
                    videoElement.play().catch(err => {
                        console.warn(`[WebRTC] ${cameraId}: Autoplay prevented:`, err);
                    });
                }
            };

            // Monitor ICE connection state
            pc.oniceconnectionstatechange = () => {
                console.log(`[WebRTC] ${cameraId}: ICE state: ${pc.iceConnectionState}`);

                if (pc.iceConnectionState === 'connected' || pc.iceConnectionState === 'completed') {
                    // Dispatch event when stream is live
                    if (!videoElement._firstFrameReceived) {
                        videoElement._firstFrameReceived = true;
                        console.log(`[WebRTC] ${cameraId}: Stream is live`);
                        videoElement.dispatchEvent(new CustomEvent('streamlive', { detail: { cameraId } }));
                    }
                } else if (pc.iceConnectionState === 'disconnected' || pc.iceConnectionState === 'failed') {
                    console.error(`[WebRTC] ${cameraId}: Connection ${pc.iceConnectionState}`);
                    videoElement.dispatchEvent(new CustomEvent('streamerror', {
                        detail: { cameraId, error: `ICE ${pc.iceConnectionState}` }
                    }));
                }
            };

            // Create SDP offer
            const offer = await pc.createOffer();
            await pc.setLocalDescription(offer);

            // Wait for ICE gathering to complete (or timeout)
            await this._waitForIceGathering(pc, 2000);

            // Send offer to MediaMTX WHEP endpoint
            console.log(`[WebRTC] ${cameraId}: Sending offer to ${whepUrl}`);
            const response = await fetch(whepUrl, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/sdp'
                },
                body: pc.localDescription.sdp
            });

            if (!response.ok) {
                const errorText = await response.text();
                throw new Error(`WHEP request failed: ${response.status} - ${errorText}`);
            }

            // Get SDP answer from MediaMTX
            const answerSdp = await response.text();
            await pc.setRemoteDescription({
                type: 'answer',
                sdp: answerSdp
            });

            // Store stream info
            this.activeStreams.set(cameraId, {
                pc: pc,
                element: videoElement,
                type: streamType,
                startTime: Date.now(),
                whepUrl: whepUrl
            });

            // Attach latency meter
            this._attachLatencyMeter(pc, videoElement);

            // Reset retry counter on success
            this.retryAttempts.delete(cameraId);

            console.log(`[WebRTC] ${cameraId}: Stream setup complete, waiting for media`);
            return true;

        } catch (error) {
            console.error(`[WebRTC] Failed to start stream for ${cameraId}:`, error);

            // Clean up on failure
            const stream = this.activeStreams.get(cameraId);
            if (stream?.pc) {
                stream.pc.close();
            }
            this.activeStreams.delete(cameraId);

            throw error;
        }
    }

    /**
     * Wait for ICE gathering to complete or timeout
     */
    _waitForIceGathering(pc, timeoutMs) {
        return new Promise((resolve) => {
            if (pc.iceGatheringState === 'complete') {
                resolve();
                return;
            }

            const timeout = setTimeout(() => {
                console.log('[WebRTC] ICE gathering timeout, proceeding with current candidates');
                resolve();
            }, timeoutMs);

            pc.onicegatheringstatechange = () => {
                if (pc.iceGatheringState === 'complete') {
                    clearTimeout(timeout);
                    resolve();
                }
            };
        });
    }

    /**
     * Stop WebRTC stream for a camera
     */
    stopStream(cameraId) {
        try {
            const stream = this.activeStreams.get(cameraId);
            if (stream) {
                // Close RTCPeerConnection
                if (stream.pc) {
                    stream.pc.close();
                }

                // Clear video element
                if (stream.element) {
                    stream.element.srcObject = null;
                    stream.element.pause();

                    // Clean up latency meter
                    if (stream.element._latencyDetach) {
                        stream.element._latencyDetach();
                        delete stream.element._latencyDetach;
                    }

                    // Reset first frame flag
                    delete stream.element._firstFrameReceived;
                }

                this.activeStreams.delete(cameraId);
                console.log(`[WebRTC] Stopped stream for ${cameraId}`);
                return true;
            }
            return false;
        } catch (error) {
            console.error(`[WebRTC] Failed to stop stream for ${cameraId}:`, error);

            // Force cleanup
            this.activeStreams.delete(cameraId);
            return false;
        }
    }

    /**
     * Check if stream is currently active
     */
    isStreamActive(cameraId) {
        const stream = this.activeStreams.get(cameraId);
        if (!stream) return false;

        // Also check if RTCPeerConnection is still connected
        const state = stream.pc?.iceConnectionState;
        return state === 'connected' || state === 'completed' || state === 'checking' || state === 'new';
    }

    /**
     * Stop all active WebRTC streams
     */
    stopAllStreams() {
        try {
            this.activeStreams.forEach((stream, cameraId) => {
                if (stream.pc) {
                    stream.pc.close();
                }
                if (stream.element) {
                    stream.element.srcObject = null;
                    stream.element.pause();

                    if (stream.element._latencyDetach) {
                        stream.element._latencyDetach();
                        delete stream.element._latencyDetach;
                    }
                }
            });
            this.activeStreams.clear();
            console.log('[WebRTC] Stopped all streams');
            return true;
        } catch (error) {
            console.error('[WebRTC] Failed to stop all streams:', error);
            this.activeStreams.clear();
            return false;
        }
    }

    /**
     * Get stream information for a camera
     */
    getStreamInfo(cameraId) {
        return this.activeStreams.get(cameraId) || null;
    }

    /**
     * Get all active stream IDs
     */
    getActiveStreamIds() {
        return Array.from(this.activeStreams.keys());
    }

    /**
     * Force refresh a stream by closing and reopening connection
     */
    async forceRefreshStream(cameraId, videoElement) {
        const current = this.activeStreams.get(cameraId);
        const streamType = current?.type ?? 'sub';

        // Stop existing stream
        this.stopStream(cameraId);

        // Brief delay before restart
        await new Promise(resolve => setTimeout(resolve, 200));

        // Restart stream
        return this.startStream(cameraId, videoElement, streamType);
    }

    /**
     * Get WebRTC connection statistics for a camera
     */
    async getStats(cameraId) {
        const stream = this.activeStreams.get(cameraId);
        if (!stream?.pc) return null;

        try {
            const stats = await stream.pc.getStats();
            const result = {
                video: null,
                audio: null,
                connection: null
            };

            stats.forEach(report => {
                if (report.type === 'inbound-rtp') {
                    if (report.kind === 'video') {
                        result.video = {
                            packetsReceived: report.packetsReceived,
                            packetsLost: report.packetsLost,
                            bytesReceived: report.bytesReceived,
                            framesDecoded: report.framesDecoded,
                            framesDropped: report.framesDropped,
                            jitter: report.jitter
                        };
                    } else if (report.kind === 'audio') {
                        result.audio = {
                            packetsReceived: report.packetsReceived,
                            packetsLost: report.packetsLost,
                            bytesReceived: report.bytesReceived,
                            jitter: report.jitter
                        };
                    }
                } else if (report.type === 'candidate-pair' && report.state === 'succeeded') {
                    result.connection = {
                        localCandidateType: report.localCandidateType,
                        remoteCandidateType: report.remoteCandidateType,
                        currentRoundTripTime: report.currentRoundTripTime,
                        availableOutgoingBitrate: report.availableOutgoingBitrate
                    };
                }
            });

            return result;
        } catch (error) {
            console.error(`[WebRTC] Failed to get stats for ${cameraId}:`, error);
            return null;
        }
    }
}
