const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const source = fs.readFileSync(path.join(__dirname, "../../broker/static/soloist_paste.js"), "utf8");

function page(clipboard) {
    const save = { focused: false, focus: () => (save.focused = true) };
    const input = {
        value: "",
        focused: false,
        focus: () => (input.focused = true),
        form: { querySelector: () => save },
    };
    const paste = { hidden: true, addEventListener: (type, callback) => (paste[type] = callback) };
    const problem = { hidden: true };
    const elements = { api_key: input, soloistPaste: paste, soloistPasteProblem: problem };
    vm.runInNewContext(source, {
        document: { getElementById: id => elements[id] },
        navigator: { clipboard },
    });
    return { input, paste, problem, save };
}

test("the Paste button fills the box from the clipboard, trimmed, and moves on to Save", async () => {
    const { input, paste, problem, save } = page({ readText: async () => "  soloist-key-abc\n" });
    assert.equal(paste.hidden, false);

    await paste.click();

    assert.equal(input.value, "soloist-key-abc");
    assert.equal(problem.hidden, true);
    assert.equal(save.focused, true);
});

test("a refused or empty clipboard says so and leaves the box to a paste by hand", async () => {
    const refusals = [
        async () => {
            throw new Error("NotAllowedError");
        },
        async () => "   ",
    ];
    for (const readText of refusals) {
        const { input, paste, problem, save } = page({ readText });

        await paste.click();

        assert.equal(input.value, "");
        assert.equal(problem.hidden, false);
        assert.equal(input.focused, true);
        assert.equal(save.focused, false);
    }
});

test("a paste that works takes the message of one that didn't away", async () => {
    let text = "";
    const { input, paste, problem } = page({ readText: async () => text });
    await paste.click();
    assert.equal(problem.hidden, false);

    text = "soloist-key-abc";
    await paste.click();

    assert.equal(input.value, "soloist-key-abc");
    assert.equal(problem.hidden, true);
});

test("there is no Paste button where a page can't read the clipboard", () => {
    assert.equal(page(undefined).paste.hidden, true);
    assert.equal(page({}).paste.hidden, true);
});
