// Native radios own the saved choices; the showcase and scrolling strips follow them.
(() => {
    "use strict";

    document.querySelectorAll(".showcase, .gallery").forEach(gallery => {
        const track = gallery.querySelector(".showcase-options, .gallery-track");
        const nav = gallery.querySelector(".gallery-nav");
        const [back, next] = nav.querySelectorAll(".gallery-step");
        const picture = gallery.querySelector(".showcase-picture");
        const name = gallery.querySelector(".showcase-name");
        const description = gallery.querySelector(".showcase-description");
        const optionSelector = ".showcase-option, .style-option";
        const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
        const update = () => {
            nav.hidden = track.scrollWidth <= track.clientWidth + 2;
            back.disabled = track.scrollLeft <= 2;
            next.disabled = track.scrollLeft + track.clientWidth >= track.scrollWidth - 2;
        };
        const reveal = input => {
            const option = input.closest(optionSelector).getBoundingClientRect();
            const viewport = track.getBoundingClientRect();
            const left =
                option.left < viewport.left
                    ? option.left - viewport.left - 6
                    : option.right > viewport.right
                      ? option.right - viewport.right + 6
                      : 0;
            track.scrollBy({ left, behavior: "instant" });
        };
        const sync = () => {
            const picked = track.querySelector("input:checked");
            if (picked) {
                if (picture) {
                    picture.src = picked.dataset.picture;
                    picture.alt = `${picked.dataset.name} style preview`;
                    name.textContent = picked.dataset.name;
                    description.textContent = picked.dataset.description;
                }
                reveal(picked);
            }
            update();
        };
        for (const button of [back, next]) {
            button.addEventListener("click", () => {
                const step =
                    track.querySelector(optionSelector).offsetWidth +
                    parseFloat(getComputedStyle(track).columnGap || 0);
                track.scrollBy({
                    left: Number(button.dataset.step) * step,
                    behavior: reducedMotion.matches ? "instant" : "smooth",
                });
            });
        }
        gallery.addEventListener("change", sync);
        track.addEventListener("focusin", e => {
            if (e.target.matches('input[type="radio"]')) reveal(e.target);
        });
        track.addEventListener("scroll", update, { passive: true });
        window.addEventListener("resize", update);
        window.addEventListener("pageshow", sync);
        gallery.closest("form").addEventListener("reset", () => requestAnimationFrame(sync));
        sync();
    });
})();
