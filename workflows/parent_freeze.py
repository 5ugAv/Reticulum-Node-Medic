"""Carry the parent medic's Python packages EXACTLY as it runs them.

A clone copied Node Medic 1's files but installed its Python packages from
whatever the wheelhouse had downloaded last: rns 1.3.8, while the parent runs
a hand-patched rns 1.3.7 whose identity tool understands the medic's Tracker
radio. Node Medic 2 could write its Tracker's firmware but never name it
(2026-10-06). The keeper's rule: "how Node Medic 1 works, all of that is
packaged up and sent across in the cloning."

So, on the parent:

* every pure-Python package the tool runs is re-packed into a wheel straight
  from the installed tree (patches included);
* compiled packages (Kivy, Pillow, cryptography, cffi) keep the downloaded
  wheel whose version matches what is installed, and are named when it does
  not match;
* nothing is removed: the downloaded wheels stay for the manifest fallback,
  and explicit pins make pip pick the parent's version regardless;
* ``assets/packages/requirements-parent.txt`` pins every name to the parent's
  version, and the clone installs from THAT.

Pure functions where possible, so the repack is unit-tested on a fake
site-packages with no network.
"""
from __future__ import annotations

import base64
import hashlib
import os
import re
import zipfile
from typing import Dict, Iterable, List, Optional, Tuple

PARENT_REQUIREMENTS = "requirements-parent.txt"
_COMPILED = (".so", ".pyd", ".dylib")


def norm_name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def file_name(name: str) -> str:
    """The wheel-filename form of a distribution name."""
    return re.sub(r"[-_.]+", "_", name)


def _b64_sha256(data: bytes) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()


def is_pure(dist) -> bool:
    """No compiled extension among the distribution's files."""
    for f in dist.files or []:
        if str(f).endswith(_COMPILED):
            return False
    return True


def repack_pure(dist, dest_dir: str) -> str:
    """Build ``<name>-<version>-py3-none-any.whl`` in *dest_dir* from an
    INSTALLED pure-Python distribution (importlib.metadata.Distribution),
    exactly as it is on disk — patches and all. Returns the wheel path.

    pip needs: the package files, a ``<dist>.dist-info`` holding METADATA,
    WHEEL, RECORD (regenerated — the installed RECORD lists console scripts
    outside site-packages, which pip recreates from entry_points.txt) and,
    when present, entry_points.txt and top_level.txt."""
    name = dist.metadata["Name"]
    version = dist.version
    tag = f"{file_name(name)}-{version}"
    info_dir = f"{tag}.dist-info"
    out = os.path.join(dest_dir, f"{tag}-py3-none-any.whl")
    root = dist.locate_file("")
    records: List[Tuple[str, str, int]] = []
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in dist.files or []:
            rel = str(f)
            if rel.startswith("..") or "/__pycache__/" in rel or rel.endswith(".pyc"):
                continue
            if rel.split("/", 1)[0].endswith(".dist-info"):
                continue
            src = os.path.join(str(root), rel)
            if not os.path.isfile(src):
                continue
            with open(src, "rb") as fh:
                data = fh.read()
            zf.writestr(rel, data)
            records.append((rel, _b64_sha256(data), len(data)))
        # metadata straight from the distribution (no folder guessing)
        meta_txt = dist.read_text("METADATA") or dist.read_text("PKG-INFO")
        if not meta_txt:
            raise RuntimeError(f"{name}: no METADATA to carry")
        for fname, txt in (("METADATA", meta_txt),
                           ("entry_points.txt", dist.read_text("entry_points.txt")),
                           ("top_level.txt", dist.read_text("top_level.txt"))):
            if txt:
                data = txt.encode("utf-8")
                zf.writestr(f"{info_dir}/{fname}", data)
                records.append((f"{info_dir}/{fname}", _b64_sha256(data), len(data)))
        wheel_txt = ("Wheel-Version: 1.0\nGenerator: nodemedic-parent-freeze\n"
                     "Root-Is-Purelib: true\nTag: py3-none-any\n").encode()
        zf.writestr(f"{info_dir}/WHEEL", wheel_txt)
        records.append((f"{info_dir}/WHEEL", _b64_sha256(wheel_txt), len(wheel_txt)))
        record_lines = [f"{p},sha256={h},{n}" for p, h, n in records]
        record_lines.append(f"{info_dir}/RECORD,,")
        zf.writestr(f"{info_dir}/RECORD", "\n".join(record_lines) + "\n")
    return out


def wheel_version(path: str) -> Optional[Tuple[str, str]]:
    """``(normalised name, version)`` from a wheel filename, or None for a
    file that is not a conforming wheel (a stray ``dummy_test.whl`` must not
    abort the freeze)."""
    base = os.path.basename(path)[:-4]
    parts = base.split("-")
    if len(parts) < 2 or not parts[0] or not parts[1]:
        return None
    return norm_name(parts[0]), parts[1]


