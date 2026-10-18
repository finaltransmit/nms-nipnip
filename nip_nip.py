"""NipNip 1.1 - edible NipNip Buds with psychedelic colours (No Man's Sky, NMS.py / pyMHF mod).

Together with the NipNip data tables (NipNip Buds get a Consume action that gives the reward R_NIPNIP),
eating a bud makes the colours of the world flow: the game's own screen filters (colour-grading LUTs such as
Weird1-8, HueShiftA-D, NMSRetroG, CorruptSentinels) melt into each other, one every FLOW_XFADE seconds, for
BUDS_SECONDS. Every further bud extends it, up to BUDS_MAX_SECONDS. Settings: see "tunables" below.

How it works (build 180836): the final post-process pass blends four colour LUT slots, Base (photo mode),
Far (planet, every frame), Storm (weather, every frame) and Effect. Effect wins over the others and the game
itself only uses it for the binoculars, so NipNip drives that slot through
cGcFullscreenEffectManager::SetScreenFilter(filter, slot, strength 0..1, extra fade distance), and borrows the
Storm slot as a second layer for crossfades (rewriting the weather's per-frame writes while it does). A
negative fade distance gives the full effect at every depth; a positive one blends the filter in with
distance (near things stay normal, far things change). Eating is detected in the two job functions the
inventory queues when a "hold to consume" completes. Nothing is written to game files or saves.

Playing with it by hand: pyMHF executes Python sent to its local executor (127.0.0.1:6770), e.g.
    import nip_nip; nip_nip.show("NegativePrint", seconds=15)
    nip_nip.flow()                     # endless, 6 s crossfades; flow(xfade=3, shuffle=True, names=[...])
    nip_nip.step("Weird3")             # crossfade to one filter and keep it
    nip_nip.cycle(["Weird3", "HueShiftC", "NMSRetroG"], each=5)
    nip_nip.pulse("Weird6", period=4, seconds=20)
    nip_nip.buds()                     # same as eating a bud
    nip_nip.off()                      # fade back to normal
All calls are queued and run on the game thread (cGcPlayer::Update), so they are safe from any thread.
Writes a log to nip_nip.log next to this file.
"""

from __future__ import annotations

import ctypes
import logging
import math
import os
import queue
import random
import time

import pymhf.core._internal as _internal
from pymhf import FUNCDEF, Mod
from pymhf.core.hooking import manual_hook
from pymhf.core.memutils import find_pattern_in_binary
from pymhf.core.mod_loader import mod_manager

import nmspy.data.types as nms
from nmspy.data.enums import cGcScreenFilters

logger = logging.getLogger("NipNip")

LOG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "nip_nip.log")
if not any(getattr(h, "name", "") == "nip_nip_file" for h in logger.handlers):
    try:
        _fh = logging.FileHandler(LOG_FILE, encoding="utf-8")
        _fh.name = "nip_nip_file"
        _fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s"))
        logger.addHandler(_fh)
    except Exception:
        pass

# ---------------------------------------------------------------------------- tunables ----
BUDS_SECONDS = 180.0  # how long one bud lasts
BUDS_MAX_SECONDS = 600.0  # extra buds extend the effect up to this much remaining time
BUDS_FADE_OUT = 8.0  # the flow fades back to normal over this many seconds at the end
FLOW_XFADE = 6.0  # seconds each filter takes to melt into the next
BUDS_SHUFFLE = True  # a new random order of the filters on every pass
BUDS_DISTANCE = -10000.0  # < 0: whole view; > 0: blend the colours in over this many metres of depth
# The filters a bud cycles through (names from nmspy's cGcScreenFilters, 85 in build 180836). Filters that
# flip light and dark (NegativePrint, BlackAndWhite) are left out on purpose.
VIVID = ["Weird3", "HueShiftC", "NMSRetroG", "Weird6", "Cyan", "Fuchsia", "HueShiftD", "NMSRetroE",
         "TwoToneStrong", "Red", "Violet", "WarmStripe", "Surveying", "Weird1", "CorruptSentinels", "Solarise"]

# ------------------------------------------------------------------ game data layout ----
# cGcFullscreenEffectManager::SetScreenFilter(this, eScreenFilter, eFilterSlot, float strength, float extra
# fade distance) on 180836 (NMS+0x916590); unique in .text.
SET_SCREEN_FILTER_SIG = ("48 89 5C 24 08 48 89 6C 24 10 48 89 74 24 18 48 89 7C 24 20 F3 0F 10 0D ? ? ? ? "
                         "48 8D B9 ? ? ? ? 48 8B 07 0F 57 C0 F3 0F 10 15 ? ? ? ? F3 0F 5F C3 4C 63 D2 4C 8B "
                         "C9 49 63 D8 F3 0F 5D C8")
SLOT_BASE, SLOT_FAR, SLOT_STORM, SLOT_EFFECT = 0, 1, 2, 3  # eFilterSlot
MGR_ACTIVE_FILTERS = 0xEC  # int32[4], active filter per slot
MGR_STRENGTHS = 0xFC  # float[4], strength per slot

