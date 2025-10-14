/**
* Detect old iOS versions that have issues with modern HLS.js
* Returns true if iOS version < 13
*/
export const isOldIOS = () => {
    const ua = navigator.userAgent;

    // Check if it's iOS
    if (!/iPad|iPhone|iPod/.test(ua)) {

        return false;
    }

    // Extract iOS version
    const match = ua.match(/OS (\d+)_(\d+)/);
    if (!match) {

        return false; // Can't determine version, assume modern
    }

    const majorVersion = parseInt(match[1]);

    return majorVersion < 13; // iOS 12 and earlier
}
