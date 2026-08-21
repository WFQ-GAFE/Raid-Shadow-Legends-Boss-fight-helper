# Third-party notices

## MinHook

This project includes MinHook v1.3.4 from <https://github.com/TsudaKageyu/minhook>, pinned at commit `c3fcafdc10146beb5919319d0683e44e3c30d537`.

MinHook is distributed under its BSD-style license. The complete license text is retained at `third_party/minhook/LICENSE.txt` and must accompany binary redistributions as required by that license.

## React user interface

The local user interface uses React 19.2.8, React DOM 19.2.8, Vite 8.2.1,
TypeScript 6.0.2, Lucide React 1.31.0, and Radix UI Dialog 1.1.23. Their
package metadata and license files are retained under `ui/node_modules` after
installation and in `ui/package-lock.json` for reproducible installation.

## Classic interface fallback

The classic fallback interface uses ttkbootstrap 2.2.0 and Pillow 12.3.0,
vendored under `third_party/python`. Their package metadata and license files
are retained alongside the installed packages.

## Native desktop shell

The Windows desktop shell uses pywebview 6.2.1 with pythonnet 3.1.0,
clr-loader 0.3.1, Bottle 0.13.4, proxy_tools 0.1.0, cffi 2.1.1,
pycparser 3.0, and typing_extensions 4.16.0. Runtime packages and their
license metadata are retained under `third_party/python`.

The distributable application is assembled with PyInstaller 6.22.0 and its
build dependencies, retained under `third_party/build_tools` with their
package metadata and license files.
