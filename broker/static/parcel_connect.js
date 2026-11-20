// The Parcel page's Paste button: fills the key's box from the clipboard, so a key copied in Parcel's web app needs no
// long press. Shown only where the browser lets a page read the clipboard; the box takes a paste by hand everywhere.
(() => {
    "use strict";

    const input = document.getElementById("parcel_api_key");
    const paste = document.getElementById("parcelPaste");
    if (!input || !paste || !navigator.clipboard?.readText) return;

    paste.hidden = false;
    paste.addEventListener("click", async () => {
        let text = "";
        try {
            text = (await navigator.clipboard.readText()).trim();
        } catch {
            // Refused (the browser's own Paste prompt dismissed, or no permission): the box still takes one by hand.
        }
        if (!text) {
            input.focus();
            return;
        }
        input.value = text;
        input.form.querySelector("[type=submit]").focus();
    });
})();
