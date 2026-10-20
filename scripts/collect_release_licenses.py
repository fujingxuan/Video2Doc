"""Collect installed release-dependency notices without copying local configuration.

Run with backend/.venv/Scripts/python.exe from any working directory.  Installed
Python distributions and the frontend's installed production dependency graph are
the inputs.  Existing upstream runtime notices are reused on repeat offline runs;
--refresh-runtime downloads them again from their versioned official sources.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata as metadata
import json
import re
import shutil
import sqlite3
import ssl
import sys
import tomllib
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import pyexpat

from packaging.requirements import Requirement


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "third_party_licenses"


def safe_name(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9._-]+", "_", value)


def normalize_source(value: str, fallback: str) -> str:
    """Turn public package-repository shorthand into a browser-usable URL."""
    value = value.strip()
    if value.startswith("github:"):
        value = "https://github.com/" + value.removeprefix("github:")
    elif re.fullmatch(r"[\w.-]+/[\w.-]+(?:#.*)?", value):
        value = "https://github.com/" + value
    value = re.sub(r"^git\+", "", value)
    value = re.sub(r"^git@github\.com:", "https://github.com/", value)
    value = re.sub(r"^ssh://git@github\.com/", "https://github.com/", value)
    value = re.sub(r"^git://", "https://", value)
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return fallback
    if parsed.username is not None or parsed.password is not None:
        return fallback
    path = parsed.path.removesuffix(".git")
    return urlunsplit((parsed.scheme, parsed.netloc, path, parsed.query, parsed.fragment))


def is_notice(path: Path) -> bool:
    return (path.name.lower().startswith(("license", "licence", "copying", "notice"))
            or any(part.lower() in {"licenses", "licences"} for part in path.parts))


def copy_notice(source: Path, destination: Path) -> str:
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)
    return destination.relative_to(ROOT).as_posix()


def python_distributions() -> list[dict]:
    project = tomllib.loads((ROOT / "backend/pyproject.toml").read_text("utf-8"))
    pending = [(item, "runtime") for item in project["project"]["dependencies"]]
    pending += [("pyinstaller", "build tool / bootloader exception")]
    if metadata.packages_distributions().get("pygments"):
        pending += [("pygments", "PyInstaller Mako hook dependency")]
    if metadata.packages_distributions().get("_pytest"):
        pending += [("pytest", "optional _pytest modules retained by PyInstaller")]
    visited: dict[str, set[str]] = {}
    entries = []
    while pending:
        requirement_text, scope = pending.pop(0)
        requirement = Requirement(requirement_text)
        name = requirement.name.lower().replace("_", "-")
        extras = set(requirement.extras)
        previous_extras = visited.get(name)
        if previous_extras is not None and extras <= previous_extras:
            continue
        visited.setdefault(name, set()).update(extras)
        dist = metadata.distribution(requirement.name)
        if previous_extras is None:
            files = []
            for installed_file in dist.files or []:
                relative = Path(str(installed_file))
                if ".." in relative.parts or not is_notice(relative):
                    continue
                source = Path(dist.locate_file(installed_file))
                if source.is_file() and source.suffix.lower() not in {".py", ".pyc"}:
                    files.append(copy_notice(source, OUTPUT / "python" /
                                             safe_name(f"{name}-{dist.version}") / relative))
            urls = dist.metadata.get_all("Project-URL", [])
            fallback = f"https://pypi.org/project/{name}/{dist.version}/"
            homepage = dist.metadata.get("Home-page") or next(
                (url.split(",", 1)[-1].strip() for url in urls),
                fallback,
            )
            homepage = normalize_source(homepage, fallback)
            license_name = dist.metadata.get("License-Expression")
            if not license_name:
                license_name = " ".join((dist.metadata.get("License") or "See license files").split())
            entries.append({"ecosystem": "Python", "name": dist.metadata["Name"],
                            "version": dist.version, "scope": scope,
                            "license": license_name[:160], "source": homepage,
                            "files": sorted(files)})
        for dependency_text in dist.requires or []:
            dependency = Requirement(dependency_text)
            if dependency.marker is None or any(
                dependency.marker.evaluate({"extra": extra}) for extra in extras | {""}
            ):
                pending.append((str(dependency), scope))
    return entries


def resolve_node_package(name: str, from_directory: Path) -> Path | None:
    for base in [from_directory, *from_directory.parents]:
        candidate = base / "node_modules" / name / "package.json"
        if candidate.is_file():
            return candidate.parent.resolve()
        if base == ROOT.parent:
            break
    return None


def frontend_packages() -> list[dict]:
    frontend = ROOT / "frontend"
    root_package = json.loads((frontend / "package.json").read_text("utf-8"))
    pending = [(name, frontend, False) for name in root_package["dependencies"]]
    visited = set()
    entries = []
    while pending:
        name, parent, optional = pending.pop(0)
        package = resolve_node_package(name, parent)
        if package is None:
            if optional:
                continue
            raise RuntimeError(f"Frontend dependency is not installed: {name}")
        if package in visited:
            continue
        visited.add(package)
        info = json.loads((package / "package.json").read_text("utf-8"))
        files = []
        # Package-level notices also cover vendor notices shipped under licenses/.
        for source in package.rglob("*"):
            relative = source.relative_to(package)
            if "node_modules" in relative.parts:
                continue
            if source.is_file() and is_notice(source):
                files.append(copy_notice(source, OUTPUT / "frontend" /
                                         safe_name(f"{info['name']}-{info['version']}") / relative))
        license_origin = "installed package"
        if not files and name.startswith(("@esbuild/", "@rollup/")):
            # Platform-only binary subpackages rely on the same-version tool's notice.
            parent_info = json.loads((parent / "package.json").read_text("utf-8"))
            if parent_info["version"] != info["version"]:
                raise RuntimeError(f"Cannot match platform-package notice: {name}")
            for source in parent.iterdir():
                if source.is_file() and is_notice(source):
                    files.append(copy_notice(source, OUTPUT / "frontend" /
                                             safe_name(f"{info['name']}-{info['version']}") /
                                             source.name))
            license_origin = f"same-version parent tool {parent_info['name']}"
        repository = info.get("repository", "")
        if isinstance(repository, dict):
            repository = repository.get("url", "")
        source_url = info.get("homepage") or repository or (
            f"https://www.npmjs.com/package/{info['name']}/v/{info['version']}"
        )
        source_url = normalize_source(
            source_url,
            f"https://www.npmjs.com/package/{info['name']}/v/{info['version']}",
        )
        entries.append({"ecosystem": "npm", "name": info["name"],
                        "version": info["version"],
                        "scope": "frontend production dependency graph (includes build tooling)",
                        "license": str(info.get("license", "See license files")),
                        "source": source_url, "files": sorted(files),
                        "license_text_origin": license_origin})
        optional_names = set(info.get("optionalDependencies", {}))
        pending.extend((child, package, child in optional_names)
                       for child in info.get("dependencies", {}))
        pending.extend((child, package, True) for child in optional_names)
    return entries


def upstream_notice(relative: str, url: str, refresh: bool) -> str:
    destination = OUTPUT / "runtime" / relative
    if refresh or not destination.is_file():
        request = urllib.request.Request(url, headers={"User-Agent": "Video2Doc-license-collector"})
        with urllib.request.urlopen(request, timeout=60) as response:
            data = response.read()
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)
    return destination.relative_to(ROOT).as_posix()


def runtime_notices(refresh: bool) -> list[dict]:
    python_version = ".".join(map(str, sys.version_info[:3]))
    python_files = [copy_notice(Path(sys.base_prefix) / "LICENSE.txt",
                                OUTPUT / "runtime/Python-LICENSE.txt")]
    python_source = f"https://raw.githubusercontent.com/python/cpython/v{python_version}"
    python_files.append(upstream_notice("Python-THIRD-PARTY-LICENSES.rst",
                                        f"{python_source}/Doc/license.rst", refresh))
    openssl_version = ssl.OPENSSL_VERSION.split()[1]
    openssl_source = f"https://raw.githubusercontent.com/openssl/openssl/openssl-{openssl_version}"
    openssl_files = [upstream_notice("OpenSSL-LICENSE.txt", f"{openssl_source}/LICENSE.txt", refresh)]
    openssl_files.append(upstream_notice("OpenSSL-AUTHORS.txt", f"{openssl_source}/AUTHORS.md", refresh))
    ffi_file = upstream_notice("libffi-LICENSE.txt",
                               "https://raw.githubusercontent.com/libffi/libffi/v3.4.4/LICENSE", refresh)
    sqlite_file = upstream_notice("SQLite-PUBLIC-DOMAIN.html",
                                  "https://www.sqlite.org/copyright.html", refresh)
    expat_version = pyexpat.EXPAT_VERSION.removeprefix("expat_")
    expat_file = upstream_notice("Expat-COPYING.txt",
                                 "https://raw.githubusercontent.com/libexpat/libexpat/"
                                 f"R_{expat_version.replace('.', '_')}/expat/COPYING", refresh)
    lzma_file = upstream_notice("XZ-liblzma-COPYING.txt",
                                "https://raw.githubusercontent.com/tukaani-project/xz/v5.2.5/COPYING",
                                refresh)
    return [
        {"ecosystem": "Runtime", "name": "CPython and standard-library third parties",
         "version": python_version, "scope": "bundled runtime", "license": "PSF and component notices",
         "source": f"https://www.python.org/downloads/release/python-{python_version.replace('.', '')}/",
         "files": python_files},
        {"ecosystem": "Runtime", "name": "OpenSSL", "version": openssl_version,
         "scope": "bundled libcrypto/libssl", "license": "Apache-2.0",
         "source": f"https://github.com/openssl/openssl/tree/openssl-{openssl_version}",
         "files": openssl_files},
        {"ecosystem": "Runtime", "name": "libffi", "version": "see bundled runtime binary",
         "scope": "bundled ctypes native dependency", "license": "MIT",
         "source": "https://github.com/libffi/libffi", "files": [ffi_file]},
        {"ecosystem": "Runtime", "name": "SQLite", "version": sqlite3.sqlite_version,
         "scope": "bundled SQLite native runtime", "license": "Public domain",
         "source": "https://www.sqlite.org/copyright.html", "files": [sqlite_file]},
        {"ecosystem": "Runtime", "name": "Expat", "version": expat_version,
         "scope": "bundled XML parser", "license": "MIT",
         "source": f"https://github.com/libexpat/libexpat/tree/R_{expat_version.replace('.', '_')}",
         "files": [expat_file]},
        {"ecosystem": "Runtime", "name": "XZ / liblzma", "version": "not exposed by Python",
         "scope": "bundled lzma native dependency; upstream 5.2.5 licensing reference",
         "license": "Public domain (liblzma; see component notice)",
         "source": "https://github.com/tukaani-project/xz/tree/v5.2.5", "files": [lzma_file]},
    ]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--refresh-runtime", action="store_true")
    args = parser.parse_args()
    entries = python_distributions() + frontend_packages() + runtime_notices(args.refresh_runtime)
    entries.sort(key=lambda item: (item["ecosystem"], item["name"].lower(), item["version"]))
    missing = [f"{item['name']} {item['version']}" for item in entries if not item["files"]]
    if missing:
        raise RuntimeError("Missing license texts: " + ", ".join(missing))
    for item in entries:
        if urlsplit(item["source"]).scheme not in {"http", "https"}:
            raise RuntimeError(f"Invalid public source URL: {item['name']}")
        item["sha256"] = {file: hashlib.sha256((ROOT / file).read_bytes()).hexdigest()
                          for file in item["files"]}
    (OUTPUT / "manifest.json").write_text(json.dumps(entries, ensure_ascii=False, indent=2) + "\n",
                                           encoding="utf-8")
    lines = ["# Third-party notices", "",
             "Video2Doc's own code is available under the root MIT LICENSE. Third-party code remains",
             "under its original terms; the MIT license does not replace those terms.", "",
             "This inventory records installed backend runtime dependencies, the frontend production",
             "dependency graph (including its build tooling), Python native runtime notices, and",
             "PyInstaller's bootloader exception. It is intentionally broader than the files retained",
             "by PyInstaller or Vite. Development/test packages outside this graph are not redistributed.", "",
             "Some auxiliary `_pytest` modules are retained by PyInstaller; their notices are included.",
             "This does not mean the complete pytest development environment is bundled.", "",
             "Exact installed versions, copied notice paths and SHA-256 hashes are in",
             "[third_party_licenses/manifest.json](third_party_licenses/manifest.json).", "",
             "Regenerate using `backend\\.venv\\Scripts\\python.exe scripts\\collect_release_licenses.py`",
             "after installing the project dependencies. The first run downloads versioned Python and",
             "OpenSSL notices from their official repositories; subsequent runs reuse those files.", "",
             "## External programs and models", "",
             "The public Windows package does not include FFmpeg, ffprobe, yt-dlp, ASR/OCR weights or",
             "their separate runtimes. Install them from their official sources and follow their own",
             "licenses. FFmpeg licensing depends on the selected build. The official Windows yt-dlp",
             "PyInstaller executable includes GPLv3+ components; a custom `-BundleYtDlp` build requires",
             "the applicable license and corresponding-source materials. The build switch does not",
             "prepare or verify those materials.", "",
             "- FFmpeg: https://ffmpeg.org/legal.html",
             "- yt-dlp: https://github.com/yt-dlp/yt-dlp#licensing",
             "- PyInstaller: https://pyinstaller.org/en/stable/license.html", "",
             "Microsoft runtime components retain the Microsoft restrictions included in",
             "[Python-LICENSE.txt](third_party_licenses/runtime/Python-LICENSE.txt), including Windows-only",
             "redistribution and protection of those components. SQLite's public-domain dedication",
             "is archived separately. libffi's license is archived",
             "from the upstream project; its exact native library version is not advertised by Python.", "",
             "The unmodified certifi source is available from its versioned PyPI source distribution:",
             f"https://pypi.org/project/certifi/{metadata.version('certifi')}/#files . Its files remain",
             "under MPL-2.0; see the archived license. XZ/liblzma's historical public-domain licensing",
             "reference is retained; Python does not expose its exact native liblzma build version.", "",
             "## Installed component inventory", "",
             "| Component | Version | License metadata | License texts |",
             "| --- | --- | --- | --- |"]
    for item in entries:
        license_text = item["license"].replace("|", "\\|").replace("\n", " ")
        first_file = item["files"][0]
        lines.append(f"| [{item['name']}]({item['source']}) | {item['version']} | {license_text} | "
                     f"[notices]({first_file}) |")
    (ROOT / "THIRD_PARTY_NOTICES.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Collected {len(entries)} component records and "
          f"{sum(len(item['files']) for item in entries)} notice files.")


if __name__ == "__main__":
    main()
