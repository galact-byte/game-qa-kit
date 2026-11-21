"""Assemble a complete desktop release after verifying every native artifact."""
import argparse
import hashlib
from pathlib import Path
import re
import shutil


def assemble(downloads, output, version):
    if not re.fullmatch(r'v\d+\.\d+\.\d+(?:-[A-Za-z0-9.-]+)?', version):
        raise ValueError('Invalid release version')
    downloads, output = Path(downloads), Path(output)
    expected = {
        f'game-qa-kit-{version}-{target}'
        for target in ('windows-x64.zip', 'macos-x64.tar.gz', 'macos-arm64.tar.gz',
                       'linux-x64.tar.gz', 'linux-arm64.tar.gz')
    }
    packages = {}
    for manifest in downloads.glob('*/SHA256SUMS.txt'):
        for line in manifest.read_text(encoding='utf-8').splitlines():
            match = re.fullmatch(r'([a-f0-9]{64})  (.+)', line)
            if not match:
                raise ValueError('Invalid checksum entry in ' + str(manifest))
            digest, name = match.groups()
            if name not in expected or name in packages:
                raise ValueError('Unexpected or duplicate release asset: ' + name)
            package = manifest.parent / name
            if not package.is_file() or package.is_symlink():
                raise ValueError('Missing native package: ' + name)
            with package.open('rb') as stream:
                if hashlib.file_digest(stream, 'sha256').hexdigest() != digest:
                    raise ValueError('Native package checksum mismatch: ' + name)
            packages[name] = (package, digest)
    if packages.keys() != expected:
        raise ValueError('Incomplete native release: ' + ', '.join(sorted(expected - packages.keys())))
    output.mkdir(parents=True, exist_ok=False)
    for name, (package, _) in packages.items():
        shutil.copyfile(package, output / name)
    (output / 'SHA256SUMS.txt').write_text(
        ''.join(digest + '  ' + name + '\n' for name, (_, digest) in sorted(packages.items())),
        encoding='utf-8',
    )


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--downloads', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--version', required=True)
    args = parser.parse_args()
    assemble(args.downloads, args.output, args.version)
