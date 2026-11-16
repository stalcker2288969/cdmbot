# cdmbot as a development tool

cdmbot 0.2.0 adds project creation, builds, execution, analysis and source packages alongside the existing YAML test runner. CocoVS integrates this CLI through a versioned JSON capability API. Existing `cdmbot program.asm tests.yaml` and `cdmbot project project.yaml` commands remain supported.

## Install from CocoVS

Run **CocoVS: Install or Update cdmbot**. CocoVS uses a working Python/toolchain and creates a private virtual environment in its extension storage. The cdmbot wheel is bundled in the VSIX; pip installs its pinned toolchain dependencies when needed. This does not upgrade a separate uv/pipx/user Python installation. Installation output appears in **Output → CocoVS**. An interrupted or failed installation leaves the previous environment intact.

The Testing view can discover this environment. `cocovs.cdmbotPythonPath` still selects an explicit installation. Development commands require API 1; older test-only versions continue to work for their existing tests. When a compatible cdmbot is available, `cocovs.developmentBackend: "auto"` uses it for builds. Set `"native"` to retain the original CoCAS build path. The existing debugger, RAM view, source maps, file profiles and project manifests remain in use.

## CLI workflow

From a cdmbot source checkout, the existing `install.cmd`, `install.ps1`, or `install.sh` provides the standalone user command. A normal Python installation can use `python -m pip install .`. The extension's private Python can also run `-m cdmbot`; its location is printed during installation. A terminal command is not automatically added to PATH by the extension installer.

```sh
cdmbot create my-app --target cdm8 --architecture vonNeumann
cd my-app
cdmbot analyze
cdmbot build
cdmbot run --max-steps 10000
cdmbot test
```

The template contains `cdm-project.json`, `src/main.asm`, a smoke suite and `project.yaml`. Existing nonempty directories are not overwritten. `cdmbot create my-lib --library --target cdm8` instead creates a library manifest and an exported routine.

Standalone files need no project:

```sh
cdmbot analyze path/to/demo.cdm16.asm
cdmbot run path/to/demo.asm --target cdm16 --architecture vonNeumann
```

CLI standalone output is local to the source's `build/`; project output uses the project root's `build/`. Processor selection comes from the manifest, filename or explicit arguments. CocoVS passes its remembered file profile to development actions. `analyze` reports per-file lines, code, comments and instruction-line counts, plus assembler syntax diagnostics. `--no-syntax` requests statistics alone. These are source statistics, not runtime or branch coverage; `cdmbot test` and the existing project command provide test coverage.

Each new command supports `--json`. `cdmbot capabilities --json` advertises API version and supported commands for integrations. Analysis with syntax errors and execution that fails to halt return a nonzero status.

## Consume a package

The bundled starter catalog contains `byte-utils` for CdM-8 and `word-utils` for CdM-16. These require no registry server or Git download after cdmbot installation:

```sh
cdmbot pub search
cdmbot pub add byte-utils
cdmbot pub list
```

Import a source file explicitly and declare its exported symbols:

```asm
# @include "@byte-utils/lib.asm"
byte_increment: ext
asect 0
main:
    setsp 0xF0
    ldi r0, 41
    jsr byte_increment
    halt
end
```

Use **CocoVS: Browse Assembly Packages** or **Add Assembly Package** for the same workflow. Restore, Update, and Remove commands are also available in the Command Palette.

For your own Git-hosted library:

```sh
cdmbot pub add my-lib --git https://example.org/team/my-lib.git --ref v1.0.0
cdmbot pub get
cdmbot pub update
cdmbot pub remove my-lib
```

Replace the URL and ref with a real repository and tag/branch. Local Git repository paths are supported for development. `--subdir path` selects a package inside a larger Git repository. Git must be installed for Git-backed packages. No package installation scripts are executed.

Dependencies are recorded in `cdm-packages.json`, exact Git commits and file SHA-256 hashes in `cdm-packages.lock.json`, and installed source files in `.cdm/packages/`. Commit both manifests; ignore `.cdm/`. `pub get` restores the lock without upgrading. `pub update` explicitly resolves newer commits for the configured refs. A modified cache is rejected rather than silently used. Downloads and checks complete before replacing installed packages; an incompatible target or invalid package does not replace a working installation.

The same dependency files work with standalone sources and multi-file projects. Add `--path path/to/file.asm` or `--path path/to/project` to package commands when not running from a project folder. Includes use the package root associated with that source/project. Both new builds and legacy YAML tests can link these imports.

## Create and publish a library

```sh
cdmbot create my-lib --library --target cdm8
```

Its `cdm-package.json` declares the package name, version, supported processors and exported file set:

```json
{
  "name": "my-lib",
  "version": "0.1.0",
  "targets": ["cdm8"],
  "files": ["lib.asm", "README.md"],
  "dependencies": {
    "byte-utils": {"builtin": "byte-utils"}
  }
}
```

Document inputs, outputs, changed registers/flags, stack behavior and processor compatibility for each routine. Names follow lowercase `my-lib` syntax. Only explicitly listed assembly/documentation/JSON files are installed, not the Git checkout. Paths escaping the package, symlinks, oversized payloads and unsupported targets are rejected.

Transitive dependencies use the same source objects as direct dependencies and are locked with the application. A project uses one source/ref per package name; conflicting sources and cycles are rejected. This is a Git/ref-based manager, not a semantic-version constraint solver. For a release, use an immutable version tag and keep package names distinct from assembly symbol names.

Push the library repository/tag yourself, then register that release in a local clone of a community index:

```sh
cdmbot pub publish ./my-lib --git https://example.org/team/my-lib.git --ref v0.1.0 --registry ./community/registry.json
```

The command validates the referenced Git package and edits the local registry entry. Commit that change or submit it to the registry maintainers. It does not publish Git repositories or deploy an online registry on your behalf.

## Registry format

An index is a JSON file served over HTTPS or stored locally:

```json
{
  "version": 1,
  "packages": {
    "my-lib": {"git": "https://example.org/team/my-lib.git", "ref": "v0.1.0"}
  }
}
```

Use `--registry path-or-https-url` for search/add, `CDMBOT_REGISTRY` in the CLI environment, or `cocovs.packageRegistry` in the extension. An empty setting uses the bundled starter catalog. A hosted community catalog requires publishing its index; no external service is required for local repositories or the starter libraries.

## Compatibility

- Existing project and suite formats, classic includes, legacy CLI commands and native CoCAS builds remain supported.
- Source analysis does not replace the Testing view's assertions or coverage reports.
- CdM-8 and CdM-16 local execution use cdmbot's existing CPU backends; extended targets can be assembled but require their matching execution/debug tools.
- Custom processor runners continue to use CocoVS's existing custom build path.
