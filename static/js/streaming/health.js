// static/js/streaming/health.js
// ES6 + jQuery Health Monitor

export class HealthMonitor {
  constructor(userOpts = {}) {
    this.opts = {
      blankThreshold: { avg: 12, std: 5 },
      sampleIntervalMs: 2000,
      staleAfterMs: 8000,
      consecutiveBlankNeeded: 3,
      cooldownMs: 15000,
      warmupMs: 10000,
      onUnhealthy: async (_info) => { },
      ...userOpts
    };

    this.trackers = new Map();
    this.downsample = { w: 64, h: 36 };
  }

  mkCanvas() {
    const c = document.createElement('canvas');
    c.width = this.downsample.w;
    c.height = this.downsample.h;
    return c;
  }

  toEl($maybe) {
    if (!$maybe) return null;
    if ($maybe instanceof Element || $maybe instanceof HTMLVideoElement || $maybe instanceof HTMLImageElement) return $maybe;
    if (typeof $maybe.get === 'function') return $maybe.get(0);
    return null;
  }

  frameSignature(el) {
    try {
      const c = this.mkCanvas();
      const g = c.getContext('2d', { willReadFrequently: true });
      g.drawImage(el, 0, 0, this.downsample.w, this.downsample.h);
      const d = g.getImageData(0, 0, this.downsample.w, this.downsample.h).data;
      let s = 2166136261 >>> 0;
      for (let i = 0; i < d.length; i += 12) {
        s ^= d[i];
        s = (s * 16777619) >>> 0;
      }
      return s;
    } catch {
      return null;
    }
  }

  luminanceStats(el) {
    try {
      const c = this.mkCanvas();
      const g = c.getContext('2d', { willReadFrequently: true });
      g.drawImage(el, 0, 0, this.downsample.w, this.downsample.h);
      const { data } = g.getImageData(0, 0, this.downsample.w, this.downsample.h);
      let sum = 0, sum2 = 0, n = this.downsample.w * this.downsample.h;
      for (let i = 0; i < data.length; i += 4) {
        const y = 0.299 * data[i] + 0.587 * data[i + 1] + 0.114 * data[i + 2];
        sum += y;
        sum2 += y * y;
      }
      const avg = sum / n;
      const variance = Math.max(0, (sum2 / n) - (avg * avg));
      return { avg, std: Math.sqrt(variance) };
    } catch {
      return null;
    }
  }

  ensure(serial) {
    if (!this.trackers.has(serial)) {
      this.trackers.set(serial, {
        el: null,
        blanks: 0,
        lastSig: null,
        lastProgressAt: performance.now(),
        timer: null,
        coolingUntil: 0,
        warmupUntil: 0
      });
    }
    return this.trackers.get(serial);
  }

  startTimer(serial, fn) {
    const t = this.ensure(serial);
    this.stopTimer(serial);
    t.timer = setInterval(fn, this.opts.sampleIntervalMs);
  }

  stopTimer(serial) {
    const t = this.trackers.get(serial);
    if (t && t.timer) {
      clearInterval(t.timer);
      t.timer = null;
    }
  }

  async markUnhealthy(serial, reason, metrics) {
    const t = this.ensure(serial);
    const now = performance.now();
    if (now < t.coolingUntil) return;
    t.coolingUntil = now + this.opts.cooldownMs;

    try {
      await this.opts.onUnhealthy({ cameraId: serial, reason, metrics });
    } catch (e) {
      console.error(`[Health] Error in onUnhealthy callback:`, e);
    }
  }

