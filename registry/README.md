# Assembly package catalog

This catalog lives in the existing [cdmbot repository](https://github.com/stalcker2288969/cdmbot). Each package has a directory under `registry/packages/` with a `package.json` metadata card.

```text
registry/
  packages/
    byte-utils/package.json
    word-utils/package.json
    your-library/package.json
```

## Register a library

Create the library with `cdmbot create your-library --library --target cdm8`. Implement its exported assembly routines and fill in its `cdm-package.json`. Commit the library and publish a release tag in its source repository.

From a local clone of cdmbot, register that source release with the following command. Replace the example source URL with the actual library URL.

```sh
cdmbot pub publish ../your-library --git https://github.com/your-account/your-library.git --ref v0.1.0 --registry ./registry
```

This creates `registry/packages/your-library/package.json`. Publish the card in the cdmbot repository's default branch or submit a pull request. Git-hosted catalogs are cached for five minutes by clients.

## Metadata card

```json
{
  "name": "your-library",
  "version": "0.1.0",
  "description": "What the library does",
  "publisher": {
    "name": "Your name",
    "description": "About the maintainer",
    "url": "https://github.com/your-account"
  },
  "targets": ["cdm8"],
  "source": {
    "git": "https://github.com/your-account/your-library.git",
    "ref": "v0.1.0"
  }
}
```

The folder name and `name` must agree with the library's `cdm-package.json`. `source.subdir` optionally selects a library folder within a larger source repository. `publisher` also accepts a plain string. The starter cards use built-in sources shipped with cdmbot.

## Use a library

Consumers can run `cdmbot pub add your-library` or write `# @include "@your-library/lib.asm"` in their assembly source and build it. The first build installs the named library. Subsequent builds use the exact locked commit. `cdmbot pub update` explicitly resolves the current catalog releases.

Applications record dependencies in `cdm-packages.json`. Reusable libraries record dependencies in `cdm-package.json`. Both use `cdm-packages.lock.json` for exact versions and `.cdm/packages/` for installed files.
