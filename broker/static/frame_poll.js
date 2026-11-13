// Keeps a page's picture of a screen as the screen has it: followFrame asks the frame's URL for a newer picture when
// the screen itself next would (each answer's Retry-After, services/pace.py), while the page is in view.
//
// `show` says what to do with the answers: picture(objectUrl) for a new picture, drawn(ms) with when the one shown
// was drawn (at every answer that has one), and optionally missing() for no picture yet and failed() for an answer
// that couldn't be had. `etag` is the ETag of the picture the page was loaded with, if any, so it isn't fetched
// again; `firstMs` is how long after loading to ask first.
(() => {
    "use strict";

    // How long to wait when an answer doesn't say: the screen's usual pace.
    const FALLBACK_S = 30;

    window.followFrame = (url, { etag = null, firstMs, ...show }) => {
        let objectUrl = null;
        let timer = null;
        let asking = false;

        async function ask() {
            clearTimeout(timer);
            timer = null;
            if (asking) return;
            asking = true;
            let sleepS = FALLBACK_S;
            try {
                // no-store, and the ETag sent by hand: the 304 itself comes back, with its own Retry-After.
                const response = await fetch(url, {
                    cache: "no-store",
                    credentials: "same-origin",
                    redirect: "error",
                    headers: etag ? { "If-None-Match": etag } : {},
                });
                const retryAfter = Number.parseInt(response.headers.get("Retry-After"), 10);
                if (retryAfter > 0) sleepS = retryAfter;
                if (response.status === 404) {
                    show.missing?.();
                } else if (response.ok || response.status === 304) {
                    if (response.ok) {
                        const blob = await response.blob();
                        if (objectUrl) URL.revokeObjectURL(objectUrl);
                        objectUrl = URL.createObjectURL(blob);
                        etag = response.headers.get("ETag");
                        show.picture(objectUrl);
                    }
                    const drawnAt = Date.parse(response.headers.get("X-Rendered-At"));
                    if (!Number.isNaN(drawnAt)) show.drawn(drawnAt);
                } else {
                    throw new Error(response.status);
                }
            } catch (_error) {
                // Offline, or the sign-in expired (which only a reload can renew).
                show.failed?.();
            }
            asking = false;
            if (!document.hidden) timer = setTimeout(ask, sleepS * 1000);
        }

        // Only ask while the page is in view, and catch up as soon as it is again.
        document.addEventListener("visibilitychange", () => {
            clearTimeout(timer);
            timer = null;
            if (!document.hidden) ask();
        });

        timer = setTimeout(ask, firstMs);
    };
})();
