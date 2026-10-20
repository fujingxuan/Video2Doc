# Third-party notices

Video2Doc's own code is available under the root MIT LICENSE. Third-party code remains
under its original terms; the MIT license does not replace those terms.

This inventory records installed backend runtime dependencies, the frontend production
dependency graph (including its build tooling), Python native runtime notices, and
PyInstaller's bootloader exception. It is intentionally broader than the files retained
by PyInstaller or Vite. Development/test packages outside this graph are not redistributed.

Some auxiliary `_pytest` modules are retained by PyInstaller; their notices are included.
This does not mean the complete pytest development environment is bundled.

Exact installed versions, copied notice paths and SHA-256 hashes are in
[third_party_licenses/manifest.json](third_party_licenses/manifest.json).

Regenerate using `backend\.venv\Scripts\python.exe scripts\collect_release_licenses.py`
after installing the project dependencies. The first run downloads versioned Python and
OpenSSL notices from their official repositories; subsequent runs reuse those files.

## External programs and models

The public Windows package does not include FFmpeg, ffprobe, yt-dlp, ASR/OCR weights or
their separate runtimes. Install them from their official sources and follow their own
licenses. FFmpeg licensing depends on the selected build. The official Windows yt-dlp
PyInstaller executable includes GPLv3+ components; a custom `-BundleYtDlp` build requires
the applicable license and corresponding-source materials. The build switch does not
prepare or verify those materials.

- FFmpeg: https://ffmpeg.org/legal.html
- yt-dlp: https://github.com/yt-dlp/yt-dlp#licensing
- PyInstaller: https://pyinstaller.org/en/stable/license.html

Microsoft runtime components retain the Microsoft restrictions included in
[Python-LICENSE.txt](third_party_licenses/runtime/Python-LICENSE.txt), including Windows-only
redistribution and protection of those components. SQLite's public-domain dedication
is archived separately. libffi's license is archived
from the upstream project; its exact native library version is not advertised by Python.

The unmodified certifi source is available from its versioned PyPI source distribution:
https://pypi.org/project/certifi/2026.6.17/#files . Its files remain
under MPL-2.0; see the archived license. XZ/liblzma's historical public-domain licensing
reference is retained; Python does not expose its exact native liblzma build version.

## Installed component inventory

