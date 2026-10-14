"""ISO 226:2023 loudness compensation (port of ISO226Contours + LoudnessCompensator fit).

The engine runs a fixed four-section topology (low shelf 80 Hz, bell 180 Hz,
bell 3.2 kHz, high shelf 10 kHz). This module computes the section gains that
best fit the ISO-derived target curve for the current listening level; the
engine turns them into biquad coefficients at the stream's sample rate.
"""
from __future__ import annotations

import math

FREQUENCIES = [20, 25, 31.5, 40, 50, 63, 80, 100, 125, 160, 200, 250, 315, 400, 500, 630, 800,
               1000, 1250, 1600, 2000, 2500, 3150, 4000, 5000, 6300, 8000, 10000, 12500]
ALPHA_F = [0.635, 0.602, 0.569, 0.537, 0.509, 0.482, 0.456, 0.433, 0.412, 0.391, 0.373, 0.357, 0.343,
           0.330, 0.320, 0.311, 0.303, 0.300, 0.295, 0.292, 0.290, 0.290, 0.289, 0.289, 0.289, 0.293,
           0.303, 0.323, 0.354]
LU = [-31.5, -27.2, -23.1, -19.3, -16.1, -13.1, -10.4, -8.2, -6.3, -4.6, -3.2, -2.1, -1.2, -0.5, 0.0,
      0.4, 0.5, 0.0, -2.7, -4.2, -1.2, 1.4, 2.3, 1.0, -2.3, -7.2, -11.2, -10.9, -3.5]
TF = [78.1, 68.7, 59.5, 51.1, 44.0, 37.5, 31.5, 26.5, 22.1, 17.9, 14.4, 11.4, 8.6, 6.2, 4.4, 3.0, 2.2,
      2.4, 3.5, 1.7, -1.3, -4.2, -6.0, -5.4, -1.5, 6.0, 12.6, 13.9, 12.3]
ALPHA_R = 0.300
DEFAULT_REFERENCE_PHON = 80.0
_REF_INDEX = 17
_P0_SQ = 4e-10

SECTIONS = [("L", 80.0, 0.707), ("P", 180.0, 0.7), ("P", 3200.0, 0.7), ("H", 10000.0, 0.85)]
_FIT_POINTS = 96
_FIT_ITERATIONS = 3


def estimated_phon(system_volume: float) -> float:
    v = max(0.0, min(1.0, float(system_volume)))
    return 20.0 + (DEFAULT_REFERENCE_PHON - 20.0) * math.pow(v, 0.5)


def contour_spl(phon: float) -> list[float]:
    p = min(max(phon, 20.0), 90.0)
    ref_t = TF[_REF_INDEX]
    out = []
    for af, lu, tf in zip(ALPHA_F, LU, TF):
        exc = (math.pow(_P0_SQ, ALPHA_R - af)
               * (math.pow(10.0, ALPHA_R * p / 10.0) - math.pow(10.0, ALPHA_R * ref_t / 10.0))
               + math.pow(10.0, ALPHA_R * (tf + lu) / 10.0))
        out.append((10.0 / af) * math.log10(exc) - lu)
    return out


def compensation_gains(phon: float, reference_phon: float = DEFAULT_REFERENCE_PHON, amount: float = 1.0,
                       max_gain_db: float = float("inf")) -> list[float]:
    amount = min(max(amount, 0.0), 1.0)
    ref = contour_spl(reference_phon)
    cur = contour_spl(phon)
    r1k, c1k = ref[_REF_INDEX], cur[_REF_INDEX]
    return [max(-max_gain_db, min(max_gain_db, ((c - c1k) - (r - r1k)) * amount)) for r, c in zip(ref, cur)]


def interpolate_compensation(gains: list[float], frequency: float) -> float:
    if len(gains) != len(FREQUENCIES):
        return 0.0
    lf = math.log(frequency)
    lfs = [math.log(f) for f in FREQUENCIES]
    if lf <= lfs[0]:
        return gains[0]
    if lf >= lfs[-1]:
        return gains[-1]
    lo = 0
    for i in range(len(lfs) - 1):
        if lfs[i + 1] >= lf:
            lo = i
            break
    t = (lf - lfs[lo]) / (lfs[lo + 1] - lfs[lo])
    return gains[lo] + t * (gains[lo + 1] - gains[lo])


# --------------------------------------------------------------- biquad math

def peaking(f, g, q, sr):
    A = 10 ** (g / 40)
    w = 2 * math.pi * f / sr
    alpha = math.sin(w) / (2 * q)
    a0 = 1 + alpha / A
    return [(1 + alpha * A) / a0, -2 * math.cos(w) / a0, (1 - alpha * A) / a0, -2 * math.cos(w) / a0, (1 - alpha / A) / a0]


