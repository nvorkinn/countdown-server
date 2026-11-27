const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const source = fs.readFileSync(path.join(__dirname, "../../broker/static/soloist_paste.js"), "utf8");

function page(clipboard) {
    const classes = new Set();
    const save = { classList: { add: name => classes.add(name) }, classes };
    const input = {
        value: "",
        focused: false,
        focus: () => (input.focused = true),
        form: { submittedBy: null, requestSubmit: by => (input.form.submittedBy = by) },
    };
    const paste = { hidden: true, addEventListener: (type, callback) => (paste[type] = callback) };
    const problem = { hidden: true };
    const elements = { api_key: input, soloistPaste: paste, soloistSave: save, soloistPasteProblem: problem };
    vm.runInNewContext(source, {
        document: { getElementById: id => elements[id] },
        navigator: { clipboard },
    });
    return { input, paste, problem, save };
}

test("the Paste button fills the box from the clipboard, trimmed, and saves it straight away", async () => {
    const { input, paste, problem, save } = page({ readText: async () => "  soloist-key-abc\n" });
    assert.equal(paste.hidden, false);
    // Paste is the button to tap, so Save key steps back.
    assert.equal(save.classes.has("is-quiet"), true);

    await paste.click();

    assert.equal(input.value, "soloist-key-abc");
    assert.equal(problem.hidden, true);
    assert.equal(input.form.submittedBy, save);
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
        // Nothing to save.
        assert.equal(input.form.submittedBy, null);
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

test("there is no Paste button where a page can't read the clipboard, and Save key stays the button to tap", () => {
    assert.equal(page(undefined).paste.hidden, true);
    assert.equal(page({}).paste.hidden, true);
    assert.equal(page({}).save.classes.has("is-quiet"), false);
});