def plan(installed: Dict[str, Tuple[str, bool]], wheels: Iterable[str]):
    """Decide, per installed package ``name -> (version, pure)``, what the
    wheelhouse needs. Returns ``(repack, keep, missing)``: names to repack
    from the tree (a repack overwrites any downloaded copy of the same
    version — patches included), compiled wheel paths already matching, and
    compiled names whose version has no wheel (need an online download)."""
    wheels = [w for w in wheels if wheel_version(w)]
    by_name: Dict[str, List[str]] = {}
    for w in wheels:
        n, _v = wheel_version(w)
        by_name.setdefault(n, []).append(w)
    repack, keep, missing = [], [], []
    for name, (version, pure) in installed.items():
        n = norm_name(name)
        matching = [w for w in by_name.get(n, []) if wheel_version(w)[1] == version]
        if pure:
            repack.append(name)
        elif matching:
            keep += matching
        else:
            missing.append(f"{name}=={version}")
    return repack, keep, missing


def fallback_versions(missing: Iterable[str], wheels: Iterable[str]) -> Tuple[Dict[str, str], List[str]]:
    """For compiled packages whose installed version has no wheel: the
    version the wheelhouse DOES hold (``name -> version``), and the names
    with no wheel at all. One mismatched compiled package must never cost
    the clone every other pin (it would get stock rns again)."""
    have: Dict[str, str] = {}
    for w in wheels:
        wv = wheel_version(w)
        if wv:
            have.setdefault(wv[0], wv[1])
    fallback, none = {}, []
    for item in missing:
        name = item.split("==", 1)[0]
        if norm_name(name) in have:
            fallback[name] = have[norm_name(name)]
        else:
            none.append(item)
    return fallback, none


def requirements_text(installed: Dict[str, Tuple[str, bool]],
                      manifest: Iterable[str] = ()) -> str:
    """The parent's exact pins, plus any manifest line whose package the
    parent takes from the OS (so the clone still gets it from the wheelhouse)."""
    lines = ["# The PARENT medic's installed packages, pinned exactly — written by",
             "# workflows.parent_freeze on the medic that makes clones. A clone",
             "# installs these, not the latest downloads, so it runs what its",
             "# parent runs (patches included)."]
    lines += [f"{name}=={ver}" for name, (ver, _p) in sorted(installed.items(),
                                                             key=lambda kv: kv[0].lower())]
    frozen = {norm_name(n) for n in installed}
    extra = [m for m in manifest
             if norm_name(re.split(r"[=<>!~ \[]", m, maxsplit=1)[0]) not in frozen]
    if extra:
        lines.append("# from the manifest — the parent has these from its OS:")
        lines += extra
    return "\n".join(lines) + "\n"


def runtime_names(requirements_path: str, wheelhouse: str) -> List[str]:
    """The packages to freeze: every name in the manifest plus every name that
    already has a wheel in the wheelhouse (the manifest's dependency closure)."""
    names = []
    try:
        for line in open(requirements_path, encoding="utf-8"):
            line = line.split("#", 1)[0].strip()
            if line:
                names.append(re.split(r"[=<>!~ ]", line, maxsplit=1)[0])
    except OSError:
        pass
    try:
        for w in os.listdir(wheelhouse):
            if w.endswith(".whl"):
                names.append(w.split("-", 1)[0])
    except OSError:
        pass
    seen, out = set(), []
    for n in names:
        if norm_name(n) not in seen:
            seen.add(norm_name(n))
            out.append(n)
    return out


def user_site_names() -> List[str]:
    """Every distribution installed in THIS user's site-packages — the whole
    of what the medic runs, not only the manifest (platformio, esptool, …)."""
    import importlib.metadata as md
    out = []
    for d in md.distributions():
        try:
            if "/.local/" in str(d.locate_file("")):
                out.append(d.metadata["Name"])
        except Exception:                                  # noqa: BLE001
            continue
    return sorted(set(out), key=str.lower)


def in_user_site(dist) -> bool:
    """Installed by the medic itself (``~/.local``), not shipped by the OS.
    Debian's dist-packages (certifi, pillow, pyserial, smbus2 on Node Medic
    1) stay the OS's business: their egg-info has no wheel metadata and the
    clone's card carries the same packages."""
    try:
        where = str(dist.locate_file(""))
    except Exception:                                      # noqa: BLE001
        return False
    # the OS's own trees; everything else (user site, /usr/local) is the
    # medic's doing and travels
    return not (where.startswith("/usr/lib/python3") or where.startswith("/lib/python3"))


def installed_versions(names: Iterable[str], lookup=None) -> Dict[str, Tuple[str, bool]]:
    """``name -> (version, pure)`` for the names installed in THIS medic's
    own user site. *lookup* (name -> Distribution) is for tests."""
    import importlib.metadata as md
    lookup = lookup or md.distribution
    out: Dict[str, Tuple[str, bool]] = {}
    for n in names:
        try:
            dist = lookup(n)
        except md.PackageNotFoundError:
            continue
        if not in_user_site(dist):
            continue
        out[dist.metadata["Name"]] = (dist.version, is_pure(dist))
    return out


