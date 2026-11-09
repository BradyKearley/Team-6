// REST calls to the Python server (proxied by Vite in dev, same-origin in prod).

async function req(method, path, body) {
  const res = await fetch(path, {
    method,
    headers: body ? { 'Content-Type': 'application/json' } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      detail = (await res.json()).detail ?? detail;
    } catch {}
    throw new Error(typeof detail === 'string' ? detail : JSON.stringify(detail));
  }
  return res.json();
}

// Multipart, for the one endpoint that takes a file (the star camera photo upload).
async function upload(path, file, fields = {}) {
  const body = new FormData();
  body.append('file', file);
  for (const [k, v] of Object.entries(fields)) if (v != null && v !== '') body.append(k, String(v));
  const res = await fetch(path, { method: 'POST', body });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      detail = (await res.json()).detail ?? detail;
    } catch {}
    throw new Error(typeof detail === 'string' ? detail : JSON.stringify(detail));
  }
  return res.json();
}

export const api = {
  towers: () => req('GET', '/api/towers'),
  createTower: (t) => req('POST', '/api/towers', t),
  updateTower: (name, t) => req('PUT', `/api/towers/${encodeURIComponent(name)}`, t),
  deleteTower: (name) => req('DELETE', `/api/towers/${encodeURIComponent(name)}`),
  calibrate: (name, known_distance, seconds = 3) =>
    req('POST', `/api/towers/${encodeURIComponent(name)}/calibrate`, { known_distance, seconds }),
  clearCalibration: (name) => req('DELETE', `/api/towers/${encodeURIComponent(name)}/calibrate`),
  resetTrack: () => req('POST', '/api/track/reset'),
  setSimSpeed: (speed) => req('POST', '/api/sim', { speed }),
  setSource: (kind, port = null) => req('POST', '/api/source', { kind, port }),
  ports: () => req('GET', '/api/ports'),
  measure: (seconds = 3) => req('POST', '/api/measure', { seconds }),
  // calibration sweep (distances in meters)
  sweep: () => req('GET', '/api/sweep'),
  sweepRecord: (known_distance, seconds = 6) => req('POST', '/api/sweep/record', { known_distance, seconds }),
  sweepUndo: () => req('DELETE', '/api/sweep/last'),
  sweepClear: () => req('DELETE', '/api/sweep'),
  sweepApply: () => req('POST', '/api/sweep/apply'),
  // LEO satellite Doppler simulation (server/app/leo_sat.py)
  leoInfo: (refresh = false) => req('GET', `/api/leo/info${refresh ? '?refresh=true' : ''}`),
  leoSimulate: (params) => req('POST', '/api/leo/simulate', params),
  fusionSimulate: (params) => req('POST', '/api/fusion/simulate', params),
  // Star camera: a photo of the night sky -> lat/lon (server/app/star_camera.py)
  starInfo: () => req('GET', '/api/star/info'),
  starSampleUrl: (key) => `/api/star/sample/${encodeURIComponent(key)}`,
  starSolveSample: (key, opt = {}) => req('POST', '/api/star/sample', { key, ...opt }),
  starSolveUpload: (file, fields = {}) => upload('/api/star/solve', file, fields),
  starCapture: (opt = {}) => req('POST', '/api/star/capture', opt),
  starCamera: () => req('GET', '/api/star/camera'),
  setStarCamera: (cfg) => req('POST', '/api/star/camera', cfg),
  // RL autopilot replays, recorded by flightsim/fly.py
  flightList: () => req('GET', '/api/flight/list'),
  flightReplay: (name = 'fused') => req('GET', `/api/flight/replay?name=${encodeURIComponent(name)}`),
  flightNetwork: (name = 'fused') => req('GET', `/api/flight/network?name=${encodeURIComponent(name)}`),
  // the same policy flown live by the server (flightsim/live.py)
  flightScenarios: () => req('GET', '/api/flight/scenarios'),
  flightLive: (params) => req('POST', '/api/flight/live', params),
  flightBatch: (params) => req('POST', '/api/flight/batch', params),
};
