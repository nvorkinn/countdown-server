// The Soloist key page's Paste button: fills the key's box from the clipboard, so a key copied in Spotify's dashboard
// needs no long press. Shown only where the browser lets a page read the clipboard; the box takes a paste by hand
// everywhere, and the page says so when the browser refuses the read.
(() => {
    "use strict";

    const input = document.getElementById("api_key");
    const paste = document.getElementById("soloistPaste");
    const problem = document.getElementById("soloistPasteProblem");
    if (!input || !paste || !problem || !navigator.clipboard?.readText) return;

    paste.hidden = false;
    paste.addEventListener("click", async () => {
        let text = "";
        try {
            text = (await navigator.clipboard.readText()).trim();
        } catch {
            // Refused (the browser's own Paste prompt dismissed, or no permission): the box still takes one by hand.
        }
        problem.hidden = text !== "";
        if (!text) {
            input.focus();
            return;
        }
        input.value = text;
        input.form.querySelector("[type=submit]").focus();
    });
})();
