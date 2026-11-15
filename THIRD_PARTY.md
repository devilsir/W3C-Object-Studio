# Credits and third-party components

## WC3 Reforged Object Studio
- **Created by:** DarkSir#1620
- Independent, unofficial Warcraft III tooling project.

## Third-party projects
- **War3AssetsImporter** — Jaccouille — MIT — https://github.com/Jaccouille/war3-assets-importer
  - Used as design/implementation reference for the integrated batch asset-import, custom-unit creation, and map-placement workflow. Object Studio uses its own Python MPQ/object/DOO transaction layer rather than bundling the Java application.
  - License: `licenses/War3AssetsImporter-LICENSE.txt`
- **StormLib 9.40** — Ladislav Zezula — MIT — https://github.com/ladislav-zezula/StormLib
  - MPQ archive access.
  - License: `licenses/StormLib-LICENSE.txt`
- **CascLib** — Ladislav Zezula — MIT — https://github.com/ladislav-zezula/CascLib
  - Read-only access to the user's local Warcraft III Reforged CASC installation.
  - License: `licenses/CascLib-LICENSE.txt`
- **war3-model 4.0.1** — 4eb0da — MIT — https://github.com/4eb0da/war3-model
  - MDX/MDL parsing and WebGL rendering used by the bundled viewer. Object Studio applies compatibility patches for current Reforged/DE models.
  - License: `licenses/war3-model-LICENSE.txt`
- **gl-matrix 3.4.3** — Brandon Jones / Colin MacKenzie IV — MIT — https://github.com/toji/gl-matrix
  - Matrix/vector math used by the WebGL viewer.
  - License: `licenses/gl-matrix-LICENSE.txt`
- **tkwry 0.1.9** — mashu3 — MIT — https://github.com/mashu3/tkwry
  - Child WebView for Tkinter; on Windows it embeds Microsoft WebView2 directly inside the preview panel.
  - License: `licenses/tkwry-LICENSE.txt`
- **Pillow** — Pillow contributors — MIT-CMU — https://github.com/python-pillow/Pillow
  - Image decoding/preview support.
  - License: `licenses/Pillow-LICENSE.txt`
- **Microsoft Edge WebView2 Runtime** — Microsoft — https://developer.microsoft.com/microsoft-edge/webview2/
  - External runtime used to host the WebGL viewer on Windows. It is not redistributed by Object Studio.

## Warcraft III / Blizzard
Warcraft III, Warcraft III: Reforged and their names, formats, trademarks and assets belong to their respective rights holders, including Blizzard Entertainment. WC3 Reforged Object Studio is an independent project and is not affiliated with, endorsed by, or published by Blizzard Entertainment. Native Warcraft assets are read from the user's local installation when needed; the Studio claims no ownership over them.
