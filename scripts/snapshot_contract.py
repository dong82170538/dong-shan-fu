"""Scoped source manifests shared by verification and local snapshot creation."""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCE_ROOTS = ('commerce_core', 'desktop', 'docs', 'extension', 'fixtures', 'scripts', 'tests', 'web')
SOURCE_FILES = ('.gitignore', 'README.md', 'pyproject.toml', 'requirements-validation.txt')
FORBIDDEN = {'.git', '.aws', '.codex', '.agents', 'node_modules', '.venv', 'venv', '__pycache__', 'approved-runtime'}
EXTENSIONS = {'.py', '.cjs', '.js', '.html', '.css', '.json', '.md', '.txt', '.toml', '.tsv'}


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def digest(data):
    return hashlib.sha256(data).hexdigest()


def entries(mapping):
    return {name: {'bytes': len(data), 'sha256': digest(data)} for name, data in sorted(mapping.items())}


def manifest_sha(manifest):
    return digest(canonical(manifest).encode('utf-8'))


def collect_sources(root=ROOT):
    sources = {}
    candidates = [root / name for name in SOURCE_FILES]
    for directory in SOURCE_ROOTS:
        candidates.extend(sorted((root / directory).rglob('*')))
    for file in candidates:
        relative = file.relative_to(root)
        if any(part in FORBIDDEN for part in relative.parts):
            continue
        if file.is_symlink():
            raise RuntimeError('Symlink is not allowed in source snapshot')
        if file.is_file() and (relative.as_posix() in SOURCE_FILES or file.suffix.lower() in EXTENSIONS):
            sources[relative.as_posix()] = file.read_bytes()
    return sources


def code_manifest(sources):
    # Prose may be added after verification. Runtime/test JSON and reference data
    # remain covered, including the route inventory read by the actual service.
    return entries({name: data for name, data in sources.items()
                    if not name.endswith('.md') and name != '.gitignore'})
