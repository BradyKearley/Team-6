<script>
  /**
   * Star Camera: one photo of the night sky, one position fix on the globe.
   *
   * The server does the work (server/app/star_camera.py): tetra3 matches the star
   * pattern to a catalogue and reports where the lens was aimed, and because the lens
   * was aimed at the zenith that direction *is* the point overhead - declination is the
   * latitude, and right ascension against sidereal time is the longitude.
   *
   * Three ways in, all landing on the same solver: a stock image, a photo you upload, or
   * a live frame off the Raspberry Pi star camera once there is one to talk to.
   */
  import { Canvas } from '@threlte/core';
  import StarGlobe from '../scene/StarGlobe.svelte';
  import { api } from '../lib/api.js';
  import { hint as tip } from '../lib/hint.js';
  import PiConnect from '../lib/PiConnect.svelte';

  let { title } = $props();

  const C = { fix: '#4ea1ff', truth: '#3fb950' };

  let info = $state(null);
  let res = $state(null);
  let busy = $state('');          // what we are waiting on, '' when idle
  let error = $state('');
  let source = $state({ kind: 'sample', key: null, label: '' });
  let photoUrl = $state('');      // the image being solved, for the preview panel
  let objectUrl = null;           // revoked when it is replaced
  let tab = $state('photo');      // preview panel: the photo, or what the solver made of it
  let capturedAt = $state('');    // uploads only; blank = the server stamps it "now"
  let showSolver = $state(false);
  let showCamera = $state(false);
  let showCone = $state(true);
  let camera = $state({ url: '', timeout_s: 5, enabled: false });

  // Solver knobs. Left at null/defaults the server uses whatever suits the source, so
  // only what is actually changed here gets sent.
  const SOLVER_DEFAULTS = {
    fov_estimate: null,
    fov_max_error: null,
    rotate180: false,
    crop: 1,
    threshold: null,
    max_stars: 8,
    search_distortion: true,
  };
  let solver = $state({ ...SOLVER_DEFAULTS });

  let samples = $derived(info?.samples ?? []);
  let stock = $derived(samples.filter((s) => !s.real));
  let photos = $derived(samples.filter((s) => s.real));

  /** Only the knobs that differ from the defaults - the rest stay the source's own. */
  function solverOverrides() {
    const out = {};
    for (const [k, v] of Object.entries(solver)) if (v !== SOLVER_DEFAULTS[k] && v !== '') out[k] = v;
    return out;
  }

  function setPhoto(url, isObject = false) {
    if (objectUrl) URL.revokeObjectURL(objectUrl);
    objectUrl = isObject ? url : null;
    photoUrl = url;
  }

  async function run(label, fn) {
    busy = label;
    error = '';
    try {
      res = await fn();
    } catch (e) {
      error = e.message;
      res = null;
    } finally {
      busy = '';
    }
  }

  function pickSample(s) {
    source = { kind: 'sample', key: s.key, label: s.title };
    setPhoto(api.starSampleUrl(s.key));
    run(`Solving ${s.title}…`, () => api.starSolveSample(s.key, solverOverrides()));
  }

  function solveFile(file) {
    if (!file) return;
    source = { kind: 'upload', key: null, label: file.name };
    setPhoto(URL.createObjectURL(file), true);
    run('Solving your photo…', () =>
      api.starSolveUpload(file, { captured_at: capturedAt, ...solverOverrides() }),
    );
  }

  async function capture() {
    source = { kind: 'pi', key: null, label: 'Pi star camera' };
    setPhoto('');
    tab = 'photo';
    await run('Capturing from the Pi…', () => api.starCapture(solverOverrides()));
    if (!error) {
      setPhoto(api.starFrameUrl(false)); // the exact frame that was solved
      tab = res?.solved ? 'solver' : 'photo';
    }
  }

  /** Take one photo and just show it - the "is the camera working?" check. No solve. */
  async function testPhoto() {
    source = { kind: 'pi', key: null, label: 'Pi camera test photo' };
    busy = 'Taking a photo on the Pi…';
    error = '';
    try {
      const r = await fetch(api.starFrameUrl(true));
      if (!r.ok) {
        let detail = r.statusText;
        try {
          detail = (await r.json()).detail ?? detail;
        } catch {}
        throw new Error(detail);
      }
      setPhoto(URL.createObjectURL(await r.blob()), true);
      res = null;
      tab = 'photo';
    } catch (e) {
      error = e.message;
    } finally {
      busy = '';
    }
  }

  /** Re-solve whatever is loaded, with the current settings. Uploads are not re-solvable:
   *  the file never left the browser, so the button asks for it again instead. */
  function resolve() {
    if (source.kind === 'pi') capture();
    else if (source.key) pickSample(samples.find((s) => s.key === source.key));
  }

  let fileInput = $state(null);
  let dropping = $state(false);

  function onDrop(e) {
    e.preventDefault();
    dropping = false;
    solveFile(e.dataTransfer?.files?.[0]);
  }

  async function saveCamera(patch) {
    try {
      camera = await api.setStarCamera({ ...patch });
      error = '';
    } catch (e) {
      error = e.message;
    }
  }

  // Load the page: what the solver can do, then solve the first stock image so there is
  // something on the globe immediately.
  (async () => {
    busy = 'Loading the star catalogue…';
    try {
      info = await api.starInfo();
      camera = { ...info.camera };
      if (info.samples.length) {
        const first = info.samples.find((s) => !s.real) ?? info.samples[0];
        source = { kind: 'sample', key: first.key, label: first.title };
        setPhoto(api.starSampleUrl(first.key));
        res = await api.starSolveSample(first.key);
      }
    } catch (e) {
      error = e.message;
    } finally {
      busy = '';
    }
  })();

  $effect(() => () => objectUrl && URL.revokeObjectURL(objectUrl));

  // Why it failed, in terms you can act on.
  let why = $derived.by(() => {
    const e = error.toLowerCase();
    if (!e) return '';
    if (e.includes('failed to fetch'))
      return 'The Python server is not reachable. Start it in server/ with: python -m app.main --source sim';
    if (e.includes('not found') || e.includes('404'))
      return 'The server is running an older build without the star camera routes. Restart it: python -m app.main --source sim';
    if (e.includes('missing package') || e.includes('pillow'))
      return 'Install the server requirements first: pip install -r requirements.txt';
    if (e.includes('tetra3'))
      return 'The pattern database is the tetra3 checkout in server/test/ — its data/default_database.npz has to be there.';
    if (e.includes('pi camera') || e.includes('switched off'))
      return 'The Pi runs star-tracker/camera.py (Flask + Picamera2). Set its address under Pi star camera below.';
    if (e.includes('could not read that image'))
      return 'Try a PNG, JPEG or TIFF of the night sky.';
    return 'Check the server terminal for the traceback.';
  });

  // While a solve is in flight the newly-picked frame is all we have; once it lands the
  // server's thumbnail is the better one (its stars survive being shrunk).
  let framePhoto = $derived(busy ? photoUrl : res?.photo || photoUrl);

  let fix = $derived(res?.solved ? res.fix : null);
  let truth = $derived(res?.truth ?? null);
  let fovDeg = $derived(res?.attitude?.fov_deg ?? 20);

  // The shipped database only knows 10-30 degree fields of view; a narrower lens needs
  // its own database built, which is worth saying out loud rather than just failing.
  let fovWarning = $derived.by(() => {
    const db = info?.database;
    const f = res?.attitude?.fov_deg ?? solver.fov_estimate;
    if (!db || !f) return '';
    if (f < db.fov_min_deg || f > db.fov_max_deg)
      return `${f.toFixed(1)}° is outside the database's ${db.fov_min_deg}–${db.fov_max_deg}° range — build one for this lens with tetra3's generate_database().`;
    return '';
  });

  const km = (v) => (v == null ? '—' : v < 1 ? `${(v * 1000).toFixed(0)} m` : `${v.toFixed(2)} km`);
  const deg = (v, n = 4) => (v == null ? '—' : `${v.toFixed(n)}°`);

  // Where each number in the panels comes from.
  const WHY = {
    lat: 'The declination of the point overhead, straight off the photo. Latitude needs no clock — this is why a sextant works at sea with no radio at all.',
    lon: "The right ascension of the point overhead, minus how far the Earth has turned (sidereal time). This one is only as good as the capture time.",
    err: 'Great-circle distance from the fix to the position the stock image was generated for. Mostly centroiding error: one pixel of a 20° frame across 1024 px is about 2 km on the ground.',
    ra: 'Right ascension of the image centre — the celestial longitude the lens was aimed at.',
    dec: 'Declination of the image centre. With the camera pointed at the zenith this is the latitude, with no further work.',
    roll: 'How far the frame is rotated from celestial north. The attitude the star camera really contributes to the aircraft.',
    fov: 'Field of view the solver fitted across the frame. It does not have to be told this — the star pattern itself sets the scale.',
    matches: 'Catalogue stars matched to detected ones. Four is the minimum pattern; more means a more certain fix and a tighter residual.',
    centroids: 'Blobs the centroider found before any matching. If this is high and matches is zero, it is finding background, not stars — raise the threshold.',
    rmse: 'How far the matched catalogue stars sit from where the solution says they should, in arcseconds. Small means the fit is real.',
    prob: 'The solver\'s own estimate of the chance this pattern matched by luck. 1e-16 is not luck.',
    gmst: 'Greenwich sidereal time at the capture instant: how far the Earth had turned, measured against the stars rather than the Sun.',
    at: 'The instant the shutter opened, UTC. The Earth turns 0.0042° of longitude per second, so a second of clock error moves the answer about 460 m at the equator. Latitude does not care.',
    solve: 'Time inside tetra3 for this frame — pattern lookup plus the fit. The 49 MB catalogue is already in memory.',
  };
