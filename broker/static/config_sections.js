// A touchscreen's page saves each setting as it changes, a section at a time, as the tablet does: PUT
// /api/devices/config/<section>?device_id=<id> with the section whole and If-Match of the config's version. A save
// shows "Saved"; a refused one says why in its section; a stale version (409, saved elsewhere since) reloads the page.
// Saves go one at a time, each with the version the last one answered, and a section's quick changes (tapping through
// the choices) wait half a second and send only the last. A typed field saves once it's left.
(() => {
    "use strict";

    const form = document.getElementById("settingsForm");
    const configUrl = form.dataset.configUrl;
    const deviceId = form.dataset.deviceId;
    const nameUrl = form.dataset.nameUrl;
    let version = form.dataset.version;
    // The config as last saved (ConfigResponse), which a section's value starts from.
    let config = JSON.parse(document.getElementById("deviceConfig").textContent);

    // The section each field saves, and the section's whole value from the page.
    const SECTIONS = {
        clock_format: () => ["clock_format", checked("clock_format")],
        theme: () => ["style", style()],
        theme_colour: () => ["style", style()],
        screen_sleep_minutes: () => [
            "display",
            { ...config.display, sleep_minutes: Number(checked("screen_sleep_minutes")) },
        ],
        postcode: () => ["notice_board", { ...config.notice_board, postcode: form.elements.postcode.value.trim() }],
        stops_order: () => ["arrivals", { ...config.arrivals, stops: stops() }],
        weather_location: weather,
        weather_place_id: weather,
    };

    function checked(name) {
        return form.querySelector(`input[name="${name}"]:checked`)?.value;
    }

    function style() {
        // The contract names the skin `skin` or, from countdown-api's theme change, `theme`: keep whichever it has.
        const key = "theme" in config.style ? "theme" : "skin";
        return { ...config.style, [key]: checked("theme"), colour: checked("theme_colour") };
    }

    function stops() {
        return [...document.querySelectorAll("#stopsList .stop-card")].map(card => ({
            source: card.dataset.source || (card.dataset.id.startsWith("NSR:") ? "entur" : "tfl"),
            id: card.dataset.id,
        }));
    }

    // A place is saved only once picked (its search result's value), or cleared by emptying the box.
    let pickedPlace = null;
    document.addEventListener("place-picked", e => {
        pickedPlace = e.detail.value;
    });
    function weather() {
        const typed = form.elements.weather_location.value.trim();
        if (!typed) return ["weather", { location: "", key: null }];
        if (form.elements.weather_place_id.value && pickedPlace) return ["weather", pickedPlace];
        return null;
    }

    const errorOf = section => {
        let error = section.querySelector(":scope > .section-error");
        if (!error) {
            error = document.createElement("p");
            error.className = "section-error";
            error.setAttribute("role", "alert");
            section.querySelector(".card-header").after(error);
        }
        return error;
    };

    function saved() {
        document.querySelector(".toast")?.remove();
        const toast = document.createElement("div");
        toast.className = "toast";
        toast.setAttribute("role", "status");
        toast.textContent = "Saved";
        document.body.append(toast);
        setTimeout(() => toast.remove(), 2700);
    }

    // One save at a time, so each sends the version the one before it answered.
    let queue = Promise.resolve();
    const pending = new Map();

    function schedule(target, delay) {
        const section = target.closest("section");
        clearTimeout(pending.get(section));
        pending.set(
            section,
            setTimeout(() => {
                pending.delete(section);
                queue = queue.then(() => save(target, section));
            }, delay),
        );
    }

    async function save(target, section) {
        const error = errorOf(section);
        try {
            if (target.name === "screen_name") {
                await saveName(target.value);
            } else {
                const picked = SECTIONS[target.name]();
                // Nothing to save: a place typed but not picked, or the section as it's saved.
                if (!picked || JSON.stringify(picked[1]) === JSON.stringify(config[picked[0]])) return;
                await saveSection(...picked);
            }
            error.textContent = "";
            saved();
        } catch (failure) {
            if (failure.stale) {
                sessionStorage.setItem("countdown-stale", "1");
                window.location.reload();
                return;
            }
            error.textContent = failure.message;
        }
    }

    async function saveSection(name, value) {
        const response = await fetch(
            `${configUrl}/${encodeURIComponent(name)}?device_id=${encodeURIComponent(deviceId)}`,
            {
                method: "PUT",
                headers: { "Content-Type": "application/json", "If-Match": version },
                body: JSON.stringify(value),
            },
        );
        if (response.status === 409) throw Object.assign(new Error(), { stale: true });
        const body = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(sentence(body.error) || "Couldn't save. Try again.");
        version = response.headers.get("ETag");
        config = body;
    }

    async function saveName(name) {
        const response = await fetch(nameUrl, { method: "POST", body: new URLSearchParams({ screen_name: name }) });
        const body = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(body.error || "Couldn't save. Try again.");
        document.querySelector(".hero h1").textContent = body.label;
    }

    // The API's errors are lower-case clauses: make one a sentence.
    function sentence(text) {
        return text ? `${text[0].toUpperCase()}${text.slice(1)}.` : "";
    }

    form.addEventListener("submit", e => e.preventDefault());
    form.addEventListener("change", e => {
        if (e.target.name in SECTIONS || e.target.name === "screen_name") schedule(e.target, 500);
    });

    try {
        if (sessionStorage.getItem("countdown-stale")) {
            sessionStorage.removeItem("countdown-stale");
            const notice = document.createElement("div");
            notice.className = "error-banner";
            notice.setAttribute("role", "status");
            notice.textContent =
                "This screen's settings were changed elsewhere, so the page shows them now. Make your change again.";
            form.before(notice);
        }
    } catch {}
})();
