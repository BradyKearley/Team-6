// Small helpers for tower names. Default height and calibration for new
// towers live on the server (server/app/towers.py), so the UI only sends name + position.

/** First unused letter A-Z, or T6, T7... once those run out. */
export function nextTowerName(towers) {
  const used = new Set(towers.map((t) => t.name));
  for (const letter of 'ABCDEFGHIJKLMNOPQRSTUVWXYZ') {
    if (!used.has(letter)) return letter;
  }
  return `T${towers.length + 1}`;
}
