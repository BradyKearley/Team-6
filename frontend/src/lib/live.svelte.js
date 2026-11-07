// Live state pushed by the server over /ws (10 times a second), with auto-reconnect.
// Usage: import { live } from './live.svelte.js'; then read live.data / live.connected.

class Live {
  connected = $state(false);
  // $state.raw: the snapshot is replaced whole every message and never edited,
  // so Svelte doesn't need to wrap every nested object.
  data = $state.raw(null);
}
export const live = new Live();

function connect() {
  const proto = location.protocol === 'https:' ? 'wss' : 'ws';
  const ws = new WebSocket(`${proto}://${location.host}/ws`);
  ws.onopen = () => (live.connected = true);
  ws.onmessage = (e) => (live.data = JSON.parse(e.data));
  ws.onclose = () => {
    live.connected = false;
    setTimeout(connect, 1000); // try again in a second
  };
  ws.onerror = () => ws.close();
}

connect();
