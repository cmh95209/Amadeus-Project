import assert from "node:assert/strict";
import { isKanaReading, nameListProblem } from "../src/components/NameListEditor";

// Settings -> Preferences -> Names she knows: the same rule the backend uses.
assert.equal(isKanaReading("イネファ"), true);
assert.equal(isKanaReading("ひらがな"), true);
assert.equal(isKanaReading("シュタインズ・ゲート"), true);
assert.equal(isKanaReading("Ineffa"), false);
assert.equal(isKanaReading("イネfa"), false);
assert.equal(isKanaReading("漢字"), false);

// All fine, and empty rows are simply dropped on Save.
assert.equal(nameListProblem([]), "");
assert.equal(nameListProblem([{ word: "Ineffa", reading: "イネファ" }, { word: " ", reading: "" }]), "");
// The first problem row is named.
assert.match(nameListProblem([{ word: "Ineffa", reading: "" }]), /"Ineffa": add how she writes it/);
assert.match(nameListProblem([{ word: "Aino", reading: "aino" }]), /"Aino": write how she says it in katakana/);
assert.match(nameListProblem([{ word: "", reading: "イネファ" }]), /Add the name for イネファ/);
assert.match(nameListProblem([{ word: "A=B", reading: "エー" }]), /can't contain "="/);

console.log("nameList.test.ts: ok");
