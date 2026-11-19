"""Build the separate managed Player from already acquired, pinned sources."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

from . import build_compat, overlay


def build(player, liblcf, dependencies, work, output, generator_instance=None):
    work.mkdir(parents=True, exist_ok=False)
    output.mkdir(parents=True, exist_ok=False)
    source = work/'source'
    shutil.copytree(player, source)
    shutil.copytree(liblcf, source/'lib/liblcf')
    build_compat.apply(source)
    manifest = overlay.apply(source)
    prefix = dependencies/'installed/x64-windows-static'
    args = ['cmake','-S',str(source),'-B',str(work/'build'),'-G','Visual Studio 17 2022','-A','x64',
            '-DCMAKE_TOOLCHAIN_FILE='+str(dependencies/'scripts/buildsystems/vcpkg.cmake'),
            '-DVCPKG_MANIFEST_MODE=OFF','-DVCPKG_TARGET_TRIPLET=x64-windows-static',
            '-DCMAKE_FIND_PACKAGE_PREFER_CONFIG=OFF','-DCMAKE_MSVC_RUNTIME_LIBRARY=MultiThreaded',
            '-DZLIB_LIBRARY_RELEASE='+str(prefix/'lib/zs.lib'),
            '-DZLIB_LIBRARY_DEBUG='+str(prefix/'debug/lib/zsd.lib'),
            '-DPIXMAN_LIBRARY_RELEASE='+str(prefix/'lib/pixman-1.lib'),
            '-DPIXMAN_LIBRARY_DEBUG='+str(prefix/'debug/lib/pixman-1.lib'),
            '-DPLAYER_BUILD_LIBLCF=ON','-DPLAYER_AUDIO_BACKEND=SDL2','-DPLAYER_WITH_NATIVE_MIDI=OFF',
            '-DPLAYER_AUDIO_RESAMPLER=OFF','-DPLAYER_WITH_FREETYPE=OFF','-DPLAYER_ENABLE_TESTS=OFF',
            '-DLIBLCF_ENABLE_TESTS=OFF','-DLIBLCF_ENABLE_TOOLS=OFF','-DLIBLCF_UPDATE_MIMEDB=OFF',
            *['-DPLAYER_WITH_'+name+'=OFF' for name in ('MPG123','LIBSNDFILE','OGGVORBIS','OPUS','WILDMIDI','FLUIDSYNTH','FLUIDLITE','XMP')]]
    if generator_instance:
        args.append('-DCMAKE_GENERATOR_INSTANCE='+str(generator_instance))
    manifest['configure'] = args
    manifest['compatibility_sha256'] = hashlib.sha256(Path(build_compat.__file__).read_bytes()).hexdigest()
    (output/'source-manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    subprocess.run(args,check=True)
    subprocess.run(['cmake','--build',str(work/'build'),'--config','Release','--parallel','2'],check=True)
    executable = output/'GameQaEasyRpg.exe'
    shutil.copyfile(work/'build/Release/GameQaEasyRpg.exe',executable)
    manifest['runtime_sha256'] = hashlib.sha256(executable.read_bytes()).hexdigest()
    licenses = output/'licenses'
    licenses.mkdir()
    shutil.copyfile(source/'COPYING',licenses/'EasyRPG-Player-GPL-3.0.txt')
    shutil.copyfile(source/'lib/liblcf/COPYING',licenses/'liblcf-MIT.txt')
    (licenses/'dependencies').mkdir()
    for name in ('sdl2', 'icu-easyrpg', 'expat', 'libpng', 'zlib', 'fmt', 'pixman'):
        destination = licenses/'dependencies'/name
        destination.mkdir()
        for filename in ('copyright', 'vcpkg.spdx.json'):
            shutil.copyfile(prefix/'share'/name/filename, destination/filename)
    shutil.make_archive(str(output/'corresponding-source'),'zip',root_dir=source)
    manifest['corresponding_source_sha256'] = hashlib.sha256((output/'corresponding-source.zip').read_bytes()).hexdigest()
    (output/'source-manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    return manifest


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('player','liblcf','dependencies','work','output'):
        parser.add_argument('--'+name,type=Path,required=True)
    parser.add_argument('--generator-instance',type=Path)
    args=parser.parse_args()
    print(json.dumps(build(**vars(args)),indent=2))


if __name__=='__main__':
    main()
