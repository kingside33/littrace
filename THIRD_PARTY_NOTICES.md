# Third-party notices

littrace itself is licensed under **AGPL-3.0-only**; see [`LICENSE`](LICENSE).
This file covers two things:

1. third-party material that is **vendored or inlined** in this repository, and
2. dependencies that need an explanation beyond "installed by pip".

Dependencies installed by pip carry their own licence files in their
distributions; their full texts are not copied here. The version ranges are
the ones declared in [`requirements.txt`](requirements.txt). The licence
expressions below were read from `importlib.metadata` in the maintainer's
environment; re-check the exact resolved versions before a release.

## Icons

`kbapp/web/assets/icons/` contains 17 SVG icons and a `LICENSE` file:

- `book.svg`
- `chart-dots.svg`
- `circle-check.svg`
- `circle.svg`
- `clock.svg`
- `cloud-check.svg`
- `coins.svg`
- `database.svg`
- `file-text.svg`
- `folder.svg`
- `git-branch.svg`
- `player-play.svg`
- `player-stop.svg`
- `plus.svg`
- `scan.svg`
- `search.svg`
- `shield-check.svg`

The vendored `LICENSE` says MIT, Copyright (c) 2020-2026 Paweł Kuna. The full
text is reproduced below.

The vendored `LICENSE` does not record the upstream repository, version or
commit. The icon names and the set are consistent with Tabler Icons, but that
is not stated in the file, so it is not asserted here. **TODO(owner): confirm
the upstream repository and the exact version or commit before the first
public release, and add the link to this section.**

```text
MIT License

Copyright (c) 2020-2026 Paweł Kuna

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

The icons are used to identify actions in the local Web interface. They are
not used as a project logo or as a mark of origin.

## PyMuPDF

PyMuPDF is the one dependency that needs a section of its own.

The installed package reports:

> Dual Licensed - GNU AFFERO GPL 3.0 or Artifex Commercial License

littrace uses the **AGPL-3.0-only** branch. PyMuPDF is a runtime dependency: it is
declared in `requirements.txt` and imported when the application renders a page
or a figure region (for example `kbapp/web/library_api.py:148`). It is not a
test-only or optional extra.

The practical consequence:

- A build that includes PyMuPDF and is distributed under terms other than
  AGPL-3.0-only needs a commercial licence from Artifex. That includes a
  proprietary installer, a closed-source plugin that bundles the runtime, or a
  hosted build whose terms are not compatible with AGPL-3.0-only.
- A build that remains under AGPL-3.0-only can use PyMuPDF under the
  AGPL-3.0-only branch. The obligations of AGPL-3.0-only apply to that build as
  a whole.
- A future paid or closed-source component does not become distributable just
  because the littrace code is separately licensed. If it includes PyMuPDF, it
  needs the Artifex commercial licence or it must remain AGPL-3.0-only.
- Removing PyMuPDF would be a code and product change, not a licensing
  formality. The runtime still uses it.

This is one of the reasons littrace is AGPL-3.0-only rather than a permissive
licence. The commercial-licence path for littrace itself does not change
Artifex's terms, and this notice does not grant any right under them.

## Dependencies

| Package | Version range in `requirements.txt` | Licence expression observed |
|---|---|---|
| fastapi | `>=0.115,<1` | MIT |
| starlette | transitive via FastAPI | BSD-3-Clause |
| uvicorn | `>=0.30,<1` | BSD-3-Clause |
| numpy | `>=1.26,<3` | BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0 |
| PyMuPDF | `>=1.24,<2` | GNU AFFERO GPL 3.0 or Artifex Commercial License |
| PyYAML | `>=6,<7` | MIT |
| httpx | `>=0.27,<1` | BSD-3-Clause |

All of these are compatible with distribution under AGPL-3.0-only. PyMuPDF is
the exception that needs the explanation above: its AGPL-3.0-only branch is
compatible, but a non-AGPL-3.0-only build needs the commercial branch.

## Interoperability is not copied code

littrace reads a Zotero SQLite database and understands the `zotero://` link
protocol. It talks to Ollama and to OpenAI-compatible HTTP endpoints. Those are
interfaces, not copied implementations:

- no Zotero source code is included;
- no Ollama source code is included;
- no OpenAI SDK source code is included.

The names Zotero, PyMuPDF, Artifex, Ollama, DeepSeek, GLM, Zhipu and OpenAlex
are used only to identify the service or format they refer to. They are the
trademarks of their respective owners. See [`TRADEMARKS.md`](TRADEMARKS.md).

## Not bundled

littrace does not bundle:

- fonts;
- CDN-hosted scripts, styles or icons;
- model weights;
- a copy of the Zotero database or of any user's library.

The offline export inlines only assets that are already in this repository, so
it works without a network and does not add a third-party distribution
obligation. This is both a privacy property and a licensing advantage.

## Maintaining this file

- When you add a vendored file, add its licence and attribution here in the
  same pull request.
- When you add a dependency, add its licence expression and version range here
  in the same pull request. A dependency that is not compatible with
  AGPL-3.0-only does not go into the runtime.
- When you remove a vendored file or a dependency, remove its entry rather than
  leaving a stale claim.
- If an upstream licence changes, update this file and the dependency in the
  same change.