# >>> nms-address-lookup (shared block: keep identical in every mod; tools/check_addresses.py runs it offline) >>>
# Build-specific addresses are looked up in the running NMS.exe by masked byte patterns, so a game update does not
# need a new mod release. Pattern tokens: "48" a byte, "?" any byte, "[2-8]" 2 to 8 any bytes, "* * * *" a rel32
# field (RIP-relative operand or call target: value = end of its instruction + rel32; several "*" fields in one
# pattern must agree), "# # # #" a u32 field (the value itself, e.g. a struct offset), ">" the end of the
# instruction holding the field when an immediate follows it, "^" the result position of a pattern without a
# field (default: its start), "@text" a rel32 that must point at the bytes "text" and "=NAME" a rel32 that must
# point at the address NAME found earlier in the same table (both pick the right one of look-alike sites).
# A pattern counts when all its matches give one value; the counting patterns must agree, and the value must pass
# its kind's check ("data": inside a writable data section, "code": a function start in executable code, plus an
# optional masked "prologue", "value": inside "range") and the optional live "check" ("ptr", "vft", "vft:<class>",
# "u32<N"; a null pointer passes). Nothing found -> 0 and a warning; the caller switches that feature off. Only on
# the build the "known" values were verified on (180836) is a failed lookup replaced by the known value.
import ctypes as _actypes
import re as _are
import sys as _asys

_ADDR_KNOWN_BUILD = (0x6ABE9229, 0x771D000)  # NMS.exe 180836: PE TimeDateStamp, SizeOfImage
_addr_k32 = _actypes.WinDLL("kernel32") if hasattr(_actypes, "WinDLL") else None
if _addr_k32 is not None:
    _addr_k32.GetCurrentProcess.restype = _actypes.c_void_p
    _addr_k32.ReadProcessMemory.argtypes = [_actypes.c_void_p, _actypes.c_void_p, _actypes.c_void_p,
                                            _actypes.c_size_t, _actypes.POINTER(_actypes.c_size_t)]


def _addr_peek(addr: int, n: int):
    """n bytes at an absolute address of this process, or None (never faults)."""
    out, got = _actypes.create_string_buffer(n), _actypes.c_size_t(0)
    if addr and _addr_k32.ReadProcessMemory(_addr_k32.GetCurrentProcess(), _actypes.c_void_p(addr), out, n,
                                            _actypes.byref(got)) and got.value == n:
        return out.raw
    return None


class _AddrImage:
    """Sections and bytes of NMS.exe: the running image (base given) or an exe file (offline checks). Code is read
    from the exe file when it is the running build: the same bytes, minus the mods' hooks and patches."""

    def __init__(self, base: int = 0, path: str = ""):
        import pefile
        self.base, self._file = base, None
        if base:
            head = _addr_peek(base, 0x1000)
            if head is None:
                raise RuntimeError("cannot read the PE header of the running image")
            pe = pefile.PE(data=head, fast_load=True)
        else:
            pe = self._file = pefile.PE(path, fast_load=True)
        self.build = (pe.FILE_HEADER.TimeDateStamp, pe.OPTIONAL_HEADER.SizeOfImage)
        self.sections = [(s.Name.rstrip(b"\0").decode(errors="replace"), s.VirtualAddress, s.Misc_VirtualSize,
                          s.Characteristics) for s in pe.sections]
        if base and path:
            try:
                f = pefile.PE(path, fast_load=True)
                if (f.FILE_HEADER.TimeDateStamp, f.OPTIONAL_HEADER.SizeOfImage) == self.build:
                    self._file = f
            except Exception:
                pass
        self._text = None

    def section(self, rva: int):
        for name, va, size, chars in self.sections:
            if va <= rva < va + size:
                return name, chars
        return None, 0

    def read(self, rva: int, n: int):
        if self.section(rva)[0] is None or self.section(rva + n - 1)[0] is None:
            return None
        if self._file is None:
            return _addr_peek(self.base + rva, n)
        b = self._file.get_data(rva, n)
        return b + b"\0" * (n - len(b))  # uninitialised data reads as zeros

    def text(self):
        if self._text is None:
            _, va, size, _ = next(s for s in self.sections if s[0] == ".text")
            self._text = (va, self.read(va, size))
        return self._text


_ADDR_RX = {}


def _addr_compile(sig: str):
    """Pattern -> (regex, [field token per regex group], {field group: group of its instruction end})."""
    if sig in _ADDR_RX:
        return _ADDR_RX[sig]
    toks, rx, fields, ends, k, last = sig.split(), b"", [], {}, 0, 0
    while k < len(toks):
        t = toks[k]
        if t in ("*", "#"):
            if toks[k:k + 4] != [t] * 4:
                raise ValueError(f"'{t}' must come four in a row: {sig}")
            rx += b"(....)"
            fields.append(t)
            last = len(fields)
            k += 4
            continue
        if t[0] in "@=":
            rx += b"(....)"
            fields.append(t)
            last = len(fields)
        elif t in (">", "^"):
            rx += b"()"
            fields.append(t)
            if t == ">":
                ends[last] = len(fields)
        elif t == "?":
            rx += b"."
        elif t.startswith("["):
            lo, hi = t[1:-1].split("-")
            rx += b".{%d,%d}?" % (int(lo), int(hi))
        else:
            rx += _are.escape(bytes([int(t, 16)]))
        k += 1
    _ADDR_RX[sig] = out = (_are.compile(rx, _are.S), fields, ends)
    return out


