# Installing TDLib

`tdpy` needs the `tdjson` shared library at runtime. There are no official
Python wheels, so it must be installed separately.

## Where tdpy looks

In order, stopping at the first library that loads:

1. The `library_path` argument, if you pass one. **No fallback is tried after
   an explicit path fails** — a wrong explicit choice should fail loudly rather
   than quietly load a different build.
2. `$TDJSON_PATH` — either the library file itself or a directory containing it.
3. An installed `tdjson` Python distribution, if you have one.
4. `ctypes.util.find_library("tdjson")`.
5. The bare filename, handed to the OS loader (`PATH`, `LD_LIBRARY_PATH`,
   `DYLD_LIBRARY_PATH`).
6. Well-known system directories.

If every candidate fails, the raised `TDJsonNotFoundError` lists each one with
the reason it failed.

## Per platform

**macOS**

```bash
brew install tdlib
```

**Linux** — build from [the official instructions](https://tdlib.github.io/td/build.html), then:

```bash
sudo ldconfig
# or, without installing system-wide:
export TDJSON_PATH=/path/to/td/build/libtdjson.so
```

**Windows** — build with vcpkg or download a prebuilt `tdjson.dll`. The DLL
needs its OpenSSL and zlib siblings in the same folder; `tdpy` registers that
folder with `os.add_dll_directory` so they resolve.

```powershell
$env:TDJSON_PATH = "C:\td\bin\tdjson.dll"
```

## Verifying

```python
from tdpy._ffi import TDJson

td = TDJson()
print(td.library_path)
print(td.execute(b'{"@type":"getTextEntities","text":"@durov"}'))
```
