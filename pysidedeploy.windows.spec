[app]
title = Digifly Workstation
project_dir = .
input_file = main.py
exec_directory = .
project_file =
icon = src/digifly_app/assets/digifly_icon.ico

[python]
python_path = .venv/Scripts/python.exe
packages = Nuitka==2.7.11

[qt]
qml_files =
excluded_qml_plugins =
modules = Core,Gui,OpenGL,OpenGLWidgets,Widgets
plugins = iconengines,imageformats,platforms,styles

[android]
wheel_pyside =
wheel_shiboken =
plugins =

[nuitka]
macos.permissions =
mode = standalone
extra_args = --quiet --assume-yes-for-downloads --disable-cache=ccache --lto=no --jobs=4 --windows-console-mode=disable --include-data-file=src/digifly_app/assets/digifly_icon.png=digifly_app/assets/digifly_icon.png --include-data-dir=src/digifly_app/workers=digifly_app/workers --include-data-dir=mechanisms=mechanisms --include-data-dir=presets=presets --include-data-dir=schemas=schemas --include-data-file=scripts/build_arbor_gap_catalogue.py=scripts/build_arbor_gap_catalogue.py --include-data-file=scripts/build_neuron_gap_mechanisms.py=scripts/build_neuron_gap_mechanisms.py --include-data-dir=docs=docs --include-data-file=README.md=README.md --include-data-file=LICENSE=LICENSE

[buildozer]
mode = debug
recipe_dir =
jars_dir =
local_libs =
arch =