def _addr_match_value(img, text_rva: int, m, fields, ends, env):
    """The value one match gives, or None (an @text/=NAME check failed, or its fields disagree)."""
    value, pos = None, None
    for g, f in enumerate(fields, 1):
        if f == "^":
            pos = text_rva + m.start(g)
        if f not in ("*", "#") and f[0] not in "@=":
            continue
        raw = int.from_bytes(m.group(g), "little", signed=f != "#")
        v = raw if f == "#" else text_rva + (m.start(ends[g]) if g in ends else m.end(g)) + raw
        if f.startswith("@"):
            want = f[1:].encode()
            if img.read(v, len(want)) != want:
                return None
        elif f[0] == "=":
            if not env.get(f[1:]) or env[f[1:]] != v:
                return None
        elif value is None:
            value = v
        elif value != v:
            return None
    if value is None:
        value = pos if pos is not None else text_rva + m.start()
    return value


def _addr_scan(img, sig: str, env: dict):
    """Set of values the pattern's matches give in .text (cached per build across mods)."""
    cache = getattr(_asys, "_nms_addr_scan_cache", None)
    if cache is None:
        cache = {}
        setattr(_asys, "_nms_addr_scan_cache", cache)
    rx, fields, ends = _addr_compile(sig)
    key = (img.build, sig) + tuple(env.get(f[1:]) for f in fields if f[0] == "=")
    if key not in cache:
        text_rva, text = img.text()
        vals = {_addr_match_value(img, text_rva, m, fields, ends, env) for m in rx.finditer(text)}
        vals.discard(None)
        cache[key] = frozenset(vals)
    return cache[key]


def _addr_live_check(img, check: str, rva: int):
    """None if the live value at NMS+rva passes `check`, else what is wrong."""
    def u64(a):
        b = _addr_peek(a, 8)
        return int.from_bytes(b, "little") if b else None
    slot = img.base + rva
    if check.startswith("u32<"):
        b = _addr_peek(slot, 4)
        v = int.from_bytes(b, "little") if b else None
        return None if v is not None and v < int(check[4:], 0) else f"value {v} not {check[3:]}"
    obj = u64(slot)
    if obj is None:
        return "slot unreadable"
    if not obj:
        return None  # not created yet: nothing to check
    if check == "ptr":
        return None if _addr_peek(obj, 8) is not None else f"points to unreadable {obj:#x}"
    vft = u64(obj)
    if vft is None:
        return f"points to unreadable {obj:#x}"
    name, chars = img.section(vft - img.base)
    if name is None or chars & 0xA0000000:  # executable or writable: not a vftable
        return f"object {obj:#x} has no vftable in the image"
    if check.startswith("vft:"):
        col = u64(vft - 8)
        hdr = _addr_peek(col, 16) if col else None
        td = int.from_bytes(hdr[12:16], "little") if hdr else 0
        want = (".?AV" + check[4:] + "@@\0").encode()
        if not td or _addr_peek(img.base + td + 16, len(want)) != want:
            return f"object {obj:#x} is not a {check[4:]}"
    return None


def _addr_check(img, spec: dict, v: int):
    """None if v is plausible for the spec's kind, else what is wrong."""
    kind = spec["kind"]
    if kind == "value":
        lo, hi = spec["range"]
        return None if lo <= v < hi else f"{v:#x} outside {lo:#x}..{hi:#x}"
    name, chars = img.section(v)
    if kind == "data":
        if name is None or not chars & 0x80000000 or chars & 0x20000000:
            return f"NMS+0x{v:X} is not in a writable data section ({name})"
    elif kind == "code":
        if name is None or not chars & 0x20000000:
            return f"NMS+0x{v:X} is not in executable code ({name})"
        before = img.read(v - 1, 1)
        if v & 0xF and before not in (b"\xcc", b"\xc3"):
            return f"NMS+0x{v:X} is not a function start"
        if spec.get("prologue"):
            rx = _addr_compile(spec["prologue"])[0]
            code = img.read(v, 64)
            if not code or not rx.match(code):
                return f"NMS+0x{v:X} does not start with the expected prologue"
    else:
        return f"unknown kind {kind!r}"
    if spec.get("check") and img.base:
        return _addr_live_check(img, spec["check"], v)
    return None


def _addr_lookup(img, spec: dict, env: dict):
    """(value or 0, problem or None, per-pattern notes)."""
    found, notes = set(), []
    for sig in spec["sigs"]:
        try:
            vals = _addr_scan(img, sig, env)
        except Exception as e:
            vals = frozenset()
            notes.append(f"error {e!r}")
            continue
        notes.append("1" if len(vals) == 1 else f"{len(vals)} values" if vals else "0")
        if len(vals) == 1:
            found |= vals
    if not found:
        return 0, "no pattern matched", notes
    if len(found) > 1:
        return 0, "patterns disagree: " + ", ".join(f"0x{v:X}" for v in sorted(found)), notes
    v = found.pop()
    problem = _addr_check(img, spec, v)
    return (0, problem, notes) if problem else (v, None, notes)


