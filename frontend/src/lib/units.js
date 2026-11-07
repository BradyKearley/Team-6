// The server, towers.json and firmware all use meters. The UI shows feet.
// Convert at the edges: toFt() for anything displayed, toM() for anything sent.

export const M_PER_FT = 0.3048;

export const toFt = (m) => (m == null ? null : m / M_PER_FT);
export const toM = (ft) => (ft == null ? null : ft * M_PER_FT);

/** Format a meter value as feet, e.g. fmtFt(3) -> "9.8 ft". */
export const fmtFt = (m, digits = 1, unit = true) =>
  m == null || !isFinite(m) ? '—' : `${(m / M_PER_FT).toFixed(digits)}${unit ? ' ft' : ''}`;

/** Round a meter value to the nearest `stepFt` feet, returning meters. */
export const snapFt = (m, stepFt = 0.5) => Math.round(m / M_PER_FT / stepFt) * stepFt * M_PER_FT;
