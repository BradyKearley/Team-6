<script>
  /**
   * The policy network, drawn as it changed over training.
   *
   * Its shape never changes - the growth you see is every connection's strength coming up
   * from its initial value. The output layer starts at almost exactly zero on purpose
   * (orthogonal init with gain 0.01), so the controls genuinely fade in from nothing.
   *
   * Edges are drawn on a canvas: ~500 lines re-drawn per animation frame is nothing for
   * canvas and would be painful as 500 reactive SVG elements.
   */
  import { untrack } from 'svelte';

  let { net, onclose } = $props();

  const POS = '#2f81f7'; // a connection that excites
  const NEG = '#db6d28'; // one that inhibits

  let playing = $state(true);
  let t = $state(0); // position in the movie, in frames (fractional = mid-morph)
  let seek = $state(0);
  let canvas = $state();
  let box = $state({ w: 900, h: 520 });

  let frames = $derived(net?.frames ?? []);
  let last = $derived(Math.max(frames.length - 1, 0));
  // t is a float the animation writes every frame, so every read of it is clamped.
  let i = $derived(Math.min(Math.max(Math.floor(t) || 0, 0), last));
  let mix = $derived(Math.min(Math.max(t - i, 0), 1));
  let now = $derived(frames[i] ?? frames[last] ?? { step: 0, return: 0, w1: [[0]], w2: [[0]], w3: [[0]] });
  let nxt = $derived(frames[Math.min(i + 1, last)] ?? now);
  let step = $derived(Math.round(now.step + (nxt.step - now.step) * mix));
  let ret = $derived(now.return + (nxt.return - now.return) * mix);

  // One scale per layer, taken over the whole movie, so a frame's faintness is meaningful.
  let scale = $derived.by(() =>
    ['w1', 'w2', 'w3'].map(
      (k) => Math.max(...frames.map((f) => Math.max(...f[k].flat().map(Math.abs))), 1e-6) || 1,
    ),
  );
  const mean = (m) => m.flat().reduce((a, b) => a + Math.abs(b), 0) / Math.max(m.flat().length, 1);
  let strength = $derived(['w1', 'w2', 'w3'].map((k) => mean(now[k])));

  $effect(() => {
    if (!playing || frames.length < 2) return;
    seek;
    let pos = untrack(() => t);
    let prev = performance.now();
    let raf = requestAnimationFrame(function tick(ts) {
      const dt = (ts - prev) / 1000;
      prev = ts;
      pos += dt * 6; // six snapshots a second
      if (pos >= last) {
        pos = last;
        playing = false; // stop on the finished network
      }
      t = pos;
      raf = requestAnimationFrame(tick);
    });
    return () => cancelAnimationFrame(raf);
  });

  /** Weight matrices for the moment between two snapshots. */
  function lerp(key) {
    const a = now[key];
    const b = nxt[key] ?? a;
    return a.map((row, r) => row.map((v, c) => v + ((b[r]?.[c] ?? v) - v) * mix));
  }

  function draw() {
    if (!canvas || !frames.length) return;
    const dpr = window.devicePixelRatio || 1;
    const { w, h } = box;
    canvas.width = w * dpr;
    canvas.height = h * dpr;
    const g = canvas.getContext('2d');
    g.setTransform(dpr, 0, 0, dpr, 0, 0);
    g.clearRect(0, 0, w, h);

    const W = [lerp('w1'), lerp('w2'), lerp('w3')];
    const sizes = [net.input_labels.length, net.hidden[0], net.hidden[1], net.output_labels.length];
    const padL = 132, padR = 118, padY = 26;
    const cols = sizes.map((_, c) => padL + ((w - padL - padR) * c) / (sizes.length - 1));
    const ys = sizes.map((n) => Array.from({ length: n }, (_, k) => padY + ((h - 2 * padY) * (k + 0.5)) / n));

    // edges, faint first so the strong ones sit on top
    for (let l = 0; l < 3; l++) {
      const m = W[l]; // [to][from]
      const s = scale[l];
      const order = [];
      for (let to = 0; to < m.length; to++)
        for (let from = 0; from < m[to].length; from++) order.push([Math.abs(m[to][from]), to, from]);
      order.sort((a, b) => a[0] - b[0]);
      for (const [mag, to, from] of order) {
        const a = mag / s;
        if (a < 0.03) continue;
        g.strokeStyle = m[to][from] >= 0 ? POS : NEG;
        g.globalAlpha = Math.min(0.08 + a * 0.75, 0.85);
        g.lineWidth = 0.4 + a * 2.1;
        g.beginPath();
        g.moveTo(cols[l], ys[l][from]);
        g.lineTo(cols[l + 1], ys[l + 1][to]);
        g.stroke();
      }
    }
    g.globalAlpha = 1;

    // nodes
    for (let c = 0; c < sizes.length; c++) {
      for (let k = 0; k < sizes[c]; k++) {
        g.beginPath();
        g.arc(cols[c], ys[c][k], c === 0 || c === 3 ? 4.5 : 3.5, 0, Math.PI * 2);
        g.fillStyle = c === 0 ? '#8b949e' : c === 3 ? '#f0883e' : '#4a5a6e';
        g.fill();
      }
    }

    // labels at the meaningful ends
    g.font = '10px ui-monospace, monospace';
    g.fillStyle = '#8b949e';
    g.textAlign = 'right';
    net.input_labels.forEach((lab, k) => g.fillText(lab, cols[0] - 9, ys[0][k] + 3));
    g.textAlign = 'left';
    g.fillStyle = '#f0883e';
    net.output_labels.forEach((lab, k) => g.fillText(lab, cols[3] + 9, ys[3][k] + 3));
    g.textAlign = 'center';
    g.fillStyle = '#6b7684';
    ['inputs', `hidden 1 (${net.hidden[0]} of ${net.full_hidden})`, `hidden 2 (${net.hidden[1]} of ${net.full_hidden})`, 'controls']
      .forEach((lab, c) => g.fillText(lab, cols[c], h - 6));
  }

  $effect(() => {
    t;
    box;
    net;
    draw();
  });