def _addr_resolve(specs: dict, log, img=None) -> dict:
    """{name: RVA ("data"/"code") or value ("value"), 0 when not found} for an ADDRESSES table."""
    out = {}
    try:
        if img is None:
            import pymhf.core._internal as _pi
            img = _AddrImage(base=_pi.BASE_ADDRESS, path=_pi.BINARY_PATH)
    except Exception:
        log.exception("address lookup: cannot read the game image")
        return {name: 0 for name in specs}
    known_build = img.build == _ADDR_KNOWN_BUILD
    for name, spec in specs.items():
        try:
            v, problem, notes = _addr_lookup(img, spec, out)
        except Exception as e:
            v, problem, notes = 0, f"lookup failed: {e!r}", []
        show = (lambda x: f"0x{x:X}") if spec["kind"] == "value" else (lambda x: f"NMS+0x{x:X}")
        if known_build and v != spec["known"]:
            log.warning("address %s: %s (patterns %s) - build 180836, so using its verified value %s", name,
                        problem or f"patterns give {show(v)}", "/".join(notes), show(spec["known"]))
            v = spec["known"]
        elif problem:
            log.warning("address %s not found: %s (patterns %s) - what needs it stays off", name, problem,
                        "/".join(notes))
        else:
            log.info("address %s = %s (patterns %s)", name, show(v), "/".join(notes))
        out[name] = v
    return out
# <<< nms-address-lookup <<<

# Game addresses (see the block above; tools/check_addresses.py verifies them offline). "known": the
# value on build 180836, used only on that build if the patterns fail there.
ADDRESSES = {
    # cGcFullscreenEffectManager::SetScreenFilter (the hook's own signature)
    "SET_SCREEN_FILTER": {"kind": "code", "known": 0x916590, "sigs": [SET_SCREEN_FILTER_SIG]},
    # gApplication.mpData (cGcApplication::Data*): the callers of SetScreenFilter load it (mov rcx,[it]; add rcx,
    # <manager>; ... call SetScreenFilter, 19 sites) and three unrelated readers (those also match 4.13)
    "APP_DATA_SLOT": {"kind": "data", "known": 0x6E8D708, "check": "ptr", "sigs": [
        "48 8B 0D * * * * [0-16] 48 81 C1 ? ? ? ? [0-24] E8 =SET_SCREEN_FILTER",
        "48 8B 0D * * * * 33 C0 39 81 ? ? ? ? 0F 9E C0 83 C0 02 C3",
        "48 8B 15 * * * * 48 63 82 ? ? ? ? 48 8B 92 ? ? ? ? 48 83 C2 04",
        "48 8B 0D * * * * 83 B9 ? ? ? ? 03 75 ? 4C 8D 8D ? ? ? ?",
    ]},
    # Data -> its cGcFullscreenEffectManager (Data.mSimulation + cGcSimulation.mFullscreenEffectManager): the "add
    # rcx,<offset>" of those same SetScreenFilter callers
    "DATA_TO_FX_MANAGER": {"kind": "value", "known": 0x72A3A0, "range": (0x100, 0x10000000), "sigs": [
        "48 8B 0D ? ? ? ? [0-16] 48 81 C1 # # # # [0-24] E8 =SET_SCREEN_FILTER",
    ]},
}
_RESOLVED = _addr_resolve(ADDRESSES, logger)
# Fallback if SetScreenFilter has not been seen yet: [gApplication.mpData] + DATA_TO_FX_MANAGER
# (Data.mSimulation + cGcSimulation.mFullscreenEffectManager), as every caller does (0 = not found on this build)
APP_DATA_SLOT = _RESOLVED["APP_DATA_SLOT"]
DATA_TO_FX_MANAGER = _RESOLVED["DATA_TO_FX_MANAGER"]
BUDS_REWARD = b"R_NIPNIP"  # reward the data mod gives when a bud is consumed
# Eating from the inventory doesn't go through GiveGenericReward: when the "hold to consume" completes, the
# UI queues a job that looks the reward up itself and calls GiveRewardsFromList. There are two such job
# functions on 180836 (product-slot popup NMS+0x6A0BA0, inventory grid NMS+0x6A6A60); their only argument is
# the captured state: +0x00 the effective reward ID (TkID<128>), then seed, store, slot, a copy of the slot
# element and the cGcConsumableItem*; a "can afford" flag (0 = the consume fails) sits at +0x78 / +0x68.
CONSUME_SIG = ("48 89 5C 24 20 55 56 57 41 54 41 55 41 56 41 57 48 8D 6C 24 ? 48 81 EC ? ? ? ? 33 F6 4C 8B F9 49 C7 "
               "C6 FF FF FF FF 40 38 71 {} 0F 84")
CONSUME_POPUP = {"sig": CONSUME_SIG.format("78"), "can_afford": 0x78, "product": 0x38}
CONSUME_GRID = {"sig": CONSUME_SIG.format("68"), "can_afford": 0x68, "product": 0x30}
NUM_FILTERS = len(cGcScreenFilters)  # 85 on 180836; also the game's "no filter requested" value

_k32 = ctypes.WinDLL("kernel32")  # private instance, so the argtypes below don't leak to other mods
_k32.GetCurrentProcess.restype = ctypes.c_void_p
_k32.ReadProcessMemory.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t,
                                   ctypes.POINTER(ctypes.c_size_t)]


def safe_read(addr: int, ctype):
    """Read a value without risking an access violation if a game update moved an offset."""
    out, n = ctype(), ctypes.c_size_t(0)
    if addr and _k32.ReadProcessMemory(_k32.GetCurrentProcess(), ctypes.c_void_p(addr), ctypes.byref(out),
                                       ctypes.sizeof(out), ctypes.byref(n)) and n.value == ctypes.sizeof(out):
        return out.value
    return None


def filter_id(name_or_id) -> int:
    """'Weird3', 'weird3' or 14 -> 14."""
    if isinstance(name_or_id, int):
        return int(cGcScreenFilters(name_or_id))
    for f in cGcScreenFilters:
        if f.name.lower() == str(name_or_id).lower():
            return int(f)
    raise ValueError(f"unknown screen filter {name_or_id!r}")