def low_shelf(f, g, q, sr):
    A = 10 ** (g / 40)
    w = 2 * math.pi * f / sr
    cs = math.cos(w)
    t = 2 * math.sqrt(A) * math.sin(w) / (2 * q)
    a0 = (A + 1) + (A - 1) * cs + t
    return [A * ((A + 1) - (A - 1) * cs + t) / a0, 2 * A * ((A - 1) - (A + 1) * cs) / a0,
            A * ((A + 1) - (A - 1) * cs - t) / a0, -2 * ((A - 1) + (A + 1) * cs) / a0,
            ((A + 1) + (A - 1) * cs - t) / a0]


def high_shelf(f, g, q, sr):
    A = 10 ** (g / 40)
    w = 2 * math.pi * f / sr
    cs = math.cos(w)
    t = 2 * math.sqrt(A) * math.sin(w) / (2 * q)
    a0 = (A + 1) - (A - 1) * cs + t
    return [A * ((A + 1) + (A - 1) * cs + t) / a0, -2 * A * ((A - 1) + (A + 1) * cs) / a0,
            A * ((A + 1) + (A - 1) * cs - t) / a0, 2 * ((A - 1) - (A + 1) * cs) / a0,
            ((A + 1) - (A - 1) * cs - t) / a0]


def coefficients(kind, f, g, q, sr):
    if f >= sr / 2:
        return [1.0, 0.0, 0.0, 0.0, 0.0]
    return {"L": low_shelf, "H": high_shelf}.get(kind, peaking)(f, g, q, sr)


def magnitude_db(coefs: list[list[float]], f: float, sr: float) -> float:
    w = 2 * math.pi * f / sr
    cw, sw, c2, s2 = math.cos(w), math.sin(w), math.cos(2 * w), math.sin(2 * w)
    mag = 1.0
    for b0, b1, b2, a1, a2 in coefs:
        nr, ni = b0 + b1 * cw + b2 * c2, -(b1 * sw + b2 * s2)
        dr, di = 1 + a1 * cw + a2 * c2, -(a1 * sw + a2 * s2)
        mag *= math.sqrt((nr * nr + ni * ni) / max(dr * dr + di * di, 1e-30))
    return 20 * math.log10(max(mag, 1e-12))


def _grid():
    return [20.0 * math.pow(1000.0, i / (_FIT_POINTS - 1)) for i in range(_FIT_POINTS)]


def _solve(m, rhs):
    n = len(rhs)
    a = [row[:] + [rhs[i]] for i, row in enumerate(m)]
    for p in range(n):
        best = max(range(p, n), key=lambda r: abs(a[r][p]))
        if abs(a[best][p]) <= 1e-12:
            return None
        a[p], a[best] = a[best], a[p]
        piv = a[p][p]
        for c in range(p, n + 1):
            a[p][c] /= piv
        for r in range(n):
            if r != p and a[r][p] != 0:
                fct = a[r][p]
                for c in range(p, n + 1):
                    a[r][c] -= fct * a[p][c]
    return [a[i][n] for i in range(n)]


def fitted_section_gains(phon: float, sample_rate: float = 48000.0) -> list[float]:
    grid = _grid()
    comp = compensation_gains(phon)
    target = [interpolate_compensation(comp, f) for f in grid]
    basis = [[magnitude_db([coefficients(k, f0, 1.0, q, sample_rate)], f, sample_rate) for f in grid]
             for (k, f0, q) in SECTIONS]
    n = len(SECTIONS)
    gram = [[sum(x * y for x, y in zip(basis[r], basis[c])) for c in range(n)] for r in range(n)]
    gains = [0.0] * n
    for _ in range(_FIT_ITERATIONS):
        coefs = [coefficients(k, f0, g, q, sample_rate) for (k, f0, q), g in zip(SECTIONS, gains)]
        realized = [magnitude_db(coefs, f, sample_rate) for f in grid]
        residual = [t - r for t, r in zip(target, realized)]
        rhs = [sum(b * r for b, r in zip(basis[i], residual)) for i in range(n)]
        delta = _solve(gram, rhs)
        if delta is None:
            break
        gains = [g + d for g, d in zip(gains, delta)]
    return gains


_cache: dict = {}


def section_gains_for_volume(volume: float, sample_rate: float = 48000.0):
    """Returns (enabled, gains). Coalesces to 1-phon steps like the macOS compensator."""
    phon = round(estimated_phon(volume))
    key = (phon, int(sample_rate))
    if key not in _cache:
        g = fitted_section_gains(float(phon), sample_rate)
        _cache[key] = g
    g = _cache[key]
    enabled = any(abs(x) >= 0.1 for x in g)
    return enabled, g
