# License

Original Amadeus project code is licensed under the [MIT License](../LICENSE).

Third-party components and assets — including the Live2D Cubism SDK,
character models, artwork, voice recordings, and model weights — are not
covered by this MIT license and remain subject to their respective licenses
and permissions.

Two external components deserve a special mention:

- **GPT-SoVITS** ([RVC-Boss/GPT-SoVITS](https://github.com/RVC-Boss/GPT-SoVITS),
  by lj1995 and contributors) — the voice engine. MIT-licensed; it is cloned
  at install time and is not part of this repository.
- **CharacterMemory** ([Francesco Caracciolo](https://github.com/FrancescoCaracciolo/CharacterMemory))
  — the long-term memory engine. GPL-3.0-or-later; it is vendored in
  `memory_sidecar/lib/` and runs **only inside the sidecar's own process**
  (127.0.0.1:9870), which keeps the GPL engine cleanly separated from the
  MIT-licensed application. See `memory_sidecar/LICENSE-NOTE.md` for the full
  notes.
