# Community preset library: data contract

This describes the catalog format of the community preset library, format
version 1, and the rules Understeer checks it against. The checks live in
`understeer/library.py`, an example catalog in
`tests/fixtures/library/catalog.json`.

## Architecture

- The whole library is **one self-contained file**, `catalog.json`, with
  every preset embedded. Understeer downloads only this file, over HTTPS,
  from one address built into Understeer; there are no separate downloads
  per preset and no address is ever taken from the catalog.
- One file keeps browsing fast, works offline from the last downloaded
  copy and gives one place to check sizes and limits. At 500 presets of
  about 1 KB each it stays far below the 2 MiB limit. Separate preset files
  are only worth it if the catalog would outgrow that limit.
- Parsing (`library.parse_catalog`) is pure: bytes in, entries out. It
  doesn't touch files, the network or any device. Downloading, caching and
  the UI are separate steps on top of it.
- Importing goes through the existing import: the preview of the presets
  found, the question on a name already taken, and saving as a local
  preset. Nothing is applied to the wheelbase on import.

## Catalog

The catalog is a single UTF-8 JSON file (a byte order mark is allowed):

```json
{
  "understeer_library": 1,
  "presets": [
    {
      "id": "acc-gt3-endurance",
      "meta": {
        "author": "Example Driver",
        "game": "Assetto Corsa Competizione",
        "wheelbase": "CSL DD",
        "drive": "direct_drive",
        "torque_nm": 8,
        "description": "Calm GT3 feel for long stints.",
        "tags": ["gt3", "endurance"]
      },
      "preset": {
        "understeer_preset": 1,
        "name": "GT3 Endurance",
        "values": {"SEN": 540, "FF": 75, "FOR": 100}
      }
    }
  ]
}
```

| Key | Rule |
| --- | --- |
| `understeer_library` | Catalog format, a whole number. A newer format than Understeer knows rejects the whole catalog ("needs a newer version"). |
| `presets` | List of entries, at most 500. |

## Entry

An entry keeps the tuning values (`preset`) apart from the community
metadata (`meta`).

| Key | Required | Rule |
| --- | --- | --- |
| `id` | yes | 1–64 characters of `a-z`, `0-9` and `-`, not starting or ending with `-`. Unique in the catalog. |
| `preset` | yes | A shared preset, exactly as Understeer exports it (see below). |
| `meta` | no | Community metadata (see below). |

### Identifiers

- `id` is chosen once by the catalog maintainers and never changes, also
  when the values of the preset are updated later. Recommended:
  `<game>-<preset name>`, lower case, words joined with `-`, with
  `-<author>` added when the name is taken.
- Understeer additionally computes a **fingerprint** of the values: the
  first 16 hex digits of SHA-256 over the settings sorted by id. It is the
  same for the same values, whatever their order, and tells whether a local
  preset already has exactly these settings.
- Neither the `id` nor anything else in the catalog is ever used as a file
  name or path.

### Preset

The format of an exported preset file (`sharing.preset_json`), so a
contributor can paste their exported file unchanged:

| Key | Rule |
| --- | --- |
| `understeer_preset` | Preset format, 1 to the version Understeer knows (now 1). |
| `name` | Text; shown as one line of printable characters, cut to 80 characters (200 bytes in UTF-8). Must not be empty. |
| `values` | Object of setting id → whole number, 1 to 64 keys. |
| `folder` | Ignored. A catalog doesn't decide the user's folders. |

### Metadata

All fields are optional; a missing field means "not given".

| Key | Type | Rule |
| --- | --- | --- |
| `author` | text | Display name, one line, at most 40 characters. |
| `game` | text | One line, at most 80 characters. |
| `wheelbase` | text | The wheelbase it was made on, one line, at most 60 characters. Use the names Understeer shows (`device.WHEEL_BASES`), e.g. `CSL DD`, `ClubSport Wheel Base V2.5`. |
| `drive` | `"belt"` or `"direct_drive"` | Strength class of the wheelbase. |
| `torque_nm` | whole number 1–40 | Peak torque of the wheelbase in Nm. |
| `description` | text | Plain text, at most 20 lines and 1000 characters. |
| `tags` | list of text | At most 10, each 1–24 characters of `a-z`, `0-9` and `-`; upper case is lowered, duplicates count once. |

Unknown keys in an entry, in `meta` or in the catalog are ignored, so
later versions can add optional fields without a new format. Understeer
never reads them.

## Compatibility

- **Settings Understeer doesn't know** (e.g. added by a newer version):
  left out of the preset and listed as `unsupported`, so the UI can say so.
  Their ids must still look like setting ids (1–8 letters).
- **Settings the preset leaves out:** stay as they are on the wheelbase, as
  with every preset.
- **Settings the connected wheelbase lacks**, or a steering angle above its
  range: handled by the existing import and apply workflow, the same as for
  a local preset.
- **Values in steps** (e.g. 2356°) are taken as the wheelbase keeps them
  (2350°), exactly as a local import does (`sharing.clean_values`, tested
  for every value of every setting). Unlike a local import, each such
  change is listed in `adjusted` as (setting, shared value, kept value), so
  the library's import preview can show "2356° → 2350°".
- **A known setting with an impossible value** (out of range, text, `true`,
  a fraction, an unknown choice) makes the whole preset unusable: better no
  preset than one that differs from what its author shared. This is
  stricter than a local import, which leaves only that setting out.
- A preset with no setting this version knows is skipped.

## Submission rules

The parser keeps all metadata optional, so a catalog stays readable when a
field is missing. New public submissions are held to more: the validation
in the preset repository (a later task) will additionally require

- `meta.author`, `meta.game` and `meta.wheelbase`, not empty,
- no entry in `skipped`, no `unsupported` settings and no `adjusted`
  values: a submission must hold exactly what Understeer keeps,
- a new `id` that no other entry uses.

## Validation

The whole catalog is rejected when it is

- larger than 2 MiB, not UTF-8 or no valid JSON,
- nested too deeply,
- holding `NaN`, `Infinity` or a key twice in the same object,
- not an object, without `understeer_library` or of a newer format,
- without a `presets` list, or with more than 500 entries.

Otherwise each entry is checked on its own. An entry that breaks a rule is
skipped with its position, its `id` (if valid) and the reason; the other
entries stay usable. A later entry with an `id` already used is skipped.

## Security

- The catalog is data only. Nothing in it is run, evaluated or
  deserialised into objects; it is read with `json.loads` alone.
- No field holds a URL Understeer fetches. URLs in a description are plain
  text.
- No field becomes a file name or path. An imported preset is saved under
  its cleaned name through the existing import, which asks before replacing
  a local preset ("Replace" or "Keep Both").
- Texts are shown as plain text, never as Pango markup or links: set them
  with `Gtk.Label.set_text()`, or escape them with
  `GLib.markup_escape_text()` where markup is used around them.
- Reading a catalog writes nothing: not to the wheelbase, not to the
  user's presets. A downloaded preset is only applied after the user
  applies it, through the existing validated workflow.
