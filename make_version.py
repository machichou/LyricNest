"""Writes assets/version.txt, what Windows shows under the file's Properties; build.bat runs it so version and author live only in lyricstool/__init__.py."""
from lyricstool import __author__, __version__

nums = tuple(int(n) for n in __version__.split(".")) + (0,)
with open("assets/version.txt", "w", encoding="utf-8") as fh:
    fh.write(f"""VSVersionInfo(
  ffi=FixedFileInfo(filevers={nums}, prodvers={nums}, mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('040404B0', [
      StringStruct('CompanyName', '{__author__}'),
      StringStruct('FileDescription', 'LyricNest'),
      StringStruct('FileVersion', '{__version__}'),
      StringStruct('InternalName', 'LyricNest'),
      StringStruct('LegalCopyright', '© 2026 {__author__}'),
      StringStruct('OriginalFilename', 'LyricNest.exe'),
      StringStruct('ProductName', 'LyricNest'),
      StringStruct('ProductVersion', '{__version__}')])]),
    VarFileInfo([VarStruct('Translation', [0x0404, 1200])])
  ]
)
""")
