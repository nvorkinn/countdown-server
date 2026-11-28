const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const source = fs.readFileSync(path.join(__dirname, "../../broker/static/touchscreen_location.js"), "utf8");

// The start page, with a browser whose geolocation the test answers by hand (or has none, for `geolocation` null).
function page(geolocation) {
    const asked = [];
    const assigned = [];
    const form = {
        dataset: { typeUrl: "/device/abc/setup/location/type?located=no" },
        elements: { latitude: { value: "" }, longitude: { value: "" }, accuracy: { value: "" } },
        submitted: 0,
        submit: () => (form.submitted += 1),
    };
    const locate = { hidden: true, disabled: false, addEventListener: (type, callback) => (locate[type] = callback) };
    const locating = { hidden: true };
    const elements = { locateForm: form, locate, locating };
    vm.runInNewContext(source, {
        document: { getElementById: id => elements[id] },
        location: { assign: url => assigned.push(url) },
        navigator: {
            geolocation:
                geolocation === null
                    ? undefined
                    : { getCurrentPosition: (found, failed, options) => asked.push({ found, failed, options }) },
        },
    });
    return { form, locate, locating, asked, assigned };
}

test("the button shows where the browser can say where the phone is, and asks only once it is tapped", () => {
    const { locate, locating, asked } = page();

    assert.equal(locate.hidden, false);
    assert.equal(asked.length, 0);

    locate.click();

    assert.equal(asked.length, 1);
    assert.equal(locate.disabled, true);
    assert.equal(locating.hidden, false);
    assert.equal(asked[0].options.enableHighAccuracy, true);
    assert.equal(asked[0].options.timeout, 15000);
});

test("the position found goes into the form, which is posted", () => {
    const { form, locate, asked, assigned } = page();
    locate.click();

    asked[0].found({ coords: { latitude: 51.48471, longitude: -0.09192, accuracy: 35 } });

    assert.equal(form.elements.latitude.value, 51.48471);
    assert.equal(form.elements.longitude.value, -0.09192);
    assert.equal(form.elements.accuracy.value, 35);
    assert.equal(form.submitted, 1);
    assert.deepEqual(assigned, []);
});

test("refused, unavailable or too slow, the page goes to the box to type a postcode in", () => {
    const { form, locate, asked, assigned } = page();
    locate.click();

    asked[0].failed({ code: 1 });

    assert.deepEqual(assigned, ["/device/abc/setup/location/type?located=no"]);
    assert.equal(form.submitted, 0);
});

test("a browser with no location to ask keeps the button hidden", () => {
    const { locate } = page(null);

    assert.equal(locate.hidden, true);
    assert.equal(locate.click, undefined);
});
