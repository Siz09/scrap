# PyInstaller spec: one-file DeviceScout executable (double-click -> browser opens).
#   pip install -e ".[dev]" && (cd web && npm ci && npm run build)
#   pyinstaller packaging/devicescout.spec
# The executable scrapes in "static" mode (HTTP with browser TLS fingerprints). Browser-based
# modes (dynamic/stealth) need Playwright browsers and are left to the pip install.
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

root = SPECPATH + "/.."
hidden = (
    collect_submodules("devicescout")
    + collect_submodules("uvicorn")
    + ["scrapling.fetchers.requests", "scrapling.engines.static", "scrapling.parser"]
)
datas = [
    (root + "/devicescout/web/dist", "devicescout/web/dist"),
    (root + "/devicescout/data", "devicescout/data"),
] + [
    f
    for pkg in ("scrapling", "curl_cffi", "browserforge", "apify_fingerprint_datapoints")
    for f in collect_data_files(pkg)   # browser-fingerprint data used for realistic request headers
]

a = Analysis(
    [root + "/devicescout/app.py"],
    pathex=[root],
    hiddenimports=hidden,
    datas=datas,
    # Scrapling's HTTP fetcher imports playwright types, so the Python package stays in; the
    # browser binaries are never bundled.
    excludes=["camoufox", "tkinter", "pytest"],
)
# Drop the Node.js browser drivers (~45 MB each): only browser modes use them.
def _keep(entry):
    path = entry[0].replace("\\", "/")
    return not ("playwright/driver/" in path or "patchright/driver/" in path)

a.datas = [d for d in a.datas if _keep(d)]
a.binaries = [b for b in a.binaries if _keep(b)]
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, a.binaries, a.datas,
    name="DeviceScout",
    console=True,          # keeps a small window with the URL and a way to stop the server
    icon=None,
    upx=False,
)
