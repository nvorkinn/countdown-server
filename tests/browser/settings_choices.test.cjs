const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const source = fs.readFileSync(path.join(__dirname, "../../broker/static/settings_choices.js"), "utf8");

class Element {
    listeners = {};
    addEventListener(type, callback) {
        this.listeners[type] ??= [];
        this.listeners[type].push(callback);
    }
    dispatch(type, target = this) {
        for (const callback of this.listeners[type] || []) callback({ target });
    }
}

function choices(showcase) {
    const gallery = new Element();
    const form = new Element();
    const track = new Element();
    const back = new Element();
    const next = new Element();
    back.dataset = { step: "-1" };
    next.dataset = { step: "1" };
    const nav = { querySelectorAll: () => [back, next] };
    const picture = {};
    const name = {};
    const description = {};
    track.scrollLeft = 0;
    track.clientWidth = 260;
    track.scrollWidth = 416;
    track.scrolls = [];
    track.scrollBy = options => {
        track.scrolls.push(options);
        track.scrollLeft = Math.max(
            0,
            Math.min(track.scrollWidth - track.clientWidth, track.scrollLeft + options.left),
        );
        track.dispatch("scroll");
    };
    track.getBoundingClientRect = () => ({ left: 0, right: track.clientWidth });
    const inputs = ["airy", "hero", "card"].map((style, index) => ({
        checked: index === 0,
        dataset: { name: style, description: `${style} description`, picture: `/styles/${style}.png` },
        matches: selector => selector === 'input[type="radio"]',
        closest: () => ({
            offsetWidth: 128,
            getBoundingClientRect: () => ({
                left: index * 144 - track.scrollLeft,
                right: index * 144 + 128 - track.scrollLeft,
            }),
        }),
    }));
    track.querySelector = selector =>
        selector === "input:checked" ? inputs.find(input => input.checked) : inputs[0].closest();
    gallery.querySelector = selector =>
        ({
            ".showcase-options, .gallery-track": track,
            ".gallery-nav": nav,
            ".showcase-picture": showcase ? picture : null,
            ".showcase-name": showcase ? name : null,
            ".showcase-description": showcase ? description : null,
        })[selector];
    gallery.closest = () => form;
    const pick = index =>
        inputs.forEach((input, i) => {
            input.checked = i === index;
        });
    return { gallery, form, track, nav, back, next, picture, name, description, inputs, pick };
}

function harness(reducedMotion = false) {
    const style = choices(true);
    const layout = choices(false);
    const window = new Element();
    window.matchMedia = () => ({ matches: reducedMotion });
    const frames = [];
    vm.runInNewContext(source, {
        document: { querySelectorAll: () => [style.gallery, layout.gallery] },
        window,
        getComputedStyle: () => ({ columnGap: "16px" }),
        requestAnimationFrame: callback => frames.push(callback),
    });
    return { style, layout, window, frames };
}

test("the selected style updates its real picture and accessible description, not the layout", () => {
    const { style, layout } = harness();
    assert.equal(style.picture.src, "/styles/airy.png");
    style.pick(2);
    style.gallery.dispatch("change");
    assert.equal(style.picture.src, "/styles/card.png");
    assert.equal(style.picture.alt, "card style preview");
    assert.equal(style.name.textContent, "card");
    assert.equal(style.description.textContent, "card description");
    assert.equal(style.track.scrollLeft, 156);
    assert.equal(layout.inputs[0].checked, true);
    layout.pick(1);
    layout.gallery.dispatch("change");
    assert.equal(style.picture.src, "/styles/card.png");
});

test("scroll buttons reflect overflow and move one option without changing the selection", () => {
    const { style, window } = harness();
    assert.equal(style.nav.hidden, false);
    assert.equal(style.back.disabled, true);
    style.next.dispatch("click");
    assert.equal(style.track.scrollLeft, 144);
    assert.equal(style.track.scrolls.at(-1).behavior, "smooth");
    assert.equal(style.inputs[0].checked, true);
    style.next.dispatch("click");
    assert.equal(style.next.disabled, true);
    style.back.dispatch("click");
    assert.equal(style.next.disabled, false);
    style.track.clientWidth = style.track.scrollWidth;
    style.track.scrollLeft = 0;
    window.dispatch("resize");
    assert.equal(style.nav.hidden, true);
});

test("keyboard focus reveals an offscreen option and reduced motion avoids animated scrolling", () => {
    const { style } = harness(true);
    style.track.dispatch("focusin", style.inputs[2]);
    assert.equal(style.track.scrollLeft, 156);
    style.back.dispatch("click");
    assert.equal(style.track.scrolls.at(-1).behavior, "instant");
});

test("restored and reset forms synchronize the showcase after native radios change", () => {
    const { style, window, frames } = harness();
    style.pick(1);
    window.dispatch("pageshow");
    assert.equal(style.picture.src, "/styles/hero.png");
    style.form.dispatch("reset");
    assert.equal(style.picture.src, "/styles/hero.png");
    style.pick(0);
    for (const callback of frames) callback();
    assert.equal(style.picture.src, "/styles/airy.png");
});