</script>

<div class="page">
  <section
    class="view"
    class:dropping
    ondragover={(e) => {
      e.preventDefault();
      dropping = true;
    }}
    ondragleave={() => (dropping = false)}
    ondrop={onDrop}
    role="application"
    aria-label="Globe showing the position the photo solved to"
  >
    <Canvas>
      <StarGlobe {fix} {truth} {fovDeg} {showCone} />
    </Canvas>

    <div class="toolbar">
      <label class="chip"><input type="checkbox" bind:checked={showCone} /> Sight cone</label>
      {#if source.label}<span class="badge mono" style:--c={res?.solved ? C.fix : 'var(--border)'}>{source.label}</span>{/if}
    </div>

    <div class="hintbar">
      <span class="hint">
        The spike stands where the photo says you are, pointing the way the lens looked · drag to orbit, scroll to zoom
      </span>
    </div>

    <!-- The photo, and what the solver made of it. -->
    <div class="shot">
      <div class="seg" role="group" aria-label="Preview">
        <button class:on={tab === 'photo'} onclick={() => (tab = 'photo')} disabled={!framePhoto}>Photo</button>
        <button class:on={tab === 'solver'} onclick={() => (tab = 'solver')} disabled={!res}>Solver</button>
      </div>
      <div class="frame">
        {#if tab === 'photo' && framePhoto}
          <img class="photo" src={framePhoto} alt="The night sky as it was captured" />
        {:else if tab === 'solver' && res?.preview}
          <img src={res.preview} alt="The stars the solver detected and matched" />
        {:else}
          <p class="empty">{busy || 'No image loaded.'}</p>
        {/if}
      </div>
      <p class="caption">
        {tab === 'photo'
          ? 'The frame as it came in. Stars are fattened and brightened, or they would vanish at this size.'
          : 'What the solver saw: white dots detected, green circles matched to the catalogue, red circles missed.'}
      </p>
    </div>

    {#if busy}<p class="loading">{busy}</p>{/if}
    {#if error}
      <div class="error" role="alert"><b>{error}</b><span>{why}</span></div>
    {/if}
  </section>

  <aside>
    <section>
      <h2>{title}</h2>
      <p class="lede">
        A camera pointed straight up, one exposure, and a star catalogue. Match the pattern and you know which way the
        lens was aimed; because it was aimed at the zenith, that direction is the point directly overhead — which is a
        latitude and, given the time, a longitude. Nothing transmits, so there is nothing to jam.
      </p>
      <ul class="key">
        <li><i class="dot" style:background={C.fix}></i>solved position</li>
        {#if truth}<li><i class="dot" style:background={C.truth}></i>true position</li>{/if}
      </ul>
    </section>

    <section>
      <h2>Stock images</h2>
      <p class="sub">Rendered from the same catalogue the solver matches against, each for a position we chose — so the fix has a right answer to miss by.</p>
      <div class="chips">
        {#each stock as s}
          <button class:on={source.key === s.key} onclick={() => pickSample(s)} disabled={!!busy} title={s.blurb}>
            {s.title}
          </button>
        {/each}
      </div>
      {#if photos.length}
        <p class="sub top">Real photographs, from the tetra3 project. Taken on a tripod aimed partway up the sky rather than at the zenith, so the position they give is not the photographer's — but the solve is the real thing.</p>
        <div class="chips">
          {#each photos as s}
            <button class:on={source.key === s.key} onclick={() => pickSample(s)} disabled={!!busy} title={s.blurb}>
              {s.title}
            </button>
          {/each}
        </div>
      {/if}
    </section>

    <section>
      <h2>Your own photo</h2>
      <input
        bind:this={fileInput}
        type="file"
        accept="image/*,.tif,.tiff"
        hidden
        onchange={(e) => solveFile(e.currentTarget.files?.[0])}
      />
      <button class="primary wide" onclick={() => fileInput?.click()} disabled={!!busy}>Upload a sky photo…</button>
      <p class="sub">Or drop one on the globe. Needs a wide enough field of view ({info?.database?.fov_min_deg ?? 10}–{info?.database?.fov_max_deg ?? 30}°) and stars down to about magnitude {info?.database?.star_max_magnitude ?? 7}.</p>
      <label class="stack">
        <span class="hinted" use:tip={WHY.at}>Capture time (UTC)</span>
        <input type="datetime-local" step="1" bind:value={capturedAt} />
      </label>
      <p class="sub">Blank means now. Get this wrong and the latitude still holds — only the longitude moves.</p>
    </section>

    {#if res}
      <section>
        <h2>Fix</h2>
        {#if res.solved}
          <div class="kv mono">
            <span class="hinted" use:tip={WHY.lat}>Latitude</span><span class="big" style:color={C.fix}>{deg(res.fix.lat, 5)}</span>
            <span class="hinted" use:tip={WHY.lon}>Longitude</span><span class="big" style:color={C.fix}>{deg(res.fix.lon, 5)}</span>
            {#if res.error_km != null}
              <span class="hinted" use:tip={WHY.err}>Off by</span><span class="big" style:color={C.truth}>{km(res.error_km)}</span>
              <span>True</span><span>{deg(res.truth.lat, 4)}, {deg(res.truth.lon, 4)}</span>
            {/if}
            <span class="hinted" use:tip={WHY.at}>Captured</span><span class="small">{res.captured_at}</span>
            <span class="hinted" use:tip={WHY.gmst}>Sidereal time</span><span>{deg(res.gmst_deg, 3)}</span>
          </div>
        {:else}
          <p class="nolock">
            <b>No lock.</b> {res.centroids} blob{res.centroids === 1 ? '' : 's'} found in the frame, no star pattern
            matched.
            {res.centroids < 4
              ? 'Fewer than four stars is not enough to form a pattern — a longer exposure, or a lower threshold.'
              : res.centroids > 60
                ? 'That many detections is usually background rather than stars — raise the threshold under Solver.'
                : 'Try widening the field-of-view search, or check the frame is really of the sky.'}
          </p>
        {/if}
      </section>

      <section>
        <h2>Solution</h2>
        <div class="kv mono">
          <span class="hinted" use:tip={WHY.ra}>Right ascension</span><span>{deg(res.attitude.ra_deg)}</span>
          <span class="hinted" use:tip={WHY.dec}>Declination</span><span>{deg(res.attitude.dec_deg)}</span>
          <span class="hinted" use:tip={WHY.roll}>Roll</span><span>{deg(res.attitude.roll_deg, 2)}</span>
          <span class="hinted" use:tip={WHY.fov}>Field of view</span><span>{deg(res.attitude.fov_deg, 3)}</span>
          <span class="hinted" use:tip={WHY.matches}>Stars matched</span><span>{res.quality.matches ?? '—'}</span>
          <span class="hinted" use:tip={WHY.centroids}>Blobs detected</span><span>{res.centroids}</span>
          <span class="hinted" use:tip={WHY.rmse}>Residual</span><span>{res.quality.rmse_arcsec == null ? '—' : `${res.quality.rmse_arcsec.toFixed(1)}″`}</span>
          <span class="hinted" use:tip={WHY.prob}>False-match odds</span><span>{res.quality.false_positive_prob == null ? '—' : res.quality.false_positive_prob.toExponential(0)}</span>
          <span class="hinted" use:tip={WHY.solve}>Solve time</span><span>{res.quality.solve_ms.toFixed(0)} ms</span>
        </div>
        {#if fovWarning}<p class="warn">{fovWarning}</p>{/if}
      </section>
    {/if}

    <section>
      <h2>Solver</h2>
      <button class="link" onclick={() => (showSolver = !showSolver)}>{showSolver ? 'Hide' : 'Show'} the knobs</button>
      {#if showSolver}
        <label title="Leave blank to search the whole range the database covers">
          Field of view <span class="u">deg</span>
          <input type="number" min="1" max="180" step="0.1" bind:value={solver.fov_estimate} placeholder="auto" />
        </label>
        <label title="How far from that estimate to accept a match">
          ± tolerance <span class="u">deg</span>
          <input type="number" min="0.1" max="90" step="0.1" bind:value={solver.fov_max_error} placeholder="auto" />
        </label>
        <label title="Drop every pixel at or below this before looking for stars (0-254). The Pi needs it; a clean frame does not.">
          Brightness cut
          <input type="number" min="0" max="254" step="5" bind:value={solver.threshold} placeholder="auto" />
        </label>
        <label title="Keep this much of the frame, centred - the Pi's tube vignettes the corners">
          Keep centre <span class="u">frac</span>
          <input type="number" min="0.1" max="1" step="0.05" bind:value={solver.crop} />
        </label>
        <label title="How many of the brightest stars get combined into candidate patterns. More is slower but finds thinner fields.">
          Pattern stars
          <input type="number" min="4" max="20" step="1" bind:value={solver.max_stars} />
        </label>
        <label class="check"><input type="checkbox" bind:checked={solver.rotate180} /> Lens inverts the frame</label>
        <label class="check"><input type="checkbox" bind:checked={solver.search_distortion} /> Fit lens distortion</label>
        <button class="wide" onclick={resolve} disabled={!!busy || source.kind === 'upload'}>
          {source.kind === 'upload' ? 'Re-upload to apply' : 'Solve again'}
        </button>
        <button class="wide" onclick={() => (solver = { ...SOLVER_DEFAULTS })} disabled={!!busy}>Reset the knobs</button>
      {/if}
    </section>

    <section>
      <h2>Pi star camera</h2>
      <p class="sub">
        Connect to the Pi below (same connection as the IMU page) and the camera address is filled in for you. Take a
        test photo first: it shows up in the preview, bottom right. Everything from the solve down is the same code the
        stock images use.
      </p>
      <button class="wide" onclick={testPhoto} disabled={!!busy || !camera.enabled}>Take a test photo</button>
      <button class="wide" onclick={capture} disabled={!!busy || !camera.enabled}>Capture and solve</button>
      {#if !camera.enabled}<p class="sub top">Connect to the Pi first, or set the address by hand below.</p>{/if}
      <button class="link" onclick={() => (showCamera = !showCamera)}>{showCamera ? 'Hide' : 'Show'} the address</button>
      {#if showCamera}
        <label class="stack">
          <span>Frame URL</span>
          <input type="text" bind:value={camera.url} placeholder="http://172.20.10.5:5000/frame" />
        </label>
        <label>Timeout <span class="u">s</span><input type="number" min="0.5" max="30" step="0.5" bind:value={camera.timeout_s} /></label>
        <label class="check"><input type="checkbox" bind:checked={camera.enabled} /> Camera is connected</label>
        <button class="wide" onclick={() => saveCamera(camera)}>Save address</button>
      {/if}
    </section>

    <PiConnect onchange={async () => (camera = { ...(await api.starCamera()) })} />

    <section>
      <p class="hint">
        The solver and the stock-image renderer are in <code>server/app/star_camera.py</code> — run it directly with
        <code>python -m app.star_camera --all</code> to solve every stock image in the terminal. Star patterns come from
        the tetra3 checkout in <code>server/test/</code>{#if info?.database}: {info.database.stars.toLocaleString()} stars,
        {(info.database.patterns / 1e6).toFixed(1)}M patterns, {info.database.catalog} catalogue{/if}.
      </p>
    </section>
  </aside>
</div>

<style>
  .page {
    display: flex;
    height: 100%;
  }
  .view {
    flex: 1;
    min-width: 0;
    position: relative;
    isolation: isolate;
    background: #070d16;
  }
  .view.dropping::after {
    content: 'Drop the photo to solve it';
    position: absolute;
    inset: 10px;
    display: grid;
    place-items: center;
    border: 2px dashed var(--accent);
    border-radius: 12px;
    background: rgba(78, 161, 255, 0.08);
    color: var(--text);
    font-size: 15px;
    z-index: 4;
    pointer-events: none;
  }
  .toolbar {
    position: absolute;
    top: 12px;
    left: 12px;
    display: flex;
    gap: 8px;
    align-items: center;
    flex-wrap: wrap;
  }
  .chip {
    display: flex;
    align-items: center;
    gap: 6px;
    background: var(--panel-2);
    border: 1px solid var(--border);
    border-radius: 6px;
    padding: 3px 10px;
    font-size: 13px;
    cursor: pointer;
    user-select: none;
  }
  .chip input[type='checkbox'] {
    width: auto;
    accent-color: var(--accent);
    margin: 0;
  }
  .badge {
    background: rgba(10, 17, 28, 0.82);
    border: 1px solid var(--border);
    border-left: 3px solid var(--c, var(--accent));
    border-radius: 6px;
    padding: 3px 10px;
    font-size: 13px;
  }
  .hintbar {
    position: absolute;
    top: 48px;
    left: 12px;
    right: 300px;
  }
  .hint {
    color: var(--muted);
    font-size: 12px;
    line-height: 1.5;
    text-shadow: 0 0 6px #000;
  }
  /* The photo panel, floating over the globe like the toolbar does. */
  .shot {
    position: absolute;
    right: 12px;
    bottom: 12px;
    width: 272px;
    background: rgba(10, 17, 28, 0.88);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 8px;
    display: flex;
    flex-direction: column;
    gap: 7px;
    z-index: 3;
  }
  .shot .seg button {
    flex: 1;
    font-size: 12px;
    padding: 2px 8px;
  }
  .frame {
    aspect-ratio: 1;
    display: grid;
    place-items: center;
    background: #05090f;
    border: 1px solid var(--border);
    border-radius: 6px;
    overflow: hidden;
  }
  .frame img {
    width: 100%;
    height: 100%;
    object-fit: contain;
    display: block;
  }
  /* A sky frame is nearly all black, and shrinking 1024 px to 270 dims the stars further.
     The stretch is for this thumbnail only - the solver works on the original pixels. */
  .frame img.photo {
    filter: brightness(1.6) contrast(1.1);
  }
  .empty {
    color: var(--muted);
    font-size: 12px;
    margin: 0;
    padding: 0 10px;
    text-align: center;
  }
  .caption {
    margin: 0;
    font-size: 10px;
    line-height: 1.4;
    color: var(--muted);
  }
  .loading {
    position: absolute;
    top: 50%;
    left: 24px;
    color: var(--muted);
    text-shadow: 0 0 6px #000;
  }
  .error {
    position: absolute;
    bottom: 12px;
    left: 12px;
    right: 300px;
    display: flex;
    flex-direction: column;
    gap: 4px;
    background: #3d1418;
    border: 1px solid var(--bad);
    color: #ffb3b0;
    padding: 8px 10px;
    border-radius: 8px;
    font-size: 12px;
    z-index: 3;
  }
  aside {
    width: 340px;
    flex-shrink: 0;
    background: var(--panel);
    border-left: 1px solid var(--border);
    overflow-y: auto;
    padding: 12px;
    display: flex;
    flex-direction: column;
    gap: 16px;
  }
  .lede {
    color: var(--muted);
    font-size: 12px;
    line-height: 1.5;
    margin: 0 0 8px;
  }
  .sub {
    color: var(--muted);
    font-size: 11px;
    line-height: 1.5;
    margin: 0 0 7px;
  }
  .sub.top {
    margin-top: 10px;
  }
  .key {
    display: flex;
    flex-wrap: wrap;
    gap: 4px 12px;
    list-style: none;
    margin: 0;
    padding: 0;
    font-size: 11px;
    color: var(--muted);
  }
  .key li {
    display: flex;
    align-items: center;
    gap: 5px;
  }
  .key .dot {
    width: 8px;
    height: 8px;
    border-radius: 50%;
  }
  .kv {
    display: grid;
    grid-template-columns: 116px 1fr;
    gap: 3px 10px;
    font-size: 12px;
    align-items: baseline;
  }
  .kv span:nth-child(odd) {
    color: var(--muted);
  }
  .kv .big {
    font-size: 17px;
    font-weight: 600;
  }
  .kv .small {
    font-size: 11px;
  }
  .nolock {
    font-size: 12px;
    line-height: 1.55;
    color: var(--muted);
    margin: 0;
  }
  .nolock b {
    color: var(--warn);
  }
  .warn {
    font-size: 11px;
    line-height: 1.5;
    color: var(--warn);
    margin: 8px 0 0;
  }
  aside label {
    display: flex;
    align-items: center;
    gap: 8px;
    font-size: 12px;
    margin-bottom: 5px;
    color: var(--muted);
  }
  aside label input[type='number'] {
    width: 82px;
    flex: none;
    margin-left: auto;
    text-align: right;
  }
  label.stack {
    display: flex;
    flex-direction: column;
    align-items: stretch;
    gap: 4px;
  }
  label.stack input {
    width: 100%;
  }
  .u {
    font-size: 10px;
    opacity: 0.7;
  }
  .chips {
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
  }
  .chips button {
    font-size: 12px;
    padding: 3px 9px;
  }
  .chips button.on {
    background: var(--accent);
    border-color: var(--accent);
    color: #fff;
  }
  button.wide {
    width: 100%;
    margin-top: 8px;
  }
  button.link {
    background: none;
    border: none;
    padding: 0;
    color: var(--accent);
    font-size: 12px;
    margin-bottom: 6px;
  }
  .check {
    color: var(--muted);
  }
  .check input {
    width: auto;
    accent-color: var(--accent);
  }
  code {
    background: var(--panel-2);
    padding: 1px 4px;
    border-radius: 3px;
  }
  @media (max-width: 1100px) {
    .hintbar,
    .error {
      right: 12px;
    }
    .shot {
      width: 200px;
    }
  }
  @media (max-width: 900px) {
    .page {
      flex-direction: column;
    }
    aside {
      width: 100%;
      height: 45%;
      border-left: none;
      border-top: 1px solid var(--border);
    }
  }
</style>