def manifest_lines(requirements_path: str) -> List[str]:
    """The manifest's own requirement lines (comments stripped)."""
    out = []
    try:
        for line in open(requirements_path, encoding="utf-8"):
            line = line.split("#", 1)[0].strip()
            if line:
                out.append(line)
    except OSError:
        pass
    return out


def marker_applies(marker: str) -> bool:
    """Does an environment marker hold HERE (a Linux aarch64 Pi, Python 3.13)?
    Uses ``packaging`` when present; otherwise anything conditional on the
    platform, the Python version or an extra is taken as not applying —
    Kivy lists Windows-only deps and Python<3.8 shims that way."""
    marker = marker.strip()
    if not marker:
        return True
    try:
        from packaging.markers import Marker
        return bool(Marker(marker).evaluate())
    except Exception:                                      # noqa: BLE001
        pass
    conditional = ("extra", "sys_platform", "platform_system", "os_name",
                   "python_version", "implementation_name", "platform_machine")
    return not any(k in marker for k in conditional)


def requires_names(dist) -> List[str]:
    """Plain names a distribution requires HERE: extras and requirements
    whose environment marker does not apply are left out."""
    out = []
    for req in dist.requires or []:
        spec, _sep, marker = req.partition(";")
        if not marker_applies(marker):
            continue
        name = re.split(r"[\s=<>!~\[(]", spec.strip(), maxsplit=1)[0]
        if name:
            out.append(name)
    return out


def unmet_requirements(installed_dists: Iterable, frozen: Iterable[str],
                       wheels: Iterable[str], manifest: Iterable[str]) -> List[str]:
    """Requirements of the frozen packages that nothing in the wheelhouse can
    satisfy offline: not frozen, no wheel of any version, not in the
    manifest. Node Medic 1 takes PyYAML (esptool) from its OS; a clone's pip
    would stop on it."""
    have = {norm_name(n) for n in frozen}
    have |= {wheel_version(w)[0] for w in wheels if wheel_version(w)}
    have |= {norm_name(re.split(r"[=<>!~ \[]", m, maxsplit=1)[0]) for m in manifest}
    unmet = []
    for dist in installed_dists:
        for name in requires_names(dist):
            if norm_name(name) not in have and norm_name(name) not in {norm_name(u) for u in unmet}:
                unmet.append(name)
    return unmet


def freeze(wheelhouse: str, requirements_path: str,
           everything: bool = False) -> Tuple[bool, str]:
    """Do it on this medic. Returns ``(ok, message)``. *everything* freezes
    the whole user site, not only the manifest's closure."""
    import importlib.metadata as md
    wheelhouse = os.path.expanduser(wheelhouse)
    requirements_path = os.path.expanduser(requirements_path)
    os.makedirs(wheelhouse, exist_ok=True)
    names = runtime_names(requirements_path, wheelhouse)
    if everything:
        names = sorted(set(names) | set(user_site_names()), key=str.lower)
    installed = installed_versions(names)
    wheels = [os.path.join(wheelhouse, w) for w in os.listdir(wheelhouse) if w.endswith(".whl")]
    repack, _keep, missing = plan(installed, wheels)
    # a compiled package at a version with no wheel: pin the wheelhouse's
    # version for the clone rather than lose every pin
    fallback, missing = fallback_versions(missing, wheels)
    for name, ver in fallback.items():
        installed[name] = (ver, False)
    dists = []
    for name in installed:
        try:
            dists.append(md.distribution(name))
        except md.PackageNotFoundError:
            pass
    missing += [f"{n} (needed by a frozen package)" for n in
                unmet_requirements(dists, installed, wheels, manifest_lines(requirements_path))]
    made = []
    for name in repack:
        try:
            made.append(os.path.basename(repack_pure(md.distribution(name), wheelhouse)))
        except Exception as exc:                               # noqa: BLE001
            return False, f"could not re-pack {name}: {exc}"
    pins_path = os.path.join(wheelhouse, PARENT_REQUIREMENTS)
    msg = (f"Froze {len(installed)} packages as this medic runs them: "
           f"{len(made)} re-packed from the installed tree, "
           f"{len(installed) - len(made)} compiled wheels kept.")
    if fallback:
        msg += (" Carried at the wheelhouse's version instead of this medic's: "
                + ", ".join(f"{k}=={v}" for k, v in fallback.items()) + ".")
    if missing:
        # no pins a clone cannot satisfy offline: it falls back to the manifest
        try:
            os.remove(pins_path)
        except OSError:
            pass
        msg += (" NOT carried (no wheel for the installed version; fetch while "
                "online): " + ", ".join(missing))
        return False, msg
    with open(pins_path, "w", encoding="utf-8") as fh:
        fh.write(requirements_text(installed, manifest_lines(requirements_path)))
    return True, msg
