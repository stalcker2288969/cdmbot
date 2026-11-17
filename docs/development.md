# cdmbot as a development tool

cdmbot 0.2.3 provides project creation, builds, execution, analysis and source packages alongside the existing YAML test runner. CocoVS integrates this CLI through a versioned JSON capability API. Existing `cdmbot program.asm tests.yaml` and `cdmbot project project.yaml` commands remain supported.

## Install from CocoVS

Run **CocoVS: Install or Update cdmbot**. CocoVS uses a working Python/toolchain and creates a private virtual environment in its extension storage. The cdmbot wheel is bundled in the VSIX. pip installs its dependencies when needed. Installation creates a stable `cdmbot` launcher and adds its directory to the current user's PATH. Open a new VS Code terminal to use it. Restart other terminal applications to pick up the persisted change. Installation paths appear in **Output → CocoVS**. Separate uv/pipx installations keep their own environments.

The private installation includes cdmbot's managed-Java helper. CdM-16 can reuse an available JDK or prepare one on first use; that first preparation may require a separate download.

The Testing view can discover this environment. `cocovs.cdmbotPythonPath` still selects an explicit installation. Development commands require API 1; older test-only versions continue to work for their existing tests. When a compatible cdmbot is available, `cocovs.developmentBackend: "auto"` uses it for builds. Set `"native"` to retain the original CoCAS build path. The existing debugger, RAM view, source maps, file profiles and project manifests remain in use.

## CLI workflow

After installing from CocoVS, open a new terminal and check `cdmbot --version`. From a cdmbot source checkout, `install.cmd`, `install.ps1`, or `install.sh` provides the standalone user command. A normal Python installation can use `python -m pip install .`.

```sh
cdmbot create my-app --target cdm8 --architecture vonNeumann
cd my-app
cdmbot analyze
cdmbot build
cdmbot run --max-steps 10000
cdmbot test
```

The application template contains `cdm-project.json`, `src/main.asm`, a smoke suite and `project.yaml`. **CocoVS: Create or Initialize Project** uses the same baseline template and processor startup instructions. Initialization preserves existing entry files and documentation. Smoke assertions are generated for the new executable template. `cdmbot create` and its `init` alias preserve existing entry files, manifests and documentation.

`cdmbot create my-lib --library --target cdm8` creates `cdm-package.json`, `lib.asm`, and documentation. `cdm-project.json` describes an executable's entry and execution settings. `cdm-package.json` describes a reusable library's identity, processors, public files and dependencies.

**CocoVS: Create or Initialize Project** selects an existing folder or a parent folder for a new project, then asks for application/library type, processor and memory architecture. Its former command IDs route to this same flow and are hidden from the Command Palette. Both CocoVS and cdmbot use the shared application and library templates. CocoVS creation works before installing cdmbot.

Standalone files need no project:

```sh
cdmbot analyze path/to/demo.cdm16.asm
cdmbot run path/to/demo.asm --target cdm16 --architecture vonNeumann
```

CLI standalone output is local to the source's `build/`; project output uses the project root's `build/`. Processor selection comes from the manifest, filename or explicit arguments. CocoVS passes its remembered file profile to development actions. `analyze` reports per-file lines, code, comments and instruction-line counts, plus assembler syntax diagnostics. `--no-syntax` requests statistics alone. These are source statistics, not runtime or branch coverage; `cdmbot test` and the existing project command provide test coverage.

Each new command supports `--json`. `cdmbot capabilities --json` advertises API version and supported commands for integrations. Analysis with syntax errors and execution that fails to halt return a nonzero status.

## Consume a package

The default community catalog is the `registry/` directory in `https://github.com/stalcker2288969/cdmbot.git`. Each library has its own `packages/<name>/package.json` card. The catalog is cached for five minutes. A bundled starter snapshot provides `byte-utils` for CdM-8 and `word-utils` for CdM-16 when the default catalog is unavailable. Search reports when a fallback snapshot is being used.

```sh
cdmbot pub search
cdmbot pub add byte-utils
cdmbot pub list
```

Writing a package include is sufficient for the first build or run. cdmbot discovers the package by name in the catalog, installs it and creates the dependency and lock files automatically. Subsequent builds reuse the lock and work offline with installed packages. Live syntax analysis leaves package installation to an explicit build, run, test or restore action.

Import a source file and declare its exported symbols:

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

Use **CocoVS: Browse Assembly Packages** or **Add Assembly Package** to search names, descriptions and publishers in one picker. Restore, Update, and Remove commands are also available in the Command Palette. Remove the corresponding source include when removing a dependency, since a later build discovers it again.

Once a library has a catalog card, consumers use its name:

```sh
cdmbot pub add my-lib
```

For direct Git sources, use **CocoVS: Add Assembly Package from Git** or:

```sh
cdmbot pub add my-lib --git https://example.org/team/my-lib.git --ref v1.0.0
cdmbot pub get
cdmbot pub update
cdmbot pub remove my-lib
```

Replace the URL and ref with a real repository and tag/branch. Local Git repository paths are supported for development. `--subdir path` selects a package inside a larger Git repository. Git must be installed for Git-backed packages. No package installation scripts are executed.

