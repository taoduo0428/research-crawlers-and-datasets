"""Export only explicitly reviewed files; no runtime directories are traversed."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path, PurePosixPath
import time
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def release_files(root: Path = ROOT) -> list[tuple[str, bytes]]:
    entries = []
    for line in (root / 'release-files.txt').read_text(encoding='utf-8').splitlines():
        name = line.strip()
        if not name or name.startswith('#'):
            continue
        relative = PurePosixPath(name)
        if relative.is_absolute() or '..' in relative.parts or '\\' in name or ':' in name:
            raise ValueError('Invalid release path: ' + name)
        source = root / relative
        root_resolved = root.resolve()
        source_resolved = source.resolve()
        if not source_resolved.is_relative_to(root_resolved):
            raise ValueError('Symlink/external release path: ' + name)
        # Check only the path components from the project root to the file.
        # macOS temporary directories can themselves be symlinks outside the
        # project; parent components above root are intentionally irrelevant.
        relative_parts = PurePosixPath(name).parts
        current = root
        for part in relative_parts:
            current = current / part
            if current.is_symlink():
                raise ValueError('Symlink/external release path: ' + name)
        if not source.is_file():
            raise ValueError('Missing release file: ' + name)
        data = source.read_bytes()
        data.decode('utf-8-sig')  # Source-only distribution, reject binary/corrupt text.
        if relative.suffix in {'.sh', '.command'}:
            data = data.replace(b'\r\n', b'\n')
        entries.append((name, data))
    if len({name for name, _ in entries}) != len(entries):
        raise ValueError('Duplicate release entries')
    return entries


def write_archive(entries: list[tuple[str, bytes]], archive: Path) -> None:
    with zipfile.ZipFile(archive, 'x', compression=zipfile.ZIP_DEFLATED) as bundle:
        for name, data in entries:
            entry = zipfile.ZipInfo('facebook/' + name)
            entry.create_system = 3
            mode = 0o100755 if PurePosixPath(name).suffix in {'.sh', '.command'} else 0o100644
            entry.external_attr = mode << 16
            entry.compress_type = zipfile.ZIP_DEFLATED
            bundle.writestr(entry, data)
        bundle.writestr('facebook/SHA256SUMS.txt', ''.join(hashlib.sha256(data).hexdigest() + '  ' + name + '\n' for name, data in entries))


def main() -> None:
    entries = release_files()
    destination = ROOT / 'dist'
    destination.mkdir(exist_ok=True)
    archive = destination / ('facebook-source-' + time.strftime('%Y%m%d-%H%M%S') + '.zip')
    write_archive(entries, archive)
    print(json.dumps({'archive': str(archive), 'files': len(entries), 'bytes': archive.stat().st_size}, ensure_ascii=True))


if __name__ == '__main__':
    main()