</script>

<div class="wrap" role="dialog" aria-label="The policy network over training">
  <header>
    <h2>Watching the policy learn</h2>
    <span class="mono">{step.toLocaleString()} steps · mean return {ret.toFixed(1)}</span>
    <button class="close" onclick={onclose} aria-label="Close">✕</button>
  </header>

  <div class="stage" bind:clientWidth={box.w} bind:clientHeight={box.h}>
    <canvas bind:this={canvas} style:width={`${box.w}px`} style:height={`${box.h}px`}></canvas>
  </div>

  <div class="controls">
    <button
      class="play"
      onclick={() => {
        if (t >= last) t = 0;
        playing = !playing;
        seek++;
      }}>{playing ? '❚❚' : '▶'}</button
    >
    <input
      type="range"
      min="0"
      max={last}
      step="0.01"
      value={t}
      aria-label="Training progress"
      oninput={(e) => {
        t = +e.currentTarget.value;
        playing = false;
        seek++;
      }}
    />
    <button
      onclick={() => {
        t = last;
        playing = false;
        seek++;
      }}>Final form</button
    >
    <span class="key">
      <i style:background={POS}></i>excites <i style:background={NEG}></i>inhibits
    </span>
  </div>

  <p class="note">
    The shape never changes: {net.input_labels.length} inputs, two layers of {net.full_hidden} (the
    {net.hidden[0]} that drive the controls hardest are drawn), three commands. What grows is the strength of each
    connection — the output layer is deliberately initialised at almost zero, so the controls fade in from nothing as it
    learns what to do. Average weight now: {strength.map((v) => v.toFixed(2)).join(' · ')}.
  </p>
</div>

<style>
  .wrap {
    position: absolute;
    inset: 16px;
    z-index: 20;
    display: flex;
    flex-direction: column;
    background: rgba(10, 15, 24, 0.97);
    border: 1px solid var(--border);
    border-radius: 12px;
    box-shadow: 0 20px 60px rgba(0, 0, 0, 0.6);
  }
  header {
    display: flex;
    align-items: baseline;
    gap: 14px;
    padding: 10px 14px;
    border-bottom: 1px solid var(--border);
  }
  h2 {
    margin: 0;
    font-size: 13px;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    color: var(--muted);
  }
  header span {
    font-size: 12px;
    color: var(--text);
  }
  .close {
    margin-left: auto;
    padding: 2px 9px;
  }
  .stage {
    flex: 1;
    min-height: 0;
    position: relative;
  }
  canvas {
    display: block;
  }
  .controls {
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 8px 14px;
    border-top: 1px solid var(--border);
  }
  .play {
    width: 34px;
    font-size: 11px;
  }
  input[type='range'] {
    flex: 1;
    accent-color: var(--accent);
    padding: 0;
  }
  .key {
    display: flex;
    align-items: center;
    gap: 6px;
    font-size: 11px;
    color: var(--muted);
  }
  .key i {
    width: 14px;
    height: 2px;
    display: inline-block;
  }
  .note {
    margin: 0;
    padding: 0 14px 10px;
    font-size: 11px;
    line-height: 1.5;
    color: var(--muted);
  }
</style>
