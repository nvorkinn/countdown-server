// The start of a touchscreen's location step on its owner's phone (touchscreen_location.html): "Use my location" asks
// the browser where the phone is, on that tap and never before, and posts the answer to the server, which lists the
// postcodes nearest it. The position goes in the form's body, never in an address. If the browser has no location to
// give, or the owner refuses, or it takes too long, the page goes to the box to type a postcode in instead. The button
// shows only where the browser can be asked at all; typing a postcode and skipping work without this script.
(() => {
    "use strict";

    // Long enough for a phone indoors to find itself by Wi-Fi, short enough that waiting never feels stuck.
    const TIMEOUT_MS = 15000;
    // A position a minute old will do: the owner is standing by the touchscreen.
    const MAXIMUM_AGE_MS = 60000;

    const form = document.getElementById("locateForm");
    const locate = document.getElementById("locate");
    const locating = document.getElementById("locating");
    if (!form || !locate || !navigator.geolocation) return;

    locate.hidden = false;
    locate.addEventListener("click", () => {
        locate.disabled = true;
        if (locating) locating.hidden = false;
        navigator.geolocation.getCurrentPosition(
            position => {
                form.elements.latitude.value = position.coords.latitude;
                form.elements.longitude.value = position.coords.longitude;
                form.elements.accuracy.value = position.coords.accuracy;
                form.submit();
            },
            () => location.assign(form.dataset.typeUrl),
            { enableHighAccuracy: true, timeout: TIMEOUT_MS, maximumAge: MAXIMUM_AGE_MS },
        );
    });
})();