| Component | Version | License metadata | License texts |
| --- | --- | --- | --- |
| [alembic](https://alembic.sqlalchemy.org) | 1.18.5 | MIT | [notices](third_party_licenses/python/alembic-1.18.5/alembic-1.18.5.dist-info/licenses/LICENSE) |
| [altgraph](https://altgraph.readthedocs.io) | 0.17.5 | MIT | [notices](third_party_licenses/python/altgraph-0.17.5/altgraph-0.17.5.dist-info/LICENSE) |
| [annotated-doc](https://github.com/fastapi/annotated-doc) | 0.0.4 | MIT | [notices](third_party_licenses/python/annotated-doc-0.0.4/annotated_doc-0.0.4.dist-info/licenses/LICENSE) |
| [annotated-types](https://github.com/annotated-types/annotated-types) | 0.7.0 | See license files | [notices](third_party_licenses/python/annotated-types-0.7.0/annotated_types-0.7.0.dist-info/licenses/LICENSE) |
| [anyio](https://anyio.readthedocs.io/en/latest/) | 4.14.2 | MIT | [notices](third_party_licenses/python/anyio-4.14.2/anyio-4.14.2.dist-info/licenses/LICENSE) |
| [certifi](https://github.com/certifi/python-certifi) | 2026.6.17 | MPL-2.0 | [notices](third_party_licenses/python/certifi-2026.6.17/certifi-2026.6.17.dist-info/licenses/LICENSE) |
| [click](https://click.palletsprojects.com/page/changes/) | 8.4.2 | BSD-3-Clause | [notices](third_party_licenses/python/click-8.4.2/click-8.4.2.dist-info/licenses/LICENSE.txt) |
| [colorama](https://github.com/tartley/colorama) | 0.4.6 | See license files | [notices](third_party_licenses/python/colorama-0.4.6/colorama-0.4.6.dist-info/licenses/LICENSE.txt) |
| [fastapi](https://github.com/fastapi/fastapi) | 0.139.2 | MIT | [notices](third_party_licenses/python/fastapi-0.139.2/fastapi-0.139.2.dist-info/licenses/LICENSE) |
| [greenlet](https://greenlet.readthedocs.io) | 3.5.3 | MIT AND PSF-2.0 | [notices](third_party_licenses/python/greenlet-3.5.3/greenlet-3.5.3.dist-info/licenses/LICENSE) |
| [h11](https://github.com/python-hyper/h11) | 0.16.0 | MIT | [notices](third_party_licenses/python/h11-0.16.0/h11-0.16.0.dist-info/licenses/LICENSE.txt) |
| [httpcore](https://www.encode.io/httpcore) | 1.0.9 | BSD-3-Clause | [notices](third_party_licenses/python/httpcore-1.0.9/httpcore-1.0.9.dist-info/licenses/LICENSE.md) |
| [httptools](https://github.com/MagicStack/httptools) | 0.8.0 | MIT | [notices](third_party_licenses/python/httptools-0.8.0/httptools-0.8.0.dist-info/licenses/LICENSE) |
| [httpx](https://github.com/encode/httpx/blob/master/CHANGELOG.md) | 0.28.1 | BSD-3-Clause | [notices](third_party_licenses/python/httpx-0.28.1/httpx-0.28.1.dist-info/licenses/LICENSE.md) |
| [idna](https://github.com/kjd/idna/blob/master/HISTORY.md) | 3.18 | BSD-3-Clause | [notices](third_party_licenses/python/idna-3.18/idna-3.18.dist-info/licenses/LICENSE.md) |
| [iniconfig](https://github.com/pytest-dev/iniconfig) | 2.3.0 | MIT | [notices](third_party_licenses/python/iniconfig-2.3.0/iniconfig-2.3.0.dist-info/licenses/LICENSE) |
| [lxml](https://lxml.de/) | 6.1.3 | BSD-3-Clause | [notices](third_party_licenses/python/lxml-6.1.3/lxml-6.1.3.dist-info/licenses/LICENSE.txt) |
| [Mako](https://www.makotemplates.org/) | 1.3.12 | MIT | [notices](third_party_licenses/python/mako-1.3.12/mako-1.3.12.dist-info/licenses/LICENSE) |
| [markdown-it-py](https://markdown-it-py.readthedocs.io) | 4.2.0 | See license files | [notices](third_party_licenses/python/markdown-it-py-4.2.0/markdown_it_py-4.2.0.dist-info/licenses/LICENSE) |
| [MarkupSafe](https://palletsprojects.com/donate) | 3.0.3 | BSD-3-Clause | [notices](third_party_licenses/python/markupsafe-3.0.3/markupsafe-3.0.3.dist-info/licenses/LICENSE.txt) |
| [mdurl](https://github.com/executablebooks/mdurl) | 0.1.2 | See license files | [notices](third_party_licenses/python/mdurl-0.1.2/mdurl-0.1.2.dist-info/LICENSE) |
| [packaging](https://packaging.pypa.io/) | 26.2 | Apache-2.0 OR BSD-2-Clause | [notices](third_party_licenses/python/packaging-26.2/packaging-26.2.dist-info/licenses/LICENSE) |
| [pefile](https://github.com/erocarrera/pefile) | 2024.8.26 | MIT | [notices](third_party_licenses/python/pefile-2024.8.26/pefile-2024.8.26.dist-info/LICENSE) |
| [pillow](https://pillow.readthedocs.io/en/stable/releasenotes/index.html) | 12.3.0 | MIT-CMU | [notices](third_party_licenses/python/pillow-12.3.0/pillow-12.3.0.dist-info/licenses/LICENSE) |
| [pluggy](https://pypi.org/project/pluggy/1.6.0/) | 1.6.0 | MIT | [notices](third_party_licenses/python/pluggy-1.6.0/pluggy-1.6.0.dist-info/licenses/LICENSE) |
| [pydantic](https://github.com/pydantic/pydantic) | 2.13.4 | MIT | [notices](third_party_licenses/python/pydantic-2.13.4/pydantic-2.13.4.dist-info/licenses/LICENSE) |
| [pydantic_core](https://github.com/pydantic/pydantic) | 2.46.4 | MIT | [notices](third_party_licenses/python/pydantic-core-2.46.4/pydantic_core-2.46.4.dist-info/licenses/LICENSE) |
| [Pygments](https://pygments.org) | 2.20.0 | BSD-2-Clause | [notices](third_party_licenses/python/pygments-2.20.0/pygments-2.20.0.dist-info/licenses/AUTHORS) |
| [pyinstaller](https://pyinstaller.org) | 6.21.0 | GPLv2-or-later with a special exception which allows to use PyInstaller to build and distribute non-free programs (including commercial ones) | [notices](third_party_licenses/python/pyinstaller-6.21.0/pyinstaller-6.21.0.dist-info/licenses/COPYING.txt) |
| [pyinstaller-hooks-contrib](https://github.com/pyinstaller/pyinstaller-hooks-contrib) | 2026.6 | See license files | [notices](third_party_licenses/python/pyinstaller-hooks-contrib-2026.6/pyinstaller_hooks_contrib-2026.6.dist-info/licenses/LICENSE) |
| [pytest](https://docs.pytest.org/en/stable/changelog.html) | 8.4.2 | MIT | [notices](third_party_licenses/python/pytest-8.4.2/pytest-8.4.2.dist-info/licenses/AUTHORS) |
| [python-docx](https://github.com/python-openxml/python-docx/blob/master/HISTORY.rst) | 1.2.0 | MIT | [notices](third_party_licenses/python/python-docx-1.2.0/python_docx-1.2.0.dist-info/licenses/LICENSE) |
| [python-dotenv](https://github.com/theskumar/python-dotenv) | 1.2.2 | BSD-3-Clause | [notices](third_party_licenses/python/python-dotenv-1.2.2/python_dotenv-1.2.2.dist-info/licenses/LICENSE) |
| [python-multipart](https://github.com/Kludex/python-multipart) | 0.0.32 | Apache-2.0 | [notices](third_party_licenses/python/python-multipart-0.0.32/python_multipart-0.0.32.dist-info/licenses/LICENSE.txt) |
| [pywin32-ctypes](https://github.com/enthought/pywin32-ctypes) | 0.2.3 | BSD-3-Clause | [notices](third_party_licenses/python/pywin32-ctypes-0.2.3/pywin32_ctypes-0.2.3.dist-info/LICENSE.txt) |
| [PyYAML](https://pyyaml.org/) | 6.0.3 | MIT | [notices](third_party_licenses/python/pyyaml-6.0.3/pyyaml-6.0.3.dist-info/licenses/LICENSE) |
| [setuptools](https://github.com/pypa/setuptools) | 83.0.0 | MIT | [notices](third_party_licenses/python/setuptools-83.0.0/setuptools-83.0.0.dist-info/licenses/LICENSE) |
| [SQLAlchemy](https://www.sqlalchemy.org) | 2.0.51 | MIT | [notices](third_party_licenses/python/sqlalchemy-2.0.51/sqlalchemy-2.0.51.dist-info/licenses/LICENSE) |
| [starlette](https://github.com/Kludex/starlette) | 1.3.1 | BSD-3-Clause | [notices](third_party_licenses/python/starlette-1.3.1/starlette-1.3.1.dist-info/licenses/LICENSE.md) |
| [typing-inspection](https://github.com/pydantic/typing-inspection) | 0.4.2 | MIT | [notices](third_party_licenses/python/typing-inspection-0.4.2/typing_inspection-0.4.2.dist-info/licenses/LICENSE) |
| [typing_extensions](https://github.com/python/typing_extensions/issues) | 4.16.0 | PSF-2.0 | [notices](third_party_licenses/python/typing-extensions-4.16.0/typing_extensions-4.16.0.dist-info/licenses/LICENSE) |
| [uvicorn](https://uvicorn.dev/release-notes) | 0.51.0 | BSD-3-Clause | [notices](third_party_licenses/python/uvicorn-0.51.0/uvicorn-0.51.0.dist-info/licenses/LICENSE.md) |
| [watchfiles](https://github.com/samuelcolvin/watchfiles) | 1.2.0 | MIT | [notices](third_party_licenses/python/watchfiles-1.2.0/watchfiles-1.2.0.dist-info/licenses/LICENSE) |
| [websockets](https://github.com/python-websockets/websockets) | 16.1.1 | BSD-3-Clause | [notices](third_party_licenses/python/websockets-16.1.1/websockets-16.1.1.dist-info/licenses/LICENSE) |
| [CPython and standard-library third parties](https://www.python.org/downloads/release/python-31214/) | 3.12.14 | PSF and component notices | [notices](third_party_licenses/runtime/Python-LICENSE.txt) |
| [Expat](https://github.com/libexpat/libexpat/tree/R_2_8_3) | 2.8.3 | MIT | [notices](third_party_licenses/runtime/Expat-COPYING.txt) |
| [libffi](https://github.com/libffi/libffi) | see bundled runtime binary | MIT | [notices](third_party_licenses/runtime/libffi-LICENSE.txt) |
| [OpenSSL](https://github.com/openssl/openssl/tree/openssl-3.5.8) | 3.5.8 | Apache-2.0 | [notices](third_party_licenses/runtime/OpenSSL-LICENSE.txt) |
| [SQLite](https://www.sqlite.org/copyright.html) | 3.53.1 | Public domain | [notices](third_party_licenses/runtime/SQLite-PUBLIC-DOMAIN.html) |
| [XZ / liblzma](https://github.com/tukaani-project/xz/tree/v5.2.5) | not exposed by Python | Public domain (liblzma; see component notice) | [notices](third_party_licenses/runtime/XZ-liblzma-COPYING.txt) |
| [@babel/code-frame](https://babel.dev/docs/en/next/babel-code-frame) | 7.29.7 | MIT | [notices](third_party_licenses/frontend/_babel_code-frame-7.29.7/LICENSE) |
| [@babel/compat-data](https://github.com/babel/babel) | 7.29.7 | MIT | [notices](third_party_licenses/frontend/_babel_compat-data-7.29.7/LICENSE) |
| [@babel/core](https://babel.dev/docs/en/next/babel-core) | 7.29.7 | MIT | [notices](third_party_licenses/frontend/_babel_core-7.29.7/LICENSE) |
| [@babel/generator](https://babel.dev/docs/en/next/babel-generator) | 7.29.7 | MIT | [notices](third_party_licenses/frontend/_babel_generator-7.29.7/LICENSE) |
| [@babel/helper-compilation-targets](https://github.com/babel/babel) | 7.29.7 | MIT | [notices](third_party_licenses/frontend/_babel_helper-compilation-targets-7.29.7/LICENSE) |
| [@babel/helper-globals](https://github.com/babel/babel) | 7.29.7 | MIT | [notices](third_party_licenses/frontend/_babel_helper-globals-7.29.7/LICENSE) |
| [@babel/helper-module-imports](https://babel.dev/docs/en/next/babel-helper-module-imports) | 7.29.7 | MIT | [notices](third_party_licenses/frontend/_babel_helper-module-imports-7.29.7/LICENSE) |
| [@babel/helper-module-transforms](https://babel.dev/docs/en/next/babel-helper-module-transforms) | 7.29.7 | MIT | [notices](third_party_licenses/frontend/_babel_helper-module-transforms-7.29.7/LICENSE) |
| [@babel/helper-plugin-utils](https://babel.dev/docs/en/next/babel-helper-plugin-utils) | 7.29.7 | MIT | [notices](third_party_licenses/frontend/_babel_helper-plugin-utils-7.29.7/LICENSE) |
| [@babel/helper-string-parser](https://babel.dev/docs/en/next/babel-helper-string-parser) | 7.29.7 | MIT | [notices](third_party_licenses/frontend/_babel_helper-string-parser-7.29.7/LICENSE) |
| [@babel/helper-validator-identifier](https://github.com/babel/babel) | 7.29.7 | MIT | [notices](third_party_licenses/frontend/_babel_helper-validator-identifier-7.29.7/LICENSE) |
| [@babel/helper-validator-option](https://github.com/babel/babel) | 7.29.7 | MIT | [notices](third_party_licenses/frontend/_babel_helper-validator-option-7.29.7/LICENSE) |
| [@babel/helpers](https://babel.dev/docs/en/next/babel-helpers) | 7.29.7 | MIT | [notices](third_party_licenses/frontend/_babel_helpers-7.29.7/LICENSE) |
| [@babel/parser](https://babel.dev/docs/en/next/babel-parser) | 7.29.7 | MIT | [notices](third_party_licenses/frontend/_babel_parser-7.29.7/LICENSE) |
| [@babel/plugin-transform-react-jsx-self](https://babel.dev/docs/en/next/babel-plugin-transform-react-jsx-self) | 7.29.7 | MIT | [notices](third_party_licenses/frontend/_babel_plugin-transform-react-jsx-self-7.29.7/LICENSE) |
| [@babel/plugin-transform-react-jsx-source](https://babel.dev/docs/en/next/babel-plugin-transform-react-jsx-source) | 7.29.7 | MIT | [notices](third_party_licenses/frontend/_babel_plugin-transform-react-jsx-source-7.29.7/LICENSE) |
| [@babel/template](https://babel.dev/docs/en/next/babel-template) | 7.29.7 | MIT | [notices](third_party_licenses/frontend/_babel_template-7.29.7/LICENSE) |
| [@babel/traverse](https://babel.dev/docs/en/next/babel-traverse) | 7.29.7 | MIT | [notices](third_party_licenses/frontend/_babel_traverse-7.29.7/LICENSE) |
| [@babel/types](https://babel.dev/docs/en/next/babel-types) | 7.29.7 | MIT | [notices](third_party_licenses/frontend/_babel_types-7.29.7/LICENSE) |
| [@esbuild/win32-x64](https://github.com/evanw/esbuild) | 0.28.1 | MIT | [notices](third_party_licenses/frontend/_esbuild_win32-x64-0.28.1/LICENSE.md) |
| [@jridgewell/gen-mapping](https://github.com/jridgewell/sourcemaps/tree/main/packages/gen-mapping) | 0.3.13 | MIT | [notices](third_party_licenses/frontend/_jridgewell_gen-mapping-0.3.13/LICENSE) |
| [@jridgewell/remapping](https://github.com/jridgewell/sourcemaps/tree/main/packages/remapping) | 2.3.5 | MIT | [notices](third_party_licenses/frontend/_jridgewell_remapping-2.3.5/LICENSE) |
| [@jridgewell/resolve-uri](https://github.com/jridgewell/resolve-uri) | 3.1.2 | MIT | [notices](third_party_licenses/frontend/_jridgewell_resolve-uri-3.1.2/LICENSE) |
| [@jridgewell/sourcemap-codec](https://github.com/jridgewell/sourcemaps/tree/main/packages/sourcemap-codec) | 1.5.5 | MIT | [notices](third_party_licenses/frontend/_jridgewell_sourcemap-codec-1.5.5/LICENSE) |
| [@jridgewell/trace-mapping](https://github.com/jridgewell/sourcemaps/tree/main/packages/trace-mapping) | 0.3.31 | MIT | [notices](third_party_licenses/frontend/_jridgewell_trace-mapping-0.3.31/LICENSE) |
| [@rolldown/pluginutils](https://rolldown.rs/) | 1.0.0-rc.3 | MIT | [notices](third_party_licenses/frontend/_rolldown_pluginutils-1.0.0-rc.3/LICENSE) |
| [@rollup/rollup-win32-x64-gnu](https://rollupjs.org/) | 4.62.2 | MIT | [notices](third_party_licenses/frontend/_rollup_rollup-win32-x64-gnu-4.62.2/LICENSE.md) |
| [@rollup/rollup-win32-x64-msvc](https://rollupjs.org/) | 4.62.2 | MIT | [notices](third_party_licenses/frontend/_rollup_rollup-win32-x64-msvc-4.62.2/LICENSE.md) |
| [@types/babel__core](https://github.com/DefinitelyTyped/DefinitelyTyped/tree/master/types/babel__core) | 7.20.5 | MIT | [notices](third_party_licenses/frontend/_types_babel__core-7.20.5/LICENSE) |
| [@types/babel__generator](https://github.com/DefinitelyTyped/DefinitelyTyped/tree/master/types/babel__generator) | 7.27.0 | MIT | [notices](third_party_licenses/frontend/_types_babel__generator-7.27.0/LICENSE) |
| [@types/babel__template](https://github.com/DefinitelyTyped/DefinitelyTyped/tree/master/types/babel__template) | 7.4.4 | MIT | [notices](third_party_licenses/frontend/_types_babel__template-7.4.4/LICENSE) |
| [@types/babel__traverse](https://github.com/DefinitelyTyped/DefinitelyTyped/tree/master/types/babel__traverse) | 7.28.0 | MIT | [notices](third_party_licenses/frontend/_types_babel__traverse-7.28.0/LICENSE) |
| [@types/debug](https://github.com/DefinitelyTyped/DefinitelyTyped/tree/master/types/debug) | 4.1.13 | MIT | [notices](third_party_licenses/frontend/_types_debug-4.1.13/LICENSE) |
| [@types/estree](https://github.com/DefinitelyTyped/DefinitelyTyped/tree/master/types/estree) | 1.0.9 | MIT | [notices](third_party_licenses/frontend/_types_estree-1.0.9/LICENSE) |
| [@types/estree-jsx](https://github.com/DefinitelyTyped/DefinitelyTyped/tree/master/types/estree-jsx) | 1.0.5 | MIT | [notices](third_party_licenses/frontend/_types_estree-jsx-1.0.5/LICENSE) |
| [@types/hast](https://github.com/DefinitelyTyped/DefinitelyTyped/tree/master/types/hast) | 3.0.5 | MIT | [notices](third_party_licenses/frontend/_types_hast-3.0.5/LICENSE) |
| [@types/mdast](https://github.com/DefinitelyTyped/DefinitelyTyped/tree/master/types/mdast) | 4.0.4 | MIT | [notices](third_party_licenses/frontend/_types_mdast-4.0.4/LICENSE) |
| [@types/ms](https://github.com/DefinitelyTyped/DefinitelyTyped/tree/master/types/ms) | 2.1.0 | MIT | [notices](third_party_licenses/frontend/_types_ms-2.1.0/LICENSE) |
| [@types/unist](https://github.com/DefinitelyTyped/DefinitelyTyped/tree/master/types/unist) | 2.0.11 | MIT | [notices](third_party_licenses/frontend/_types_unist-2.0.11/LICENSE) |
| [@types/unist](https://github.com/DefinitelyTyped/DefinitelyTyped/tree/master/types/unist) | 3.0.3 | MIT | [notices](third_party_licenses/frontend/_types_unist-3.0.3/LICENSE) |
| [@ungap/structured-clone](https://github.com/ungap/structured-clone#readme) | 1.3.3 | ISC | [notices](third_party_licenses/frontend/_ungap_structured-clone-1.3.3/LICENSE) |
| [@vitejs/plugin-react](https://github.com/vitejs/vite-plugin-react/tree/main/packages/plugin-react#readme) | 5.2.0 | MIT | [notices](third_party_licenses/frontend/_vitejs_plugin-react-5.2.0/LICENSE) |
| [bail](https://github.com/wooorm/bail) | 2.0.2 | MIT | [notices](third_party_licenses/frontend/bail-2.0.2/license) |
| [baseline-browser-mapping](https://github.com/web-platform-dx/baseline-browser-mapping) | 2.10.43 | Apache-2.0 | [notices](third_party_licenses/frontend/baseline-browser-mapping-2.10.43/LICENSE.txt) |
| [browserslist](https://github.com/browserslist/browserslist) | 4.28.6 | MIT | [notices](third_party_licenses/frontend/browserslist-4.28.6/LICENSE) |
| [caniuse-lite](https://github.com/browserslist/caniuse-lite) | 1.0.30001806 | CC-BY-4.0 | [notices](third_party_licenses/frontend/caniuse-lite-1.0.30001806/LICENSE) |
| [ccount](https://github.com/wooorm/ccount) | 2.0.1 | MIT | [notices](third_party_licenses/frontend/ccount-2.0.1/license) |
| [character-entities](https://github.com/wooorm/character-entities) | 2.0.2 | MIT | [notices](third_party_licenses/frontend/character-entities-2.0.2/license) |
| [character-entities-html4](https://github.com/wooorm/character-entities-html4) | 2.1.0 | MIT | [notices](third_party_licenses/frontend/character-entities-html4-2.1.0/license) |
| [character-entities-legacy](https://github.com/wooorm/character-entities-legacy) | 3.0.0 | MIT | [notices](third_party_licenses/frontend/character-entities-legacy-3.0.0/license) |
| [character-reference-invalid](https://github.com/wooorm/character-reference-invalid) | 2.0.1 | MIT | [notices](third_party_licenses/frontend/character-reference-invalid-2.0.1/license) |
| [comma-separated-tokens](https://github.com/wooorm/comma-separated-tokens) | 2.0.3 | MIT | [notices](third_party_licenses/frontend/comma-separated-tokens-2.0.3/license) |
| [convert-source-map](https://github.com/thlorenz/convert-source-map) | 2.0.0 | MIT | [notices](third_party_licenses/frontend/convert-source-map-2.0.0/LICENSE) |
| [debug](https://github.com/debug-js/debug) | 4.4.3 | MIT | [notices](third_party_licenses/frontend/debug-4.4.3/LICENSE) |
| [decode-named-character-reference](https://github.com/wooorm/decode-named-character-reference) | 1.3.0 | MIT | [notices](third_party_licenses/frontend/decode-named-character-reference-1.3.0/license) |
| [dequal](https://github.com/lukeed/dequal) | 2.0.3 | MIT | [notices](third_party_licenses/frontend/dequal-2.0.3/license) |
| [devlop](https://github.com/wooorm/devlop) | 1.1.0 | MIT | [notices](third_party_licenses/frontend/devlop-1.1.0/license) |
| [electron-to-chromium](https://github.com/Kilian/electron-to-chromium) | 1.5.392 | ISC | [notices](third_party_licenses/frontend/electron-to-chromium-1.5.392/LICENSE) |
| [entities](https://github.com/fb55/entities) | 6.0.1 | BSD-2-Clause | [notices](third_party_licenses/frontend/entities-6.0.1/LICENSE) |
| [esbuild](https://github.com/evanw/esbuild) | 0.28.1 | MIT | [notices](third_party_licenses/frontend/esbuild-0.28.1/LICENSE.md) |
| [escalade](https://github.com/lukeed/escalade) | 3.2.0 | MIT | [notices](third_party_licenses/frontend/escalade-3.2.0/license) |
| [escape-string-regexp](https://github.com/sindresorhus/escape-string-regexp) | 5.0.0 | MIT | [notices](third_party_licenses/frontend/escape-string-regexp-5.0.0/license) |
| [estree-util-is-identifier-name](https://github.com/syntax-tree/estree-util-is-identifier-name) | 3.0.0 | MIT | [notices](third_party_licenses/frontend/estree-util-is-identifier-name-3.0.0/license) |
| [extend](https://github.com/justmoon/node-extend) | 3.0.2 | MIT | [notices](third_party_licenses/frontend/extend-3.0.2/LICENSE) |
| [fdir](https://github.com/thecodrr/fdir#readme) | 6.5.0 | MIT | [notices](third_party_licenses/frontend/fdir-6.5.0/LICENSE) |
| [gensync](https://github.com/loganfsmyth/gensync) | 1.0.0-beta.2 | MIT | [notices](third_party_licenses/frontend/gensync-1.0.0-beta.2/LICENSE) |
| [hast-util-from-parse5](https://github.com/syntax-tree/hast-util-from-parse5) | 8.0.3 | MIT | [notices](third_party_licenses/frontend/hast-util-from-parse5-8.0.3/license) |
| [hast-util-parse-selector](https://github.com/syntax-tree/hast-util-parse-selector) | 4.0.0 | MIT | [notices](third_party_licenses/frontend/hast-util-parse-selector-4.0.0/license) |
| [hast-util-raw](https://github.com/syntax-tree/hast-util-raw) | 9.1.0 | MIT | [notices](third_party_licenses/frontend/hast-util-raw-9.1.0/license) |
| [hast-util-sanitize](https://github.com/syntax-tree/hast-util-sanitize) | 5.0.2 | MIT | [notices](third_party_licenses/frontend/hast-util-sanitize-5.0.2/license) |
| [hast-util-to-jsx-runtime](https://github.com/syntax-tree/hast-util-to-jsx-runtime) | 2.3.6 | MIT | [notices](third_party_licenses/frontend/hast-util-to-jsx-runtime-2.3.6/license) |
| [hast-util-to-parse5](https://github.com/syntax-tree/hast-util-to-parse5) | 8.0.1 | MIT | [notices](third_party_licenses/frontend/hast-util-to-parse5-8.0.1/license) |
| [hast-util-whitespace](https://github.com/syntax-tree/hast-util-whitespace) | 3.0.0 | MIT | [notices](third_party_licenses/frontend/hast-util-whitespace-3.0.0/license) |
| [hastscript](https://github.com/syntax-tree/hastscript) | 9.0.1 | MIT | [notices](third_party_licenses/frontend/hastscript-9.0.1/license) |
| [html-url-attributes](https://github.com/rehypejs/rehype-minify/tree/main/packages/html-url-attributes) | 3.0.1 | MIT | [notices](third_party_licenses/frontend/html-url-attributes-3.0.1/license) |
| [html-void-elements](https://github.com/wooorm/html-void-elements) | 3.0.0 | MIT | [notices](third_party_licenses/frontend/html-void-elements-3.0.0/license) |
| [inline-style-parser](https://github.com/remarkablemark/inline-style-parser) | 0.2.7 | MIT | [notices](third_party_licenses/frontend/inline-style-parser-0.2.7/LICENSE) |
| [is-alphabetical](https://github.com/wooorm/is-alphabetical) | 2.0.1 | MIT | [notices](third_party_licenses/frontend/is-alphabetical-2.0.1/license) |
| [is-alphanumerical](https://github.com/wooorm/is-alphanumerical) | 2.0.1 | MIT | [notices](third_party_licenses/frontend/is-alphanumerical-2.0.1/license) |
| [is-decimal](https://github.com/wooorm/is-decimal) | 2.0.1 | MIT | [notices](third_party_licenses/frontend/is-decimal-2.0.1/license) |
| [is-hexadecimal](https://github.com/wooorm/is-hexadecimal) | 2.0.1 | MIT | [notices](third_party_licenses/frontend/is-hexadecimal-2.0.1/license) |
| [is-plain-obj](https://github.com/sindresorhus/is-plain-obj) | 4.1.0 | MIT | [notices](third_party_licenses/frontend/is-plain-obj-4.1.0/license) |
| [js-tokens](https://github.com/lydell/js-tokens) | 4.0.0 | MIT | [notices](third_party_licenses/frontend/js-tokens-4.0.0/LICENSE) |
| [jsesc](https://mths.be/jsesc) | 3.1.0 | MIT | [notices](third_party_licenses/frontend/jsesc-3.1.0/LICENSE-MIT.txt) |
| [json5](http://json5.org/) | 2.2.3 | MIT | [notices](third_party_licenses/frontend/json5-2.2.3/LICENSE.md) |
| [longest-streak](https://github.com/wooorm/longest-streak) | 3.1.0 | MIT | [notices](third_party_licenses/frontend/longest-streak-3.1.0/license) |
| [lru-cache](https://github.com/isaacs/node-lru-cache) | 5.1.1 | ISC | [notices](third_party_licenses/frontend/lru-cache-5.1.1/LICENSE) |
| [markdown-table](https://github.com/wooorm/markdown-table) | 3.0.4 | MIT | [notices](third_party_licenses/frontend/markdown-table-3.0.4/license) |
| [mdast-util-find-and-replace](https://github.com/syntax-tree/mdast-util-find-and-replace) | 3.0.2 | MIT | [notices](third_party_licenses/frontend/mdast-util-find-and-replace-3.0.2/license) |
| [mdast-util-from-markdown](https://github.com/syntax-tree/mdast-util-from-markdown) | 2.0.3 | MIT | [notices](third_party_licenses/frontend/mdast-util-from-markdown-2.0.3/license) |
| [mdast-util-gfm](https://github.com/syntax-tree/mdast-util-gfm) | 3.1.0 | MIT | [notices](third_party_licenses/frontend/mdast-util-gfm-3.1.0/license) |
| [mdast-util-gfm-autolink-literal](https://github.com/syntax-tree/mdast-util-gfm-autolink-literal) | 2.0.1 | MIT | [notices](third_party_licenses/frontend/mdast-util-gfm-autolink-literal-2.0.1/license) |
| [mdast-util-gfm-footnote](https://github.com/syntax-tree/mdast-util-gfm-footnote) | 2.1.0 | MIT | [notices](third_party_licenses/frontend/mdast-util-gfm-footnote-2.1.0/license) |
| [mdast-util-gfm-strikethrough](https://github.com/syntax-tree/mdast-util-gfm-strikethrough) | 2.0.0 | MIT | [notices](third_party_licenses/frontend/mdast-util-gfm-strikethrough-2.0.0/license) |
| [mdast-util-gfm-table](https://github.com/syntax-tree/mdast-util-gfm-table) | 2.0.0 | MIT | [notices](third_party_licenses/frontend/mdast-util-gfm-table-2.0.0/license) |
| [mdast-util-gfm-task-list-item](https://github.com/syntax-tree/mdast-util-gfm-task-list-item) | 2.0.0 | MIT | [notices](third_party_licenses/frontend/mdast-util-gfm-task-list-item-2.0.0/license) |
| [mdast-util-mdx-expression](https://github.com/syntax-tree/mdast-util-mdx-expression) | 2.0.1 | MIT | [notices](third_party_licenses/frontend/mdast-util-mdx-expression-2.0.1/license) |
| [mdast-util-mdx-jsx](https://github.com/syntax-tree/mdast-util-mdx-jsx) | 3.2.0 | MIT | [notices](third_party_licenses/frontend/mdast-util-mdx-jsx-3.2.0/license) |
| [mdast-util-mdxjs-esm](https://github.com/syntax-tree/mdast-util-mdxjs-esm) | 2.0.1 | MIT | [notices](third_party_licenses/frontend/mdast-util-mdxjs-esm-2.0.1/license) |
| [mdast-util-phrasing](https://github.com/syntax-tree/mdast-util-phrasing) | 4.1.0 | MIT | [notices](third_party_licenses/frontend/mdast-util-phrasing-4.1.0/license) |
| [mdast-util-to-hast](https://github.com/syntax-tree/mdast-util-to-hast) | 13.2.1 | MIT | [notices](third_party_licenses/frontend/mdast-util-to-hast-13.2.1/license) |
| [mdast-util-to-markdown](https://github.com/syntax-tree/mdast-util-to-markdown) | 2.1.2 | MIT | [notices](third_party_licenses/frontend/mdast-util-to-markdown-2.1.2/license) |
| [mdast-util-to-string](https://github.com/syntax-tree/mdast-util-to-string) | 4.0.0 | MIT | [notices](third_party_licenses/frontend/mdast-util-to-string-4.0.0/license) |
| [micromark](https://github.com/micromark/micromark/tree/main/packages/micromark) | 4.0.2 | MIT | [notices](third_party_licenses/frontend/micromark-4.0.2/license) |
| [micromark-core-commonmark](https://github.com/micromark/micromark/tree/main/packages/micromark-core-commonmark) | 2.0.3 | MIT | [notices](third_party_licenses/frontend/micromark-core-commonmark-2.0.3/license) |
| [micromark-extension-gfm](https://github.com/micromark/micromark-extension-gfm) | 3.0.0 | MIT | [notices](third_party_licenses/frontend/micromark-extension-gfm-3.0.0/license) |
| [micromark-extension-gfm-autolink-literal](https://github.com/micromark/micromark-extension-gfm-autolink-literal) | 2.1.0 | MIT | [notices](third_party_licenses/frontend/micromark-extension-gfm-autolink-literal-2.1.0/license) |
| [micromark-extension-gfm-footnote](https://github.com/micromark/micromark-extension-gfm-footnote) | 2.1.0 | MIT | [notices](third_party_licenses/frontend/micromark-extension-gfm-footnote-2.1.0/license) |
| [micromark-extension-gfm-strikethrough](https://github.com/micromark/micromark-extension-gfm-strikethrough) | 2.1.0 | MIT | [notices](third_party_licenses/frontend/micromark-extension-gfm-strikethrough-2.1.0/license) |
| [micromark-extension-gfm-table](https://github.com/micromark/micromark-extension-gfm-table) | 2.1.1 | MIT | [notices](third_party_licenses/frontend/micromark-extension-gfm-table-2.1.1/license) |
| [micromark-extension-gfm-tagfilter](https://github.com/micromark/micromark-extension-gfm-tagfilter) | 2.0.0 | MIT | [notices](third_party_licenses/frontend/micromark-extension-gfm-tagfilter-2.0.0/license) |
| [micromark-extension-gfm-task-list-item](https://github.com/micromark/micromark-extension-gfm-task-list-item) | 2.1.0 | MIT | [notices](third_party_licenses/frontend/micromark-extension-gfm-task-list-item-2.1.0/license) |
| [micromark-factory-destination](https://github.com/micromark/micromark/tree/main/packages/micromark-factory-destination) | 2.0.1 | MIT | [notices](third_party_licenses/frontend/micromark-factory-destination-2.0.1/license) |
| [micromark-factory-label](https://github.com/micromark/micromark/tree/main/packages/micromark-factory-label) | 2.0.1 | MIT | [notices](third_party_licenses/frontend/micromark-factory-label-2.0.1/license) |
| [micromark-factory-space](https://github.com/micromark/micromark/tree/main/packages/micromark-factory-space) | 2.0.1 | MIT | [notices](third_party_licenses/frontend/micromark-factory-space-2.0.1/license) |
| [micromark-factory-title](https://github.com/micromark/micromark/tree/main/packages/micromark-factory-title) | 2.0.1 | MIT | [notices](third_party_licenses/frontend/micromark-factory-title-2.0.1/license) |
| [micromark-factory-whitespace](https://github.com/micromark/micromark/tree/main/packages/micromark-factory-whitespace) | 2.0.1 | MIT | [notices](third_party_licenses/frontend/micromark-factory-whitespace-2.0.1/license) |
| [micromark-util-character](https://github.com/micromark/micromark/tree/main/packages/micromark-util-character) | 2.1.1 | MIT | [notices](third_party_licenses/frontend/micromark-util-character-2.1.1/license) |
| [micromark-util-chunked](https://github.com/micromark/micromark/tree/main/packages/micromark-util-chunked) | 2.0.1 | MIT | [notices](third_party_licenses/frontend/micromark-util-chunked-2.0.1/license) |
| [micromark-util-classify-character](https://github.com/micromark/micromark/tree/main/packages/micromark-util-classify-character) | 2.0.1 | MIT | [notices](third_party_licenses/frontend/micromark-util-classify-character-2.0.1/license) |
| [micromark-util-combine-extensions](https://github.com/micromark/micromark/tree/main/packages/micromark-util-combine-extensions) | 2.0.1 | MIT | [notices](third_party_licenses/frontend/micromark-util-combine-extensions-2.0.1/license) |
| [micromark-util-decode-numeric-character-reference](https://github.com/micromark/micromark/tree/main/packages/micromark-util-decode-numeric-character-reference) | 2.0.2 | MIT | [notices](third_party_licenses/frontend/micromark-util-decode-numeric-character-reference-2.0.2/license) |
| [micromark-util-decode-string](https://github.com/micromark/micromark/tree/main/packages/micromark-util-decode-string) | 2.0.1 | MIT | [notices](third_party_licenses/frontend/micromark-util-decode-string-2.0.1/license) |
| [micromark-util-encode](https://github.com/micromark/micromark/tree/main/packages/micromark-util-encode) | 2.0.1 | MIT | [notices](third_party_licenses/frontend/micromark-util-encode-2.0.1/license) |
| [micromark-util-html-tag-name](https://github.com/micromark/micromark/tree/main/packages/micromark-util-html-tag-name) | 2.0.1 | MIT | [notices](third_party_licenses/frontend/micromark-util-html-tag-name-2.0.1/license) |
| [micromark-util-normalize-identifier](https://github.com/micromark/micromark/tree/main/packages/micromark-util-normalize-identifier) | 2.0.1 | MIT | [notices](third_party_licenses/frontend/micromark-util-normalize-identifier-2.0.1/license) |
| [micromark-util-resolve-all](https://github.com/micromark/micromark/tree/main/packages/micromark-util-resolve-all) | 2.0.1 | MIT | [notices](third_party_licenses/frontend/micromark-util-resolve-all-2.0.1/license) |
| [micromark-util-sanitize-uri](https://github.com/micromark/micromark/tree/main/packages/micromark-util-sanitize-uri) | 2.0.1 | MIT | [notices](third_party_licenses/frontend/micromark-util-sanitize-uri-2.0.1/license) |
| [micromark-util-subtokenize](https://github.com/micromark/micromark/tree/main/packages/micromark-util-subtokenize) | 2.1.0 | MIT | [notices](third_party_licenses/frontend/micromark-util-subtokenize-2.1.0/license) |
| [micromark-util-symbol](https://github.com/micromark/micromark/tree/main/packages/micromark-util-symbol) | 2.0.1 | MIT | [notices](third_party_licenses/frontend/micromark-util-symbol-2.0.1/license) |
| [micromark-util-types](https://github.com/micromark/micromark/tree/main/packages/micromark-util-types) | 2.0.2 | MIT | [notices](third_party_licenses/frontend/micromark-util-types-2.0.2/license) |
| [ms](https://github.com/vercel/ms) | 2.1.3 | MIT | [notices](third_party_licenses/frontend/ms-2.1.3/license.md) |
| [nanoid](https://github.com/ai/nanoid) | 3.3.16 | MIT | [notices](third_party_licenses/frontend/nanoid-3.3.16/LICENSE) |
| [node-releases](https://github.com/chicoxyzzy/node-releases) | 2.0.51 | MIT | [notices](third_party_licenses/frontend/node-releases-2.0.51/LICENSE) |
| [parse-entities](https://github.com/wooorm/parse-entities) | 4.0.2 | MIT | [notices](third_party_licenses/frontend/parse-entities-4.0.2/license) |
| [parse5](https://parse5.js.org) | 7.3.0 | MIT | [notices](third_party_licenses/frontend/parse5-7.3.0/LICENSE) |
| [picocolors](https://github.com/alexeyraspopov/picocolors) | 1.1.1 | ISC | [notices](third_party_licenses/frontend/picocolors-1.1.1/LICENSE) |
| [picomatch](https://github.com/micromatch/picomatch) | 4.0.5 | MIT | [notices](third_party_licenses/frontend/picomatch-4.0.5/LICENSE) |
| [postcss](https://postcss.org/) | 8.5.19 | MIT | [notices](third_party_licenses/frontend/postcss-8.5.19/LICENSE) |
| [property-information](https://github.com/wooorm/property-information) | 7.2.0 | MIT | [notices](third_party_licenses/frontend/property-information-7.2.0/license) |
| [react](https://react.dev/) | 19.2.7 | MIT | [notices](third_party_licenses/frontend/react-19.2.7/LICENSE) |
| [react-dom](https://react.dev/) | 19.2.7 | MIT | [notices](third_party_licenses/frontend/react-dom-19.2.7/LICENSE) |
| [react-markdown](https://github.com/remarkjs/react-markdown) | 10.1.0 | MIT | [notices](third_party_licenses/frontend/react-markdown-10.1.0/license) |
| [react-refresh](https://react.dev/) | 0.18.0 | MIT | [notices](third_party_licenses/frontend/react-refresh-0.18.0/LICENSE) |
| [rehype-raw](https://github.com/rehypejs/rehype-raw) | 7.0.0 | MIT | [notices](third_party_licenses/frontend/rehype-raw-7.0.0/license) |
| [rehype-sanitize](https://github.com/rehypejs/rehype-sanitize) | 6.0.0 | MIT | [notices](third_party_licenses/frontend/rehype-sanitize-6.0.0/license) |
| [remark-gfm](https://github.com/remarkjs/remark-gfm) | 4.0.1 | MIT | [notices](third_party_licenses/frontend/remark-gfm-4.0.1/license) |
| [remark-parse](https://remark.js.org) | 11.0.0 | MIT | [notices](third_party_licenses/frontend/remark-parse-11.0.0/license) |
| [remark-rehype](https://github.com/remarkjs/remark-rehype) | 11.1.2 | MIT | [notices](third_party_licenses/frontend/remark-rehype-11.1.2/license) |
| [remark-stringify](https://remark.js.org) | 11.0.0 | MIT | [notices](third_party_licenses/frontend/remark-stringify-11.0.0/license) |
| [rollup](https://rollupjs.org/) | 4.62.2 | MIT | [notices](third_party_licenses/frontend/rollup-4.62.2/LICENSE.md) |
| [scheduler](https://react.dev/) | 0.27.0 | MIT | [notices](third_party_licenses/frontend/scheduler-0.27.0/LICENSE) |
| [semver](https://github.com/npm/node-semver) | 6.3.1 | ISC | [notices](third_party_licenses/frontend/semver-6.3.1/LICENSE) |
| [source-map-js](https://github.com/7rulnik/source-map-js) | 1.2.1 | BSD-3-Clause | [notices](third_party_licenses/frontend/source-map-js-1.2.1/LICENSE) |
| [space-separated-tokens](https://github.com/wooorm/space-separated-tokens) | 2.0.2 | MIT | [notices](third_party_licenses/frontend/space-separated-tokens-2.0.2/license) |
| [stringify-entities](https://github.com/wooorm/stringify-entities) | 4.0.4 | MIT | [notices](third_party_licenses/frontend/stringify-entities-4.0.4/license) |
| [style-to-js](https://github.com/remarkablemark/style-to-js) | 1.1.21 | MIT | [notices](third_party_licenses/frontend/style-to-js-1.1.21/LICENSE) |
| [style-to-object](https://github.com/remarkablemark/style-to-object) | 1.0.14 | MIT | [notices](third_party_licenses/frontend/style-to-object-1.0.14/LICENSE) |
| [tinyglobby](https://superchupu.dev/tinyglobby) | 0.2.17 | MIT | [notices](third_party_licenses/frontend/tinyglobby-0.2.17/LICENSE) |
| [trim-lines](https://github.com/wooorm/trim-lines) | 3.0.1 | MIT | [notices](third_party_licenses/frontend/trim-lines-3.0.1/license) |
| [trough](https://github.com/wooorm/trough) | 2.2.0 | MIT | [notices](third_party_licenses/frontend/trough-2.2.0/license) |
| [unified](https://unifiedjs.com) | 11.0.5 | MIT | [notices](third_party_licenses/frontend/unified-11.0.5/license) |
| [unist-util-is](https://github.com/syntax-tree/unist-util-is) | 6.0.1 | MIT | [notices](third_party_licenses/frontend/unist-util-is-6.0.1/license) |
| [unist-util-position](https://github.com/syntax-tree/unist-util-position) | 5.0.0 | MIT | [notices](third_party_licenses/frontend/unist-util-position-5.0.0/license) |
| [unist-util-stringify-position](https://github.com/syntax-tree/unist-util-stringify-position) | 4.0.0 | MIT | [notices](third_party_licenses/frontend/unist-util-stringify-position-4.0.0/license) |
| [unist-util-visit](https://github.com/syntax-tree/unist-util-visit) | 5.1.0 | MIT | [notices](third_party_licenses/frontend/unist-util-visit-5.1.0/license) |
| [unist-util-visit-parents](https://github.com/syntax-tree/unist-util-visit-parents) | 6.0.2 | MIT | [notices](third_party_licenses/frontend/unist-util-visit-parents-6.0.2/license) |
| [update-browserslist-db](https://github.com/browserslist/update-db) | 1.2.3 | MIT | [notices](third_party_licenses/frontend/update-browserslist-db-1.2.3/LICENSE) |
| [vfile](https://github.com/vfile/vfile) | 6.0.3 | MIT | [notices](third_party_licenses/frontend/vfile-6.0.3/license) |
| [vfile-location](https://github.com/vfile/vfile-location) | 5.0.3 | MIT | [notices](third_party_licenses/frontend/vfile-location-5.0.3/license) |
| [vfile-message](https://github.com/vfile/vfile-message) | 4.0.3 | MIT | [notices](third_party_licenses/frontend/vfile-message-4.0.3/license) |
| [vite](https://vite.dev) | 7.3.6 | MIT | [notices](third_party_licenses/frontend/vite-7.3.6/LICENSE.md) |
| [web-namespaces](https://github.com/wooorm/web-namespaces) | 2.0.1 | MIT | [notices](third_party_licenses/frontend/web-namespaces-2.0.1/license) |
| [yallist](https://github.com/isaacs/yallist) | 3.1.1 | ISC | [notices](third_party_licenses/frontend/yallist-3.1.1/LICENSE) |
| [zwitch](https://github.com/wooorm/zwitch) | 2.0.4 | MIT | [notices](third_party_licenses/frontend/zwitch-2.0.4/license) |
