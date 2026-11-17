"""Versioned compatibility profiles for the CdM assembly dialect."""
from .model import TestError

LANGUAGE_VERSION = '1.0'


def language_version(manifest):
    version = manifest.get('languageVersion', LANGUAGE_VERSION)
    if version != LANGUAGE_VERSION:
        raise TestError(f'Unsupported languageVersion {version!r}; this cdmbot supports {LANGUAGE_VERSION}. Update the toolchain or choose a supported profile')
    return version


def check_language(package, required):
    version = package.get('languageVersion', LANGUAGE_VERSION)
    if version != required:
        raise TestError(f'{package.get("name", "Package")}: languageVersion {version!r} is incompatible with project {required!r}')