def _fixed_string(p) -> bytes:
    """Bytes of a cTkFixedString passed by pointer (pyMHF hands over an int or a ctypes pointer)."""
    if isinstance(p, int):
        addr = p
    elif hasattr(p, "contents"):
        addr = ctypes.addressof(p.contents)
    else:
        addr = ctypes.cast(p, ctypes.c_void_p).value or 0
    raw = safe_read(addr, ctypes.c_char * 16) if addr else None
    return bytes(raw).split(bytes(1), 1)[0] if raw is not None else b""


def filter_name(fid: int) -> str:
    try:
        return cGcScreenFilters(fid).name
    except ValueError:
        return str(fid)


# --------------------------------------------------------------------------- programs ----
# A program is a function of the time since it started returning (filter, strength, distance) for the
# Effect slot, or a pair (effect layer, storm layer) to use the Storm slot as a second layer, or None when it
# has finished. NipNip evaluates it every frame and calls SetScreenFilter when the result changes. The Storm
# slot sits under Effect in the blend (final = s3*Effect + (1-s3)*s2*Storm + rest planet), so a filter in
# one slot can crossfade into a filter in the other; cGcSky's per-frame Storm writes are rewritten meanwhile.

def _envelope(t: float, seconds: float | None, fade: float) -> float:
    """0..1: fade in over `fade` s, hold, fade out over the last `fade` s of `seconds` (None = forever)."""
    k = min(1.0, t / fade) if fade > 0 else 1.0
    if seconds is not None and fade > 0:
        k = min(k, max(0.0, (seconds - t) / fade))
    return k


def prog_show(fid: int, seconds: float | None, strength: float, distance: float, fade: float):
    def run(t):
        if seconds is not None and t >= seconds:
            return None
        return fid, strength * _envelope(t, seconds, fade), distance
    return run


def prog_pulse(fid: int, period: float, lo: float, hi: float, seconds: float | None, distance: float):
    def run(t):
        if seconds is not None and t >= seconds:
            return None
        s = lo + (hi - lo) * (0.5 - 0.5 * math.cos(2 * math.pi * t / period))
        return fid, s * _envelope(t, seconds, 1.0), distance
    return run


def prog_cycle(fids: list[int], each: float, total: float | None, strength: float, distance: float,
               fade: float):
    """Show each filter for `each` s (dipping through the normal look in between), round and round."""
    def run(t):
        if total is not None and t >= total:
            return None
        i, tt = divmod(t, each)
        s = strength * _envelope(tt, each, fade)
        if total is not None:
            s *= _envelope(t, total, fade)
        return fids[int(i) % len(fids)], s, distance
    return run


def prog_fade_out(layers, fade: float):
    """Fade whatever is showing (effect layer, storm layer) back to normal."""
    eff, storm = layers

    def run(t):
        if t >= fade:
            return None
        g = 1.0 - t / fade
        return ((eff[0], eff[1] * g, eff[2]) if eff else None,
                (storm[0], storm[1] * g, storm[2]) if storm else None)
    return run


class Scene:
    """One filter in a show: crossfades in from the previous scene over `xfade` s, then holds for `hold` s.
    `pulse` = (period s, depth 0..1) lets its strength breathe; `distance` as in show()."""

    def __init__(self, name, xfade: float, hold: float, distance: float = -10000.0, pulse=None):
        self.fid, self.xfade, self.hold, self.distance, self.pulse = filter_id(name), xfade, hold, distance, pulse

    def strength(self, u: float) -> float:
        if not self.pulse:
            return 1.0
        period, depth = self.pulse  # breathes from the end of its crossfade, so the hand-over is seamless
        u = max(0.0, u - self.xfade)
        return 1.0 - depth * (0.5 - 0.5 * math.cos(2 * math.pi * u / period))


def _under(weight: float, top: float) -> float:
    """Storm-slot strength that gives the lower layer `weight` in the final picture under a top layer of
    strength `top` (the blend is wStorm = (1 - top) * s2), so crossfades sum to 1 and stay linear."""
    return min(1.0, weight / max(1e-6, 1.0 - top)) if weight > 0 else 0.0


def prog_scenes(scenes: list[Scene], out: float):
    """Scenes alternate between the Effect slot (even) and the Storm slot (odd) so each can crossfade into
    the next; after the last one everything fades back to normal over `out` s."""
    starts, t = [], 0.0
    for sc in scenes:
        starts.append(t)
        t += sc.xfade + sc.hold
    end = t

    def layer(i, s):
        sc = scenes[i]
        return sc.fid, max(0.0, min(1.0, s)), sc.distance

    def run(t):
        if t >= end + out:
            return None
        if t >= end:
            i, k, g = len(scenes) - 1, 1.0, 1.0 - (t - end) / out
        else:
            i = max(j for j, st in enumerate(starts) if st <= t)
            u = t - starts[i]
            k, g = (min(1.0, u / scenes[i].xfade) if scenes[i].xfade > 0 else 1.0), 1.0
        m = scenes[i].strength(t - starts[i]) * g
        prev = i - 1 if i > 0 and k < 1.0 else None
        mp = scenes[prev].strength(t - starts[prev]) if prev is not None else 0.0
        if i % 2 == 0:  # this scene on top (Effect), the previous one underneath (Storm) while crossfading
            return layer(i, k * m), (layer(prev, mp) if prev is not None else None)
        # this scene underneath (Storm), the previous one on top (Effect) fading away
        if prev is None:
            return (scenes[i - 1].fid, 0.0, scenes[i - 1].distance), layer(i, m)
        top = mp * (1.0 - k)
        return layer(prev, top), layer(i, _under(k * m, top))
    run.duration = end + out
    return run


