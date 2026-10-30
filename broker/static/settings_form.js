// A settings form (#settingsForm) and its save bar (#saveBar): the bar shows only while the form differs from what
// was loaded, "Saving…" while it posts, and "Saved" for a moment on the page it comes back to. Buttons with a
// data-confirm ask first. Shared by a screen's page and the account's.
(() => {
    "use strict";

    const settingsForm = document.getElementById("settingsForm");
    const saveStatus = document.getElementById("saveStatus");
    const formState = () => new URLSearchParams(new FormData(settingsForm)).toString();
    const loaded = formState();
    let submitting = false;
    const checkDirty = () => settingsForm.classList.toggle("dirty", formState() !== loaded);
    settingsForm.classList.add("tracked");
    settingsForm.addEventListener("input", checkDirty);
    settingsForm.addEventListener("change", checkDirty);
    const discard = document.getElementById("discardChanges");
    discard.hidden = false;
    discard.addEventListener("click", () => {
        submitting = true;
        window.location.reload();
    });
    settingsForm.addEventListener("submit", () => {
        submitting = true;
        settingsForm.classList.add("saving");
        saveStatus.textContent = "Saving…";
        try {
            sessionStorage.setItem("countdown-saving", "1");
        } catch {}
    });
    window.addEventListener("beforeunload", e => {
        if (!submitting && settingsForm.classList.contains("dirty")) e.preventDefault();
    });
    try {
        if (sessionStorage.getItem("countdown-saving")) {
            sessionStorage.removeItem("countdown-saving");
            if (!document.querySelector(".error-banner")) {
                const toast = document.createElement("div");
                toast.className = "toast";
                toast.setAttribute("role", "status");
                toast.textContent = "Saved";
                document.body.append(toast);
                setTimeout(() => toast.remove(), 3000);
            }
        }
    } catch {}

    // What can't be undone (unlinking a screen, forgetting a login) asks first.
    for (const button of document.querySelectorAll("[data-confirm]")) {
        button.addEventListener("click", e => {
            if (!confirm(button.dataset.confirm)) e.preventDefault();
        });
    }
})();