Application settings and dependencies are recorded together in `cdm-project.json`. Exact Git commits and file SHA-256 hashes are recorded in `cdm-packages.lock.json`, and installed source files in `.cdm/packages/`. Commit the project manifest and lock file and ignore `.cdm/`. An existing `cdm-packages.json` is merged into the project manifest and removed after a successful restore, update or package add/remove. The migration preserves the current lock and unrelated project settings. Conflicting declarations require resolving the dependency in the two manifests. A failed restore preserves both files. Standalone files without a project use `cdm-packages.json`, and library projects keep dependencies in `cdm-package.json`. `pub get` restores the lock without upgrading. `pub update` resolves current catalog entries for named dependencies and newer commits for direct Git refs. Package validation completes before replacing installed sources. Modified sources require restoring a clean cache.

To select a new release tag, re-add that dependency with its new `--ref`; unrelated package locks are retained. Use `--target cdm16` when managing packages for a plain standalone `.asm` file whose processor cannot be inferred from its name.

Package commands support standalone sources and multi-file projects. Add `--path path/to/file.asm` or `--path path/to/project` to package commands when not running from a project folder. Includes use the package root associated with that source/project. Both new builds and legacy YAML tests can link these imports.

## Assembly compatibility

`languageVersion` identifies the assembly compatibility profile shared by CocoVS and cdmbot. Profile `1.0` supports CoCAS 0.2.3 with the current template and package integration. This is the toolchain's dialect version. Processor selection uses `target` or a library's `targets`, and package release versions use their own `version` field.

```json
{
  "entry": "src/main.asm",
  "target": "cdm16",
  "architecture": "vonNeumann",
  "languageVersion": "1.0",
  "dependencies": {
    "memoryze16": "*"
  }
}
```

Applications, standalone dependency manifests and libraries can declare `languageVersion`. Existing manifests without it use profile `1.0`. This release supports `1.0` and rejects unsupported project profiles before building. Direct and transitive dependencies must match the project's profile. Restore, cached builds and the native CocoVS backend check compatibility. Language compatibility is independent of the target and the exact package revision pinned in the lock file.

## Create and publish a library

```sh
cdmbot create my-lib --library --target cdm8
```

Its `cdm-package.json` declares the package name, version, supported processors and exported file set:

```json
{
  "name": "my-lib",
  "languageVersion": "1.0",
  "version": "0.1.0",
  "description": "Small arithmetic helpers",
  "publisher": "Your name",
  "targets": ["cdm8"],
  "files": ["lib.asm", "README.md"],
  "dependencies": {
    "byte-utils": "*"
  }
}
```

Document inputs, outputs, changed registers/flags, stack behavior and processor compatibility for each routine. Names follow lowercase `my-lib` syntax. Only explicitly listed assembly/documentation/JSON files are installed, not the Git checkout. Paths escaping the package, symlinks, oversized payloads and unsupported targets are rejected.

Inside a library folder, `cdmbot pub add byte-utils` writes its dependency into `cdm-package.json`. A `"*"` dependency requests the current catalog release. Direct Git source objects also remain supported. Transitive dependencies are locked with the application. A project uses one source/ref per package name. Conflicting sources and cycles are rejected. Version tags identify releases, and lock files retain exact commits.

Push the library repository/tag, then register that release in the local cdmbot catalog:

```sh
cdmbot pub publish ./my-lib --git https://example.org/team/my-lib.git --ref v0.1.0 --registry ../cdmbot/registry
```

The command validates the referenced Git package and writes `registry/packages/my-lib/package.json`. Publish that change in the cdmbot repository's `main` branch or submit a pull request. The catalog reader uses the default branch. A Git repository can also contain the library source under a subdirectory selected with `--subdir`.

## Registry format

A catalog repository has one folder per library:

```text
registry/
  README.md
  packages/
    my-lib/
      package.json
    another-lib/
      package.json
```

An example `package.json` card follows. Replace the example URL with the library repository:

```json
{
  "name": "my-lib",
  "languageVersion": "1.0",
  "version": "0.1.0",
  "description": "Small arithmetic helpers",
  "publisher": "Your name",
  "targets": ["cdm8"],
  "source": {
    "git": "https://example.org/team/my-lib.git",
    "ref": "v0.1.0"
  }
}
```

Use `--registry` with a local catalog directory or an HTTPS Git repository URL ending in `.git`, `CDMBOT_REGISTRY` in the CLI environment, or `cocovs.packageRegistry` in the extension. A standalone catalog repository can put `packages/` at its root. Legacy JSON indexes served over HTTPS or stored locally are also accepted. The default uses the existing cdmbot repository. Catalog entries become available to other users after publication on GitHub.

## Compatibility

- Existing project and suite formats, classic includes, legacy CLI commands and native CoCAS builds remain supported.
- Source analysis does not replace the Testing view's assertions or coverage reports.
- CdM-8 and CdM-16 local execution use cdmbot's existing CPU backends; extended targets can be assembled but require their matching execution/debug tools.
- Custom processor runners continue to use CocoVS's existing custom build path.

## Structure templates

CoCAS templates expose `Name._` as the byte size and `Name.field` as a field offset. cdmbot 0.2.2 resolves these constants before instruction and storage encoding, including negative frame allocations such as `addsp -Name._` and static reservations such as `instance: ds Name._`. Each module defines its own templates. CdM-16 word fields and stack allocations use even alignment.