# "Let It Happen" (Tame Impala, 7:47): ~125 bpm, so a bar is 1.92 s. Gentle colour drift, depth-faded hue
# shifts, the weird-planet looks, a fast retro run for the skipping-record break, a pulsing climax and a slow
# return to normal. Nothing changes faster than a 1.5 s crossfade; pulses breathe over 1-2 bars.
BAR = 1.92
TRIP_SCENES = [
    ("NewVibrant", 8, 12), ("HueShiftA", 6, 14, 250), ("HueShiftB", 5, 15, 150), ("Weird2", 5, 15),
    ("Weird3", 5, 20), ("Weird6", 5, 15, -10000.0, (2 * BAR, 0.4)), ("Weird1", 5, 15), ("Weird7", 5, 20, 300),
    ("TwoTone", 4, 16),
    ("NMSRetroA", 1.5, 6), ("NMSRetroB", 1.5, 6), ("NMSRetroC", 1.5, 6), ("NMSRetroD", 1.5, 6),
    ("NMSRetroE", 1.5, 6), ("NMSRetroF", 1.5, 6), ("NMSRetroG", 2, 10, -10000.0, (BAR, 0.5)),
    ("Solarise", 4, 12), ("CorruptSentinels", 4, 16, 500), ("HueShiftC", 3, 17, -10000.0, (2 * BAR, 0.3)),
    ("NegativePrint", 3, 12), ("WarmStripe", 3, 12), ("HueShiftD", 3, 17), ("TwoToneStrong", 3, 12),
    ("Surveying", 3, 12), ("MissionSurvey", 3, 12), ("Weird5", 4, 16), ("NewVibrantWarm", 5, 20),
    ("Weird8", 5, 12),
]
TRIP_OUT = 6.0


def prog_step(cur, fid: int, xfade: float, distance: float):
    """Crossfade from the filter showing now (cur = (filter, distance, slot) or None) to `fid` in the other
    slot, then hold it until the next program."""
    def run(t):
        k = min(1.0, t / xfade) if xfade > 0 else 1.0
        if cur is None:  # nothing showing: fade in on top
            return (fid, k, distance), None
        old_fid, old_dist, old_slot = cur
        if old_slot == SLOT_EFFECT:  # old on top fades away, new one underneath
            return (old_fid, 1.0 - k, old_dist), (fid, k, distance)
        return (fid, k, distance), ((old_fid, 1.0, old_dist) if k < 1.0 else None)
    run.slot = SLOT_EFFECT if cur is None or cur[2] == SLOT_STORM else SLOT_STORM
    return run


def prog_flow(fids: list[int], xfade: float, shuffle: bool, distance: float):
    """Endless flow: every filter melts straight into the next (no hold), round and round until off()."""
    order_cache: dict[int, list[int]] = {}

    def fid_at(i: int) -> int:
        n = len(fids)
        rnd, pos = divmod(i, n)
        if not shuffle:
            return fids[pos]
        if rnd not in order_cache:
            order = fids[:]
            random.Random(rnd * 7919 + n).shuffle(order)
            prev_last = order_cache.get(rnd - 1, [None])[-1] if rnd > 0 else None
            if n > 1 and order[0] == prev_last:  # no repeat across the boundary of two passes
                order[0], order[1] = order[1], order[0]
            order_cache[rnd] = order
            order_cache.pop(rnd - 2, None)
        return order_cache[rnd][pos]

    def run(t):
        i, u = divmod(t, xfade)
        i, k = int(i), u / xfade
        cur = fid_at(i)
        if i == 0:  # the very first filter fades in from the normal look
            return (cur, k, distance), None
        prev = fid_at(i - 1)
        if i % 2 == 0:  # new one on top, the previous one underneath
            return (cur, k, distance), (prev, 1.0, distance)
        return (prev, 1.0 - k, distance), (cur, _under(k, 1.0 - k), distance)
    return run


# Work for the game thread: (callable, args). Filled from any thread, drained in cGcPlayer::Update.
_jobs: "queue.SimpleQueue[tuple]" = queue.SimpleQueue()
_instance: "NipNip | None" = None


def _queue(method: str, *args):
    _jobs.put((method, args))


# ------------------------------------------------------------------- public test API ----
def show(name, seconds: float | None = 15.0, strength: float = 1.0, distance: float = -10000.0,
         fade: float = 1.0):
    """Show one filter. distance < 0: full effect at every depth; > 0: blends in over that many metres."""
    _queue("_start", prog_show(filter_id(name), seconds, strength, distance, fade),
           f"show {name} {seconds}s strength {strength} distance {distance}")


def pulse(name, period: float = 4.0, lo: float = 0.0, hi: float = 1.0, seconds: float | None = 20.0,
          distance: float = -10000.0):
    _queue("_start", prog_pulse(filter_id(name), period, lo, hi, seconds, distance),
           f"pulse {name} period {period}s {lo}..{hi} for {seconds}s")


