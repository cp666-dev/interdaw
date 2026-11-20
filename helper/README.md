# InterDAW helper

The local service behind the InterDAW panel. A LUNA Add On page can't open files on disk, so this
reads Live sets for it and writes the folders Send Back makes. Nothing leaves the Mac.

Users install it with one line (see the main README): `install.sh` is published with each release
as `install-helper.sh`, and installs the code from that release's `interdaw-helper.tar.gz` into
`~/Library/Application Support/InterDAW`, as a login item. From a checkout, `bash helper/install.sh`
installs this folder's code instead. `uninstall.sh` removes it.

To run it in a terminal instead:

```sh
cd helper
python3 -m interdaw_helper      # http://127.0.0.1:8792; set INTERDAW_PORT to change it
```

It needs only Python 3.9 or newer, with no packages to install. It answers the InterDAW page only
(origin `ua-content://com.cp.interdaw`): other origins get a 403, so a website can't read your
sets through it. `INTERDAW_DEV=1` also lets `ua-builder serve` (`http://localhost:8790`) in, for
development.

| Method | Path | Body | Answer |
|---|---|---|---|
| GET | `/health` | | `{ ok, version, api }` |
| POST | `/als/read` | `{ path }` | The import plan, or `{ error }` with status 422 |
| POST | `/return/prepare` | `{ setPath, parent, format }` | `{ folder }`: a new, empty return folder |
| POST | `/return/build` | `{ folder, setPath, prints, moves, removed, warnings, format, sessionTempo }` | What was made, once stems are in `Stems/` |

`api` goes up when a route or body changes, so the panel can ask for an update. `format` is
`both`, `live` (the set and only what changed) or `stems` (no set). It defaults to `both`.

Times in a plan are Live beats on the arrangement and seconds into the audio file. Looped clips
are expanded into one piece per pass, because LUNA has no looping clip to map them to.

## Tests

```sh
python3 -m unittest discover -s tests -t .
```

Set `INTERDAW_TEST_ALS` to a real set's path to include it. Keep other people's sets out of git.
