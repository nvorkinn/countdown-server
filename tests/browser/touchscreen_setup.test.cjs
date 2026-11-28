const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const source = fs.readFileSync(path.join(__dirname, "../../broker/static/touchscreen_setup.js"), "utf8");

// The page at one step, with a server that gives `answers` in turn (a step's name, a status code, or an Error to
// throw), and timers the test runs by hand.
function page(step, answers, reloadAfterS) {
    const timers = [];
    const asked = [];
    const replaced = [];
    const offline = { hidden: true };
    const dataset = { step, statusUrl: "/api/devices/setup?device_id=abc", setupUrl: "/device/abc/setup" };
    if (reloadAfterS !== undefined) dataset.reloadAfterS = String(reloadAfterS);
    const elements = { setupStep: { dataset }, setupOffline: offline };
    vm.runInNewContext(source, {
        document: { getElementById: id => elements[id] },
        location: { replace: url => replaced.push(url) },
        setTimeout: (callback, ms) => timers.push({ callback, ms }),
        fetch: async (url, options) => {
            asked.push({ url, options });
            const answer = answers.shift();
            if (answer instanceof Error) throw answer;
            if (typeof answer === "number") return { ok: false, status: answer };
            return { ok: true, json: async () => ({ step: answer }) };
        },
    });
    // Run the next poll (never the reload timer, which a test fires itself).
    const poll = async () => {
        const index = timers.findIndex(timer => timer.ms === 2000);
        await timers.splice(index, 1)[0].callback();
    };
    return { timers, asked, replaced, offline, poll };
}

test("the page asks which step the setup is at, and stays while it is still this one", async () => {
    const { asked, replaced, poll, timers } = page("presence", ["presence", "presence"]);

    await poll();
    await poll();

    assert.equal(asked.length, 2);
    assert.equal(asked[0].url, "/api/devices/setup?device_id=abc");
    assert.equal(asked[0].options.cache, "no-store");
    assert.deepEqual(replaced, []);
    // And asks again.
    assert.equal(timers.filter(timer => timer.ms === 2000).length, 1);
});

test("when the step has moved on, the phone goes back to the setup, which sends it wherever is next", async () => {
    const { replaced, poll, timers } = page("soloist_sign_in", ["soloist_sign_in", "location"]);

    await poll();
    await poll();

    assert.deepEqual(replaced, ["/device/abc/setup"]);
    // It has left: nothing more is asked.
    assert.equal(timers.length, 0);
});

test("a server it can't reach is said so after a few tries, and unsaid once it answers", async () => {
    const { offline, replaced, poll } = page("presence", [new Error("offline"), 502, 401, "presence"]);

    await poll();
    await poll();
    assert.equal(offline.hidden, true);
    await poll();
    assert.equal(offline.hidden, false);
    await poll();
    assert.equal(offline.hidden, true);
    assert.deepEqual(replaced, []);
});

test("a step that only waits for the touchscreen asks where to go after a while, whatever the answer", () => {
    const { timers, replaced } = page("spotify", [], 62);

    const reload = timers.find(timer => timer.ms === 62000);
    assert.ok(reload);
    reload.callback();

    assert.deepEqual(replaced, ["/device/abc/setup"]);
});

test("a step the person acts on is never left on a timer", () => {
    const { timers } = page("location", []);

    assert.deepEqual(
        timers.map(timer => timer.ms),
        [2000],
    );
});

test("a page that isn't a setup step does nothing", () => {
    const timers = [];
    vm.runInNewContext(source, {
        document: { getElementById: () => null },
        setTimeout: (callback, ms) => timers.push(ms),
    });

    assert.deepEqual(timers, []);
});
