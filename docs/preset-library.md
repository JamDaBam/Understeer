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
- Importing goes through the existing import: the question on a name
  already taken, and saving as a local preset. Before it, the preset's page
  shows its values and the confirmation lists adjusted and left-out
  settings and how its wheelbase compares; a preset made on another or an
  unknown model needs an explicit acknowledgement. Nothing is applied to
  the wheelbase on import.

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
field is missing. New public submissions are held to more: the check in
the preset repository (`scripts/validate_catalog.py` in
[Understeer-Presets](https://github.com/JamDaBam/Understeer-Presets))
additionally requires

- `meta.author`, `meta.game` and `meta.wheelbase`, not empty,
- no entry in `skipped`, no `unsupported` settings and no `adjusted`
  values: a submission must hold exactly what Understeer keeps,
- a new `id` that no other entry uses,
- no web address in the preset name or in `meta.author`, `meta.game`,
  `meta.wheelbase` and `meta.description`: nothing like `scheme://` or
  `www.`. Understeer would show it as plain text only, but a public
  catalog shouldn't send its readers anywhere.

## Preparing a submission

`understeer/submission.py` turns one local preset into a catalog entry
for a submission, as it would stand in `catalog.json`. It is pure: the
preset and the metadata come in as arguments, nothing is read from files,
the system, the network or a device, and the local preset isn't changed.

```python
result = submission.prepare(name, values, author=..., game=...,
                            wheelbase=..., description=..., tags=[...],
                            taken_ids=ids_in_catalog)
submission.entry_json(result.entry)   # the entry as text, deterministic
```

- **Required:** preset name, `game`, `wheelbase`, `author` and
  `description`. `author` is only what the user typed or confirmed;
  nothing fills it in. `wheelbase` must be one of the names Understeer
  shows; an optional `drive` must match it.
- **Nothing is cut or changed quietly.** A text over its limit (as in
  the tables above), with a line break where one line is expected, or
  with control or invisible characters is refused. Only spacing is
  tidied. Settings Understeer doesn't know and values it would keep
  differently (e.g. 905° → 900°) are refused, never left out or fitted.
- **Privacy:** web addresses (the submission rule above), e-mail
  addresses and file paths are refused in every text. Words the caller
  passes as private (e.g. the login name) and serial-like codes give a
  warning, for the user to look at again. Only the given fields and the
  settings end up in the entry: no folder, no path, no device data.
- **Id:** proposed as `<game>-<preset name>` in `a-z`, `0-9` and `-`;
  when taken, `-<author>`, then `-2`, `-3`, … is added. Without latin
  letters it is `preset-` and the fingerprint. Maintainers may still
  choose another id when they add the entry; once published, it stays.
- **Read back:** finally the entry is read with `parse_catalog`; unless
  the parser takes it exactly as prepared, it is refused.

Every problem is reported at once, with the field and a code, for the UI
to show in its own words.

In Understeer, a saved preset's menu has **Share → Share with
Community…** (`understeer/ui/share_dialog.py`). It takes the values of
the saved preset file, never the current tuning, and asks for the public
details; only the preset name is filled in, the wheelbase has to be
chosen. A review page, built from the prepared entry, shows every public
detail, the proposed id with the note that it is not reserved, and every
setting with its exact value; the user confirms with a tick and
**Confirm**. The prepared entry is then kept in memory only.

The last page explains what happens on GitHub and has **Continue on
GitHub…**: it opens the submission form of the preset repository
(`.github/ISSUE_TEMPLATE/community-preset.yml`) in the default browser,
through `Gtk.UriLauncher`, with the fields filled in from URL parameters
named after the form's field ids (`understeer/github_form.py`). The
field *Preset JSON* gets only the preset
(`{"understeer_preset": 1, "name": …, "values": {…}}`); the metadata and
the proposed id go into their own fields, and no acknowledgement box is
ticked. The user checks everything there and submits the issue
themselves; Understeer never submits anything, and says GitHub opened
only when the browser reports it.

GitHub doesn't state a length limit for such addresses. Filling in the
form was tested with an address of about 830 characters; Understeer keeps
addresses to 2000 characters (`MAX_URL_CHARS`). When a submission doesn't
fit, whole fields are left out of the address, the preset JSON first,
then the description, and the page lists them with a **Copy** button
each, to paste into their fields on GitHub. Nothing is cut short, and
nothing is copied unless the user clicks. The address holds only the
public details, but can show up in the browser history.

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
