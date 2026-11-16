// The screen's name, edited in the page's title: the pencil turns the title into a box, Save (or Enter) saves it on
// its own (POST /device/<id>/name), and Cancel (or Escape) puts the title back as it was. An empty name clears it, and
// the title and the sidebar show what the pages now call the screen.
(() => {
    "use strict";

    const title = document.getElementById("screenTitle");
    const label = document.getElementById("screenLabel");
    const button = document.getElementById("renameButton");
    const form = document.getElementById("renameForm");
    const input = document.getElementById("screenNameInput");
    const error = document.getElementById("renameError");
    const save = form.querySelector(".title-save");
    let saved = input.value;

    function editing(on) {
        title.hidden = on;
        form.hidden = !on;
        error.textContent = "";
        if (on) {
            input.focus();
            input.select();
        } else {
            input.value = saved;
            button.focus();
        }
    }

    // Without JavaScript the name can't be edited here, so the pencil only shows once this has run.
    button.hidden = false;
    button.addEventListener("click", () => editing(true));
    document.getElementById("renameCancel").addEventListener("click", () => editing(false));
    input.addEventListener("keydown", e => {
        if (e.key === "Escape") editing(false);
    });

    form.addEventListener("submit", async e => {
        e.preventDefault();
        if (input.value.trim() === saved.trim()) {
            editing(false);
            return;
        }
        save.disabled = true;
        try {
            const response = await fetch(form.action, { method: "POST", body: new FormData(form) });
            const body = await response.json().catch(() => ({}));
            if (!response.ok) throw new Error(body.error || "Couldn't save. Try again.");
            saved = body.name;
            label.textContent = body.label;
            const current = document.querySelector(".sidebar .nav-item.current");
            if (current) current.lastChild.textContent = body.label;
            editing(false);
        } catch (failure) {
            error.textContent = failure.message;
            input.focus();
        } finally {
            save.disabled = false;
        }
    });
})();
