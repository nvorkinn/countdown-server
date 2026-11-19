const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const source = fs.readFileSync(path.join(__dirname, "../../broker/static/parcel_connect.js"), "utf8");

function page(clipboard) {
    const save = { focused: false, focus: () => (save.focused = true) };
    const input = {
        value: "",
        focused: false,
        focus: () => (input.focused = true),
        form: { querySelector: () => save },
    };
    const paste = { hidden: true, addEventListener: (type, callback) => (paste[type] = callback) };
    const hint = { textContent: "" };
    const elements = { parcel_api_key: input, parcelPaste: paste, parcelKeyHint: hint };
    vm.runInNewContext(source, {
        document: { getElementById: id => elements[id] },
        navigator: { clipboard },
    });
    return { input, paste, hint, save };
}

test("the Paste button fills the box from the clipboard and moves on to Save", async () => {
    const { input, paste, hint, save } = page({ readText: async () => "  parcel-key-abc\n" });
    assert.equal(paste.hidden, false);

    await paste.click();

    assert.equal(input.value, "parcel-key-abc");
    assert.equal(save.focused, true);
    assert.match(hint.textContent, /^Pasted/);
});

test("a refused or empty clipboard leaves the box to a paste by hand", async () => {
    const refusals = [
        async () => {
            throw new Error("NotAllowedError");
        },
        async () => "   ",
    ];
    for (const readText of refusals) {
        const { input, paste, hint, save } = page({ readText });

        await paste.click();

        assert.equal(input.value, "");
        assert.equal(input.focused, true);
        assert.equal(save.focused, false);
        assert.match(hint.textContent, /press and hold/);
    }
});

test("there is no Paste button where a page can't read the clipboard", () => {
    assert.equal(page(undefined).paste.hidden, true);
    assert.equal(page({}).paste.hidden, true);
});