def cycle(names, each: float = 5.0, total: float | None = None, strength: float = 1.0,
          distance: float = -10000.0, fade: float = 0.7):
    fids = [filter_id(n) for n in names]
    if total is None:
        total = each * len(fids)
    _queue("_start", prog_cycle(fids, each, total, strength, distance, fade),
           f"cycle {[filter_name(f) for f in fids]} {each}s each for {total}s")


def scenes(entries, out: float = 6.0, label: str = "scenes"):
    """entries: (name, crossfade s, hold s[, distance[, (pulse period, depth)]]) - crossfades filter to filter."""
    prog = prog_scenes([Scene(*e) for e in entries], out)
    _queue("_start", prog, f"{label}: {len(entries)} scenes, {prog.duration:.0f}s")
    return prog.duration


def trip():
    """The 'Let It Happen' show (~7:47). Start the song at the same moment."""
    return scenes(TRIP_SCENES, TRIP_OUT, "trip")


def flow(names=None, xfade: float = FLOW_XFADE, shuffle: bool = False, distance: float = -10000.0):
    """Loop forever: each filter melts into the next over `xfade` s with no hold. off() fades it out."""
    fids = [filter_id(n) for n in (names or VIVID)]
    _queue("_start", prog_flow(fids, xfade, shuffle, distance),
           f"flow {len(fids)} filters, {xfade}s crossfades{', shuffled' if shuffle else ''}")


def buds():
    """Same as eating one Nip-Nip Bud (for testing without the item)."""
    _queue("_eat_bud")


def step(name, xfade: float = 0.5, distance: float = -10000.0):
    """Crossfade to one filter and keep it (tour mode); off() ends it."""
    _queue("_step", filter_id(name), xfade, distance)


def off(fade: float = 1.0):
    _queue("_stop", fade)


def status() -> str:
    m = _instance
    if m is None:
        return "NipNip not loaded"
    mgr = m._manager()
    active = [safe_read(mgr + MGR_ACTIVE_FILTERS + 4 * i, ctypes.c_int32) for i in range(4)] if mgr else None
    strengths = [safe_read(mgr + MGR_STRENGTHS + 4 * i, ctypes.c_float) for i in range(4)] if mgr else None
    names = [filter_name(a) if a is not None else "?" for a in active] if active else None
    t = time.monotonic() - m._program_t0 if m._program else 0.0
    return (f"manager 0x{mgr:X} slots(base, far, storm, effect)={names} strengths="
            f"{[round(s, 3) if s is not None else None for s in strengths] if strengths else None} "
            f"program={'running %.0fs' % t if m._program else 'none'} effect={m._applied} storm={m._storm}")


