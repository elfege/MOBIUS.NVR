/**
 * Tiny formatting utilities for the Collected Data page.
 * No deps; safe to import as a side-effect-free module.
 */

export function humanRelativeTime(isoStr) {
    if (!isoStr) return '—';
    const t = new Date(isoStr);
    if (isNaN(t)) return '—';
    const diffSec = (Date.now() - t.getTime()) / 1000;
    if (diffSec < 0) return 'in the future';
    if (diffSec < 60)         return `${Math.floor(diffSec)}s ago`;
    if (diffSec < 3600)       return `${Math.floor(diffSec / 60)}m ago`;
    if (diffSec < 86400)      return `${Math.floor(diffSec / 3600)}h ago`;
    if (diffSec < 86400 * 30) return `${Math.floor(diffSec / 86400)}d ago`;
    return t.toLocaleDateString();
}

export function humanBytes(n) {
    if (n == null || isNaN(n)) return '—';
    const units = ['B', 'KB', 'MB', 'GB', 'TB', 'PB'];
    let i = 0;
    let v = Number(n);
    while (v >= 1024 && i < units.length - 1) { v /= 1024; i++; }
    // 2 decimals for GB+, 1 for MB, 0 for KB/B
    const decimals = i >= 3 ? 2 : i >= 2 ? 1 : 0;
    return `${v.toFixed(decimals)} ${units[i]}`;
}

export function humanDuration(seconds) {
    if (seconds == null || isNaN(seconds)) return '—';
    const s = Math.round(Number(seconds));
    if (s < 60)   return `${s}s`;
    const m = Math.floor(s / 60);
    const r = s % 60;
    if (m < 60)   return `${m}m ${r}s`;
    const h = Math.floor(m / 60);
    const rm = m % 60;
    return `${h}h ${rm}m`;
}

export function escapeHtml(str) {
    if (str == null) return '';
    return String(str)
        .replaceAll('&', '&amp;')
        .replaceAll('<', '&lt;')
        .replaceAll('>', '&gt;')
        .replaceAll('"', '&quot;')
        .replaceAll("'", '&#39;');
}
