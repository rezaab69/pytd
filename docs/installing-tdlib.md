# Installing TDLib

`tdpy` needs the `tdjson` shared library at runtime. There are no official
Python wheels, so it must be installed separately.

## Where tdpy looks

In order, stopping at the first library that loads:

1. The `library_path` argument, if you pass one.
2. `$TDJSON_PATH` — either the library file itself or a directory containing it.
3. An installed `tdjson` Python distribution, if you have one.
4. `ctypes.util.find_library("tdjson")`.
5. The bare filename, handed to the OS loader (`PATH`, `LD_LIBRARY_PATH`,
   `DYLD_LIBRARY_PATH`).
6. Well-known system directories.

**Naming a library — by argument or by `TDJSON_PATH` — suppresses every other
candidate.** Only an unguided search falls through the list. Quietly loading a
different build than the one you named is worse than failing: the build under
test would not be the build that loaded, and nothing would say so.

If every candidate fails, the raised `TDJsonNotFoundError` lists each one with
the reason it failed, which is usually enough to diagnose it:

```
Could not load TDLib's tdjson shared library.

Looked for libtdjson.so, libtdjson.so.1 in this order:
  - /opt/td/libtdjson.so
      libc++.so.1: cannot open shared object file: No such file or directory
```

That one means the library was built against LLVM's libc++ rather than
libstdc++. On Debian and Ubuntu:

```bash
sudo apt-get install libc++1 libc++abi1
```

Note that Ubuntu's `libunwind8` provides `libunwind.so.8`, which is a
*different* library from the `libunwind.so.1` an LLVM-built TDLib wants; the
`libc++abi1` package pulls in the right one.

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
