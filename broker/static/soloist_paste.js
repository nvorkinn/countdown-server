// The Soloist key page's Paste button: takes the key from the clipboard and saves it in one tap, so a key copied in
// Spotify's dashboard needs no long press and no second button. Shown only where the browser lets a page read the
// clipboard, and then Save key steps back to being the quieter way, for a key pasted or typed by hand. The box takes
// a paste by hand everywhere, and the page says so when the browser refuses the read.
(() => {
    "use strict";

    const input = document.getElementById("api_key");
    const paste = document.getElementById("soloistPaste");
    const save = document.getElementById("soloistSave");
    const problem = document.getElementById("soloistPasteProblem");
    if (!input || !paste || !save || !problem || !navigator.clipboard?.readText) return;

    paste.hidden = false;
    save.classList.add("is-quiet");
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
        // Straight on: the key is in the box, and there is nothing to check before saving it.
        input.form.requestSubmit(save);
    });
})();
