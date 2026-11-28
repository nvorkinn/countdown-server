// A step of a touchscreen's setup on its owner's phone (touchscreen_step.html): asks the server which step the setup
// is at every couple of seconds (GET /api/devices/setup?device_id=, with the owner's login), and when that is no
// longer the step this page shows, goes back to the setup's address, which sends the phone wherever is next. So the
// phone and the touchscreen show the same step. A step that only waits for the touchscreen also asks again after a
// while whatever the answer, in case the touchscreen never caught up. Says so when the server can't be reached.
(() => {
    "use strict";

    const POLL_MS = 2000;
    // How many answers in a row may fail before the page says it is waiting for the touchscreen.
    const QUIET_FAILURES = 3;

    const shown = document.getElementById("setupStep");
    if (!shown) return;
    const { step, statusUrl, setupUrl, reloadAfterS } = shown.dataset;
    const offline = document.getElementById("setupOffline");
    let failures = 0;

    const next = () => location.replace(setupUrl);
    const failed = () => {
        failures += 1;
        if (offline && failures >= QUIET_FAILURES) offline.hidden = false;
    };
    const ask = async () => {
        try {
            const response = await fetch(statusUrl, { headers: { Accept: "application/json" }, cache: "no-store" });
            if (response.ok) {
                failures = 0;
                if (offline) offline.hidden = true;
                if ((await response.json()).step !== step) {
                    next();
                    return;
                }
            } else {
                failed();
            }
        } catch {
            failed();
        }
        setTimeout(ask, POLL_MS);
    };
    setTimeout(ask, POLL_MS);
    if (reloadAfterS) setTimeout(next, Number(reloadAfterS) * 1000);
})();
