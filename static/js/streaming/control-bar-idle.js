/**
 * control-bar-idle.js
 *
 * Adds an .idle class to each .stream-actions-bar after 30 seconds of no
 * mouse / touch activity over its parent .stream-item. CSS in
 * stream-control-bar.css fades the bar out when .idle is present.
 *
 * Mouse activity (mousemove / mouseenter / touchstart) removes .idle and
 * restarts the 30s timer.
 *
 * Operator directive 2026-06-13: "All icons fade after 30s of no mouse
 * activity."
 */
(function () {
    'use strict';

    const IDLE_MS = 30 * 1000;
    const ATTACHED_FLAG = '_controlBarIdleAttached';

    function attachToTile(tile) {
        if (tile[ATTACHED_FLAG]) return;
        const bar = tile.querySelector('.stream-actions-bar');
        if (!bar) return;
        tile[ATTACHED_FLAG] = true;

        let timer = null;

        function resetIdle() {
            bar.classList.remove('idle');
            if (timer !== null) clearTimeout(timer);
            timer = setTimeout(() => bar.classList.add('idle'), IDLE_MS);
        }

        // Any of these resets the timer.
        tile.addEventListener('mousemove',  resetIdle, { passive: true });
        tile.addEventListener('mouseenter', resetIdle, { passive: true });
        tile.addEventListener('touchstart', resetIdle, { passive: true });

        // Start the timer immediately — the bar fades after 30s of no
        // activity even if the cursor never touches the tile. (If the
        // operator wants the bar always-visible on a static screen, they
        // hover it briefly.)
        resetIdle();
    }

    function scan() {
        document.querySelectorAll('.stream-item').forEach(attachToTile);
    }

    function init() {
        scan();
        // Watch for tiles added later (grid rebuild, fullscreen entry, etc.).
        // childList+subtree catches the .stream-item nodes appearing under
        // any container that mutates.
        const mo = new MutationObserver((mutations) => {
            // Cheap check first: any mutation with addedNodes triggers a rescan.
            for (const m of mutations) {
                if (m.addedNodes && m.addedNodes.length) {
                    scan();
                    return;
                }
            }
        });
        mo.observe(document.body, { childList: true, subtree: true });
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
