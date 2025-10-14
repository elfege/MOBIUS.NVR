// static/js/streaming/health.js
// ES6 module. Minimal deps; plays nice with jQuery callers.
// Exported API:
//   makeHealthMonitor({ onUnhealthy, blankThreshold, sampleIntervalMs, staleAfterMs, consecutiveBlankNeeded, cooldownMs })
//   -> { attachHls(serial, $videoOrDom, hlsInstance?), attachMjpeg(serial, $imgOrCanvas), detach(serial) }

export function makeHealthMonitor(userOpts = {}) {
  const opts = {
    // Tune for your lighting/cameras (12/5 is conservative)
    blankThreshold: { avg: 12, std: 5 },
    sampleIntervalMs: 2000,
    staleAfterMs: 8000,
    consecutiveBlankNeeded: 3,
    cooldownMs: 15000, // don’t spam restarts per-serial
    onUnhealthy: async (_info) => { },
    ...userOpts
  };

  const trackers = new Map(); // serial -> tracker

  const downsample = { w: 64, h: 36 };
  const mkCanvas = () => {
    const c = document.createElement('canvas');
    c.width = downsample.w; c.height = downsample.h;
    return c;
  };

  function toEl($maybe) {
    // Accept DOM element OR jQuery object
    if (!$maybe) return null;
    if ($maybe instanceof Element || $maybe instanceof HTMLVideoElement || $maybe instanceof HTMLImageElement) return $maybe;
    if (typeof $maybe.get === 'function') return $maybe.get(0);
    return null;
  }

  function luminanceStats(el) {
    try {
      const c = mkCanvas();
      const g = c.getContext('2d', { willReadFrequently: true });
      g.drawImage(el, 0, 0, downsample.w, downsample.h);
      const { data } = g.getImageData(0, 0, downsample.w, downsample.h);
      let sum = 0, sum2 = 0, n = downsample.w * downsample.h;
      for (let i = 0; i < data.length; i += 4) {
        const y = 0.299 * data[i] + 0.587 * data[i + 1] + 0.114 * data[i + 2];
        sum += y; sum2 += y * y;
      }
      const avg = sum / n;
      const variance = Math.max(0, (sum2 / n) - (avg * avg));
      return { avg, std: Math.sqrt(variance) };
    } catch {
      return null; // tainted/cross-origin or not ready
    }
  }

  function frameSignature(el) {
    try {
      const c = mkCanvas();
      const g = c.getContext('2d', { willReadFrequently: true });
      g.drawImage(el, 0, 0, downsample.w, downsample.h);
      const d = g.getImageData(0, 0, downsample.w, downsample.h).data;
      let s = 2166136261 >>> 0; // FNV-ish rolling
      for (let i = 0; i < d.length; i += 12) {
        s ^= d[i]; s = (s * 16777619) >>> 0;
      }
      return s;
    } catch { return null; }
  }

  function decodedFrames(video) {
    try {
      if (typeof video.getVideoPlaybackQuality === 'function') {
        const q = video.getVideoPlaybackQuality();
        return (q.totalVideoFrames || 0) - (q.droppedVideoFrames || 0);
      }
      // Fallback heuristics
      return Math.floor(video.currentTime * 10); // ~ deciseconds progressed
    } catch { return 0; }
  }

  function ensure(serial) {
    if (!trackers.has(serial)) {
      trackers.set(serial, {
        type: null, el: null, hls: null,
        blanks: 0, lastProgressAt: performance.now(),
        lastTime: 0, lastDecoded: 0, lastSig: null,
        timer: null, coolingUntil: 0,
        warmupUntil: 0 // Initialized to 0 (no warmup period yet) is set to performance.now() + opts.warmupMs when we attach monitors
      });
    }
    return trackers.get(serial);
  }

  function startTimer(serial, fn) {
    const t = ensure(serial);
    stopTimer(serial);
    t.timer = setInterval(fn, opts.sampleIntervalMs);
  }
  function stopTimer(serial) {
    const t = trackers.get(serial);
    if (t && t.timer) { clearInterval(t.timer); t.timer = null; }
  }

  async function markUnhealthy(serial, reason, metrics) {
    const t = ensure(serial);
    const now = performance.now();
    if (now < t.coolingUntil) return; // cooldown guard
    t.coolingUntil = now + opts.cooldownMs;

    // INITIAL LOGIC: Clean up per attach so we don’t keep sampling a dead element
    // NEW LOGIC: DON'T stop timer - let it keep running
    // stopTimer(serial);

    try {
      await opts.onUnhealthy({ serial, reason, metrics });
    } finally {
      // Timer keeps running, will try again after cooldown expires
    }
  }

  function attachHls(serial, $videoOrDom, hlsInstance = null) {
    // Check if health monitoring is enabled for this camera
    const $streamItem = $(`.stream-item[data-camera-serial="${serial}"]`);
    const healthEnabled = $streamItem.data('ui-health-monitor');

    if (healthEnabled === false || healthEnabled === 'false') {
      console.log(`[Health] Monitoring disabled for ${serial}`);
      return () => { }; // Return empty cleanup function
    }
    const el = toEl($videoOrDom);
    if (!el) return () => { };
    const t = ensure(serial);
    t.type = 'hls'; t.el = el; t.hls = hlsInstance;
    t.blanks = 0; t.lastTime = 0; t.lastDecoded = 0;
    t.lastProgressAt = performance.now();

    // Set warmup period for this attach
    t.warmupUntil = performance.now() + opts.warmupMs;

    // REMOVED: Don't skip timer creation during warmup

    const onTimeupdate = () => {
      const ct = el.currentTime || 0;
      const dec = decodedFrames(el);
      if (ct > t.lastTime + 0.1 || dec > t.lastDecoded) {
        t.lastTime = ct; t.lastDecoded = dec; t.lastProgressAt = performance.now();
      }
    };
    el.addEventListener('timeupdate', onTimeupdate);

    if (t.hls && t.hls.on && window.Hls) {
      const H = window.Hls;
      const mark = () => { t.lastProgressAt = performance.now(); };
      t.hls.on(H.Events.FRAG_BUFFERED, mark);
      t.hls.on(H.Events.FRAG_PARSED, mark);
      t._hlsMark = mark;
    }

    startTimer(serial, () => {
      // Check warmup
      if (performance.now() < t.warmupUntil) {
        console.log(`[Health:attachHls] ${serial}: In warmup period, skipping checks`);
        return;
      }

      // ===== STALE CHECK =====
      const staleDuration = performance.now() - t.lastProgressAt;
      console.log(`[Health] ${serial}: staleDuration=${(staleDuration / 1000).toFixed(1)}s, currentTime=${el.currentTime?.toFixed(1)}, paused=${el.paused}`);


      if (staleDuration > opts.staleAfterMs) {
        console.warn(`[Health] ${serial}: STALE - No progress for ${(staleDuration / 1000).toFixed(1)}s`);
        markUnhealthy(serial, 'stale_hls', { staleDuration });
        return;
      }
      // ===== END IMPROVED STALE CHECK =====

      // blank check (make it less sensitive)
      if (el.videoWidth > 0 && el.videoHeight > 0) {
        const s = luminanceStats(el);
        if (s && s.avg < opts.blankThreshold.avg && s.std < opts.blankThreshold.std) {
          if (++t.blanks >= opts.consecutiveBlankNeeded) {
            // ADDED: Verify it's actually black, not just a dark scene
            if (s.avg < 2 && s.std < 1) { // Truly black (near zero)
              console.warn(`[Health] ${serial}: Confirmed black screen - Avg: ${s.avg.toFixed(2)}, Std: ${s.std.toFixed(2)}`);
              markUnhealthy(serial, 'black_hls', s);
            } else {
              console.log(`[Health] ${serial}: Dark but not black - Avg: ${s.avg.toFixed(2)}, Std: ${s.std.toFixed(2)} - resetting counter`);
              t.blanks = 0; // Reset if not truly black
            }
          }
        } else {
          t.blanks = 0;
        }
      }
    });

    return () => detach(serial, onTimeupdate);
  }

  /**
 * Attach RTMP/FLV stream health monitor.
 * Mirrors attachHls() but listens to flv.js events instead.
 */
  function attachRTMP(serial, videoEl, flvInstance) {
    // Respect per-camera toggle (same as attachHls/attachMjpeg)
    const $streamItem = $(`.stream-item[data-camera-serial="${serial}"]`);
    const healthEnabled = $streamItem.data('ui-health-monitor');
    if (healthEnabled === false || healthEnabled === 'false') {
      console.log(`[Health] Monitoring disabled for ${serial}`);
      return () => { };
    }

    const el = toEl(videoEl);
    if (!el) return () => { };

    const t = ensure(serial);
    t.type = 'rtmp';
    t.el = el;
    t.blanks = 0;
    t.lastTime = 0;
    t.lastDecoded = 0;
    t.lastProgressAt = performance.now();

    // Warmup: use provided warmupMs if present, else default 6000
    const warmupMs = Number.isFinite(opts.warmupMs) ? opts.warmupMs : 6000;
    t.warmupUntil = performance.now() + warmupMs;

    // Track transient errors without acting immediately
    let lastErrorAt = 0;

    // flv.js hooks (guard existence)
    const hasFlv = typeof flvInstance?.on === 'function' && typeof window.flvjs !== 'undefined';
    if (hasFlv) {
      const F = window.flvjs;
      const markProgress = () => { t.lastProgressAt = performance.now(); };
      flvInstance.on(F.Events.STATISTICS_INFO, markProgress);
      flvInstance.on(F.Events.LOADING_COMPLETE, markProgress);
      flvInstance.on(F.Events.ERROR, (err) => {
        // Record only; verification happens in the timer.
        lastErrorAt = performance.now();
        console.warn(`[Health] ${serial}: flv.js error (transient-ignored)`, err);
      });
      // keep a reference if you want to detach specific handlers later (optional)
      t._flvMark = markProgress;
    }

    // HTMLVideoElement fallbacks
    el.ontimeupdate = () => { t.lastProgressAt = performance.now(); };
    el.onerror = () => {
      lastErrorAt = performance.now();
      console.warn(`[Health] ${serial}: video.onerror (transient-ignored)`);
    };

    startTimer(serial, () => {
      // Warmup window
      if (performance.now() < t.warmupUntil) {
        console.log(`[Health:attachRTMP] ${serial}: In warmup period, skipping checks`);
        return;
      }

      if (staleFor > opts.staleAfterMs) {
        console.warn(`[Health] ${serial}: STALE (RTMP) - No progress for ${Math.round(staleFor)}ms`);
        markUnhealthy(serial, 'stale_rtmp', { staleFor });
        return;
      }

      // Black/dark frame check (only truly black counts)
      if (el.videoWidth > 0 && el.videoHeight > 0) {
        const s = luminanceStats(el);
        if (s && s.avg < opts.blankThreshold.avg && s.std < opts.blankThreshold.std) {
          if (++t.blanks >= opts.consecutiveBlankNeeded) {
            if (s.avg < 2 && s.std < 1) {
              console.warn(`[Health] ${serial}: Confirmed black screen (RTMP) - Avg: ${s.avg.toFixed(2)}, Std: ${s.std.toFixed(2)}`);
              markUnhealthy(serial, 'black_rtmp', s);
            } else {
              // Dark scene, not black
              t.blanks = 0;
            }
          }
        } else {
          t.blanks = 0;
        }
      }
    });

    console.log(`[Health] Attached RTMP monitor for ${serial}`);

    // Same contract as attachHls/attachMjpeg
    return () => {
      detach(serial);
      console.log(`[Health] Detached RTMP monitor for ${serial}`);
    };
  }



  function attachMjpeg(serial, $imgOrCanvas) {
    const $streamItem = $(`.stream-item[data-camera-serial="${serial}"]`);
    const healthEnabled = $streamItem.data('ui-health-monitor');

    if (healthEnabled === false || healthEnabled === 'false') {
      console.log(`[Health] Monitoring disabled for ${serial}`);
      return () => { }; // Return empty cleanup function
    }
    const el = toEl($imgOrCanvas);
    if (!el) return () => { };
    const t = ensure(serial);
    t.type = 'mjpeg'; t.el = el; t.blanks = 0; t.lastSig = null;
    t.lastProgressAt = performance.now();

    // Set warmup period for this attach
    t.warmupUntil = performance.now() + opts.warmupMs;

    startTimer(serial, () => {
      if (performance.now() < t.warmupUntil) {
        console.log(`[Health:attachMjpeg] ${serial}: In warmup period, skipping checks`);
        return;
      }

      // image changed?
      const sig = frameSignature(el);
      if (sig != null) {
        if (t.lastSig !== sig) {
          t.lastSig = sig;
          t.lastProgressAt = performance.now();
        }
      }
      // stale?
      if (performance.now() - t.lastProgressAt > opts.staleAfterMs) {
        markUnhealthy(serial, 'stale_mjpeg');
        return;
      }
      // blank check too (some proxies return pure black jpeg)
      const s = luminanceStats(el);
      if (s && s.avg < opts.blankThreshold.avg && s.std < opts.blankThreshold.std) {
        if (++t.blanks >= opts.consecutiveBlankNeeded) {
          markUnhealthy(serial, 'black_mjpeg', s);
        }
      } else {
        t.blanks = 0;
      }
    });

    return () => detach(serial);
  }

  function detach(serial, onTimeupdate = null) {
    const t = trackers.get(serial);
    if (!t) return;
    stopTimer(serial);
    if (t.type === 'hls' && t.el && onTimeupdate) {
      t.el.removeEventListener('timeupdate', onTimeupdate);
    }
    trackers.delete(serial);
  }

  return { attachHls, attachMjpeg, attachRTMP, detach };

}
