# Third-party notices

## MinHook

This project includes MinHook v1.3.4 from <https://github.com/TsudaKageyu/minhook>, pinned at commit `c3fcafdc10146beb5919319d0683e44e3c30d537`.

MinHook is distributed under its BSD-style license. The complete license text is retained at `third_party/minhook/LICENSE.txt` and must accompany binary redistributions as required by that license.

## React user interface

The local user interface uses React 19.2.8, React DOM 19.2.8, Vite 8.2.1,
TypeScript 6.0.2, Lucide React 1.31.0, and Radix UI Dialog 1.1.23. Their
package metadata and license files are retained under `ui/node_modules` after
installation and in `ui/package-lock.json` for reproducible installation.

## Legacy development interface

The retained legacy source interface uses ttkbootstrap 2.2.0. Pillow 12.3.0
is also used by the current native visual-asset cache. Their package metadata
and license files are retained under `third_party/python`. The desktop release
does not launch or collect the legacy interface.

## Native desktop shell

The Windows desktop shell uses pywebview 6.2.1 with pythonnet 3.1.0,
clr-loader 0.3.1, Bottle 0.13.4, proxy_tools 0.1.0, cffi 2.1.1,
pycparser 3.0, and typing_extensions 4.16.0. Runtime packages and their
license metadata are retained under `third_party/python`.

Native game portrait and icon extraction in packaged releases uses UnityPy
1.25.3 and its declared decoder dependencies. UnityPy is MIT-licensed; the
licenses supplied with those build-environment packages apply to their bundled
components.

The distributable application is assembled with PyInstaller 6.22.0 and its
build dependencies, retained under `third_party/build_tools` with their
package metadata and license files.