class NipNip(Mod):
    __author__ = "Traveller"
    __description__ = "Edible NipNip Buds: the colours of the world flow while they last"
    __version__ = "1.1"

    def __init__(self):
        super().__init__()
        global _instance
        _instance = self
        self._mgr = 0  # cGcFullscreenEffectManager*, captured from the game's own SetScreenFilter calls
        self._program = None
        self._program_t0 = 0.0
        self._applied = None  # (filter, strength, distance) last passed to SetScreenFilter for the Effect slot
        self._storm = None  # (filter, strength, distance) forced into the Storm slot, None = the game's own
        self._step_state = None
        self._buds_until = 0.0  # monotonic time the current bud effect ends, 0 = no bud effect
        self._retired = False
        self._take_over()
        off_ = find_pattern_in_binary(SET_SCREEN_FILTER_SIG, False)
        self._set_filter = None
        if off_ is None:
            logger.error("SetScreenFilter not found - NipNip can't switch filters on this build")
        else:
            self._set_filter = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_int32, ctypes.c_int32,
                                                ctypes.c_float, ctypes.c_float)(_internal.BASE_ADDRESS + off_)
            logger.info("NipNip %s loaded; SetScreenFilter at NMS+0x%X", self.__version__, off_)

    @manual_hook("cGcFullscreenEffectManager::SetScreenFilter", pattern=SET_SCREEN_FILTER_SIG,
                 func_def=FUNCDEF(None, [ctypes.c_void_p, ctypes.c_int32, ctypes.c_int32, ctypes.c_float,
                                         ctypes.c_float]), detour_time="before")
    def on_set_screen_filter(self, this, filt, slot, strength, distance):
        if self._retired:
            return
        if this:
            self._mgr = int(this)  # cGcSky calls this every frame for the Far and Storm slots
        storm = self._storm
        if storm is not None and slot == SLOT_STORM:
            return this, storm[0], SLOT_STORM, storm[1], storm[2]  # our second layer instead of the weather's

    def _take_over(self):
        """A pyMHF hot reload can leave the old instance's cGcPlayer::Update hook attached, so retire the older
        NipNip and carry the running effect over to this one."""
        prev = mod_manager.mods.get("NipNip")
        if prev is None or prev is self:
            return
        for name in ("_mgr", "_program", "_program_t0", "_applied", "_storm", "_step_state", "_buds_until"):
            if hasattr(prev, name):
                setattr(self, name, getattr(prev, name))
        prev._retired = True
        prev._program = None
        prev._storm = None
        if self._program is not None:
            logger.info("hot reload: took over the running effect")

    def _manager(self) -> int:
        if self._mgr:
            return self._mgr
        if not APP_DATA_SLOT or not DATA_TO_FX_MANAGER:
            return 0
        data = safe_read(_internal.BASE_ADDRESS + APP_DATA_SLOT, ctypes.c_uint64)
        return data + DATA_TO_FX_MANAGER if data else 0

    # ------------------------------------------------------------------- the item ----
    @manual_hook("NipNip consume (product popup)", pattern=CONSUME_POPUP["sig"], func_def=FUNCDEF(None, [ctypes.c_void_p]),
                 detour_time="before")
    def on_consume_popup(self, cap):
        self._on_consume(cap, CONSUME_POPUP)

    @manual_hook("NipNip consume (inventory grid)", pattern=CONSUME_GRID["sig"], func_def=FUNCDEF(None, [ctypes.c_void_p]),
                 detour_time="before")
    def on_consume_grid(self, cap):
        self._on_consume(cap, CONSUME_GRID)

    def _on_consume(self, cap, layout):
        """Runs on a job thread: only read and queue."""
        if self._retired or not cap:
            return
        try:
            cap = int(cap)
            rid = _fixed_string(cap)
            product = _fixed_string(cap + layout["product"])
            ok = safe_read(cap + layout["can_afford"], ctypes.c_uint8)
        except Exception:
            logger.exception("could not read the consume job")
            return
        is_bud = rid == BUDS_REWARD
        logger.log(logging.INFO if is_bud else logging.DEBUG, "consumed %s -> reward %s%s",
                   product.decode(errors="replace"), rid.decode(errors="replace"),
                   "" if ok else " (can't afford, nothing happens)")
        if is_bud and ok:
            _jobs.put(("_eat_bud", ()))

    def _eat_bud(self):
        now = time.monotonic()
        if self._buds_until > now and self._program is not None:
            self._buds_until = min(now + BUDS_MAX_SECONDS, self._buds_until + BUDS_SECONDS)
            logger.info("another bud: the flow now lasts %.0f s more", self._buds_until - now)
            return
        self._start(prog_flow([filter_id(n) for n in VIVID], FLOW_XFADE, BUDS_SHUFFLE, BUDS_DISTANCE),
                    "NipNip bud: the colours start to flow")
        self._buds_until = now + BUDS_SECONDS

    # ----------------------------------------------------------------- game thread ----
    @nms.cGcPlayer.Update.before
    def game_tick(self, this, lfStep: float):
        if self._retired:
            return
        while True:
            try:
                method, args = _jobs.get_nowait()
            except queue.Empty:
                break
            try:
                getattr(self, method)(*args)
            except Exception:
                logger.exception("NipNip job %s failed", method)
        if self._buds_until and time.monotonic() >= self._buds_until:
            self._buds_until = 0.0
            logger.info("the buds wear off")
            self._stop(BUDS_FADE_OUT)
        if self._program is not None:
            try:
                want = self._program(time.monotonic() - self._program_t0)
            except Exception:
                logger.exception("NipNip program failed; switching the effect off")
                want = None
            if want is None:
                self._program = None
                self._apply((0, 0.0, 0.0), None)
                logger.info("effect finished, colours back to normal")
            elif len(want) == 3:
                self._apply(want, None)
            else:
                self._apply(*want)

    def _start(self, program, label: str):
        self._program, self._program_t0 = program, time.monotonic()
        self._step_state = None
        self._buds_until = 0.0
        logger.info("start: %s", label)

    def _step(self, fid: int, xfade: float, distance: float):
        cur = getattr(self, "_step_state", None)
        if cur is not None and self._program is None:
            cur = None  # something switched the effect off in between
        prog = prog_step(cur, fid, xfade, distance)
        self._program, self._program_t0 = prog, time.monotonic()
        self._step_state = (fid, distance, prog.slot)
        logger.info("step: %s", filter_name(fid))

    def _stop(self, fade: float):
        eff = self._applied if self._applied and self._applied[1] > 0 else None
        if (eff or self._storm) and fade > 0:
            self._program, self._program_t0 = prog_fade_out((eff, self._storm), fade), time.monotonic()
        else:
            self._program = None
            self._apply((0, 0.0, 0.0), None)
        self._step_state = None
        self._buds_until = 0.0
        logger.info("stop (fade %.1fs)", fade)

    def _apply(self, effect, storm):
        mgr = self._manager()
        if not mgr or not self._set_filter:
            return
        if effect is None:
            effect = (self._applied[0] if self._applied else 0, 0.0, 0.0)
        want = (int(effect[0]), round(float(effect[1]), 3), float(effect[2]))
        # Re-apply too when someone else changed the slot (the binoculars restore their saved filter).
        if want != self._applied or \
                safe_read(mgr + MGR_ACTIVE_FILTERS + 4 * SLOT_EFFECT, ctypes.c_int32) != want[0]:
            self._set_filter(mgr, want[0], SLOT_EFFECT, want[1], want[2])
            if self._applied is None or self._applied[0] != want[0]:
                logger.debug("effect slot -> %s (strength %.2f, distance %.0f)", filter_name(want[0]), want[1],
                             want[2])
            self._applied = want
        if storm is not None:
            storm = (int(storm[0]), round(float(storm[1]), 3), float(storm[2]))
        if storm != self._storm:
            if storm is not None and (self._storm is None or self._storm[0] != storm[0]):
                logger.debug("storm slot -> %s (strength %.2f)", filter_name(storm[0]), storm[1])
            elif storm is None:
                logger.debug("storm slot back to the weather")
            self._storm = storm
            if storm is not None:  # the hook keeps it there when cGcSky writes the slot next frame
                self._set_filter(mgr, storm[0], SLOT_STORM, storm[1], storm[2])