  attach(serial, element) {
    const $streamItem = $(`.stream-item[data-camera-serial="${serial}"]`);
    const healthEnabled = $streamItem.data('ui-health-monitor');

    if (healthEnabled === false || healthEnabled === 'false') {
      console.log(`[Health] Monitoring disabled for ${serial}`);
      return () => { };
    }

    const el = this.toEl(element);
    if (!el) {
      console.warn(`[Health] No element found for ${serial}`);
      return () => { };
    }

    const t = this.ensure(serial);
    t.el = el;
    t.blanks = 0;
    t.lastSig = null;
    t.lastProgressAt = performance.now();
    t.warmupUntil = performance.now() + this.opts.warmupMs;
    t.hasReceivedFrames = false;  // Track if we've ever seen frames
    t.lastCurrentTime = 0;  // Track video currentTime for more reliable stale detection

    console.log(`[Health] Attached monitor for ${serial}`);

    this.startTimer(serial, () => {
      const now = performance.now();

      // Skip during warmup period
      if (now < t.warmupUntil) return;

      // Wait for video element to have data before health checking
      // readyState: 0=nothing, 1=metadata, 2=current, 3=future, 4=enough
      if (t.el.readyState < 2) {
        // Video not ready yet - reset lastProgressAt to avoid false stale detection
        t.lastProgressAt = now;
        return;
      }

      // For HLS streams, check if video is actually playing (not paused/ended)
      // A paused video shouldn't be marked unhealthy
      if (t.el.paused || t.el.ended) {
        t.lastProgressAt = now;  // Don't mark paused streams as stale
        return;
      }

      // PRIMARY STALE CHECK: Use currentTime progression (most reliable)
      // This works even for static/dark scenes where frame signatures don't change
      const currentTime = t.el.currentTime || 0;
      if (currentTime > t.lastCurrentTime) {
        // Video is progressing - stream is healthy
        t.lastCurrentTime = currentTime;
        t.lastProgressAt = now;
        t.hasReceivedFrames = true;
        t.blanks = 0;  // Reset blanks counter on progress
      }

      // SECONDARY CHECK: Frame signature (backup for img elements)
      const sig = this.frameSignature(t.el);
      if (sig !== null && sig !== t.lastSig) {
        t.lastSig = sig;
        t.lastProgressAt = now;
        t.blanks = 0;
        t.hasReceivedFrames = true;
      }

      // Only check for stale if we've ever received frames
      // This prevents false stale detection during initial buffering
      if (!t.hasReceivedFrames) {
        // Haven't seen any frame changes yet - extend grace period
        t.lastProgressAt = now;
        return;
      }

      const staleDuration = now - t.lastProgressAt;
      if (staleDuration > this.opts.staleAfterMs) {
        // Double-check: is currentTime REALLY stuck?
        if (t.el.currentTime === t.lastCurrentTime && t.lastCurrentTime > 0) {
          console.warn(`[Health] ${serial}: STALE - currentTime stuck at ${t.el.currentTime.toFixed(1)}s for ${(staleDuration / 1000).toFixed(1)}s`);
          this.markUnhealthy(serial, 'stale', { staleDuration, currentTime: t.el.currentTime });
        }
        return;
      }

      const lum = this.luminanceStats(t.el);
      if (lum && lum.avg < this.opts.blankThreshold.avg && lum.std < this.opts.blankThreshold.std) {
        if (++t.blanks >= this.opts.consecutiveBlankNeeded) {
          if (lum.avg < 2 && lum.std < 1) {
            console.warn(`[Health] ${serial}: BLACK SCREEN - Avg: ${lum.avg.toFixed(2)}`);
            this.markUnhealthy(serial, 'black', lum);
          } else {
            t.blanks = 0;
          }
        }
      } else {
        t.blanks = 0;
      }
    });

    return () => this.detach(serial);
  }

  detach(serial) {
    const t = this.trackers.get(serial);
    if (!t) return;
    this.stopTimer(serial);
    this.trackers.delete(serial);
    console.log(`[Health] Detached monitor for ${serial}`);
  }

  // API methods for backwards compatibility
  attachHls(serial, el) {
    return this.attach(serial, el);
  }

  attachRTMP(serial, el) {
    return this.attach(serial, el);
  }

  attachMjpeg(serial, el) {
    return this.attach(serial, el);
  }

  /**
   * Attach health monitor to WebRTC stream
   * WebRTC uses the same video element monitoring as HLS
   * The RTCPeerConnection is passed for potential future ICE state monitoring
   *
   * @param {string} serial - Camera ID
   * @param {HTMLVideoElement} el - Video element
   * @param {RTCPeerConnection} pc - Peer connection (optional, for future use)
   */
  attachWebRTC(serial, el, pc = null) {
    // Store pc reference for potential future ICE state monitoring
    const t = this.ensure(serial);
    t.peerConnection = pc;

    return this.attach(serial, el);
  }
}

// Factory function for existing code
export function makeHealthMonitor(userOpts = {}) {
  return new HealthMonitor(userOpts);
}