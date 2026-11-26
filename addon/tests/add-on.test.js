// Runs against Luna.MockEngine: no LUNA needed. `npm test`.
import { describe, expect, it } from 'vitest';
import { Luna } from '@uaudio/luna-builder-sdk';

globalThis.Luna = Luna; // logic.js is a classic script that reads the Luna global
await import('../logic.js');
const logic = globalThis.AddOnLogic;
const B = Luna.TICKS_PER_BEAT;

async function connected() {
  const engine = new Luna.MockEngine();
  engine.addSession('abc', { name: 'Demo', tracks: [] });
  await engine.settled();
  const luna = await Luna.connect({ port_: engine.port() });
  const session = await luna.session();
  return { engine, luna, session };
}

const clip = (over = {}) => ({
  name: 'Kick', startBeat: 0, stopBeat: 4, sourcePath: '/set/kick.wav', sourceStartSec: 0, sourceStopSec: 1.76,
  gainDb: 0, muted: false, fadeInBeats: 0, fadeOutBeats: 0, ...over,
});

const plan = (over = {}) => ({
  source: { path: '/set/Song.als', name: 'Song.als', creator: 'Ableton Live 12.3.6' },
  tempo: 136,
  timeSignature: { numerator: 4, denominator: 4 },
  locators: [{ name: 'BREAK', beat: 80 }],
  sources: ['/set/kick.wav'],
  warnings: ['Return track "A-Reverb" not carried over (Reverb).'],
  missing: [],
  tracks: [{ name: 'Kicks', kind: 'audio', stemFormat: 'stereo', gainDb: 0, muted: false, clips: [clip(), clip({ startBeat: 4, stopBeat: 8 })], notes: [] }],
  ...over,
});

describe('InterDAW import', () => {
  it('summarises a plan', () => {
    expect(logic.summary(plan())).toMatchObject({ name: 'Song.als', tempo: 136, meter: '4/4', tracks: 1, clips: 2, notes: 0, files: 1, markers: 1 });
    expect(logic.stepCount(plan())).toBe(5);
  });

  it('builds the track, its clips and the marker relative to bar 1', async () => {
    const { engine, session } = await connected();
    await session.ready();
    await session.musical.ready();
    const origin = session.musical.timeline.tickAtBar(1);
    const steps = [];
    const result = await logic.runImport(session, plan(), (s) => steps.push(s));
    await engine.settled();
    expect(result).toEqual({ tracks: 1, failed: [] });
    expect(steps.at(-1)).toMatchObject({ step: 5, total: 5 });

    const track = session.visibleTracks[0];
    expect(track.name.value).toBe('Kicks');
    expect(track.stemFormat.value).toBe('stereo');
    const playlist = session.playlistFor(track);
    await playlist.clips.load();
    const clips = playlist.clips.items.map((c) => [c.start.value, c.stop.value, c.sourceStart.value, c.sourceStop.value]);
    expect(clips).toContainEqual([origin, origin + 4 * B, 0, 1760000000]);
    expect(clips).toContainEqual([origin + 4 * B, origin + 8 * B, 0, 1760000000]);
    expect(session.markers.sorted.map((m) => [m.name.value, m.location.value])).toEqual([['BREAK', origin + 80 * B]]);
  });

  it('sets the tempo and meter', async () => {
    const { engine, session } = await connected();
    await logic.runImport(session, plan({ tempo: 98, timeSignature: { numerator: 3, denominator: 4 } }));
    await engine.settled();
    const origin = session.musical.timeline.tickAtBar(1);
    expect(session.musical.tempoAt(origin)).toBe(98);
    expect(engine.requests.some((r) => r.path.endsWith('/set_time_signature'))).toBe(true);
  });

  it('copies each audio file once, however many clips use it', async () => {
    const { engine, session } = await connected();
    await logic.runImport(session, plan());
    await engine.settled();
    expect(engine.requests.filter((r) => r.path === '/workspace/import_file')).toHaveLength(1);
  });

  it('applies clip gain and fades, track gain and mute', async () => {
    const { engine, session } = await connected();
    const tracks = [{ name: 'Bass', kind: 'audio', stemFormat: 'stereo', gainDb: -6, muted: true, notes: [], clips: [clip({ gainDb: -6.45, fadeOutBeats: 0.25, stopBeat: 4 })] }];
    const result = await logic.runImport(session, plan({ tracks }));
    await engine.settled();
    expect(result.failed).toEqual([]);
    const track = session.visibleTracks[0];
    await track.loadStrip();
    expect(track.gain.real.value).toBe(-6);
    expect(track.mute.on).toBe(true);
    const playlist = session.playlistFor(track);
    await playlist.clips.reload();
    expect(playlist.clips.items[0].gain.value).toBeCloseTo(-6.45);
    expect(engine.requests.some((r) => /fade/.test(r.path))).toBe(true);
  });

  it('writes MIDI notes onto an instrument track', async () => {
    const { engine, session } = await connected();
    await session.ready();
    await session.musical.ready();
    const origin = session.musical.timeline.tickAtBar(1);
    const tracks = [{ name: 'Keys', kind: 'instrument', stemFormat: 'stereo', gainDb: 0, muted: false, clips: [], notes: [{ pitch: 37, startBeat: 288, stopBeat: 289, velocity: 90 }] }];
    const result = await logic.runImport(session, plan({ tracks, sources: [] }));
    await engine.settled();
    expect(result.failed).toEqual([]);
    const body = engine.requests.find((r) => /create_note/.test(r.path));
    expect(JSON.stringify(body)).toContain(String(origin + 288 * B));
  });

  it('remembers which set the session came from', async () => {
    const { session } = await connected();
    expect(await logic.rememberedSet(session)).toBeUndefined();
    await logic.rememberSet(session, '/set/Song.als');
    await logic.rememberSet(session, '/set/Other.als');
    expect(await logic.rememberedSet(session)).toBe('/set/Other.als');
  });

  it('a file that fails to copy skips its clips and is reported, the rest carry on', async () => {
    const { session } = await connected();
    const realImport = session.importPath.bind(session);
    session.importPath = (path) => (path === '/set/bad.wav' ? Promise.reject(new Error('cannot open')) : realImport(path));
    const tracks = [
      { name: 'Bad', kind: 'audio', stemFormat: 'stereo', gainDb: 0, muted: false, notes: [], clips: [clip({ sourcePath: '/set/bad.wav' })] },
      { name: 'Good', kind: 'audio', stemFormat: 'stereo', gainDb: 0, muted: false, notes: [], clips: [clip()] },
    ];
    const result = await logic.runImport(session, plan({ tracks, sources: ['/set/bad.wav', '/set/kick.wav'] }));
    expect(result.tracks).toBe(2);
    expect(result.failed).toEqual(['/set/bad.wav: cannot open']);
  });
});

describe('InterDAW suggestions', () => {
  const insert = (over = {}) => ({ id: 's', title: 'Add EQ', why: '', match: 'close', target: 'Kicks', audible: true, action: { kind: 'insert', inserts: [{ slot: 'effect_0', pluginId: 'com.appl.au.aufx_nbeq', pluginName: null, params: [{ id: 'gain', real: -3 }], automation: [] }] }, ...over });

  async function imported() {
    const ctx = await connected();
    ctx.engine.addPlugin('com.appl.au.aufx_nbeq', { name: 'AUNBandEQ', categories: ['EQ'] });
    await logic.runImport(ctx.session, plan());
    await ctx.engine.settled();
    return ctx;
  }

  it('waits for the track it acts on', async () => {
    const { session } = await connected();
    await session.ready();
    expect(logic.canRun(session, insert())).toBe(false);
    expect(logic.canRun(session, insert({ target: '__main__' }))).toBe(true);
  });

  it('loads the plugin into the planned slot', async () => {
    const { engine, luna, session } = await imported();
    await logic.runSuggestion(luna, session, insert());
    await engine.settled();
    const track = session.visibleTracks[0];
    await track.effects.load();
    expect(track.effects.byId('effect_0').pluginId.value).toBe('com.appl.au.aufx_nbeq');
  });

  it('running twice does not load a second plugin', async () => {
    const { engine, luna, session } = await imported();
    await logic.runSuggestion(luna, session, insert());
    await engine.settled();
    const count = engine.requests.filter((r) => r.path.endsWith('/set_inserts')).length;
    await logic.runSuggestion(luna, session, insert());
    await engine.settled();
    expect(engine.requests.filter((r) => r.path.endsWith('/set_inserts'))).toHaveLength(count);
  });

  it('says so when LUNA lacks the plugin', async () => {
    const { luna, session } = await imported();
    await expect(logic.runSuggestion(luna, session, insert({ action: { kind: 'insert', inserts: [{ slot: 'effect_0', pluginId: 'com.nope', pluginName: 'Nope', params: [], automation: [] }] } }))).rejects.toThrow("LUNA doesn't have Nope");
  });

  it('builds a return with each send at its level before it is routed', async () => {
    const { engine, luna, session } = await imported();
    const action = { kind: 'return', name: 'A-Reverb', send: 'send_0', inserts: [{ slot: 'effect_0', pluginId: 'com.appl.au.aufx_nbeq', params: [] }], levels: [{ track: 'Kicks', gainDb: -70 }] };
    await logic.runSuggestion(luna, session, { ...insert(), target: '__session__', action });
    await engine.settled();
    const track = session.visibleTracks[0];
    await track.outputs.load();
    const send = track.outputs.byId('send_0');
    await send.controls.load();
    expect(send.gain.real.value).toBe(-70);
    expect(session.buses.items.some((b) => b.name.value === 'A-Reverb')).toBe(true);
    await logic.runSuggestion(luna, session, { ...insert(), target: '__session__', action });
    await engine.settled();
    expect(session.buses.items.filter((b) => b.name.value === 'A-Reverb')).toHaveLength(1);
  });

  it('places slices as audio on a new track, once', async () => {
    const { engine, luna, session } = await imported();
    const action = { kind: 'slices', name: 'Keys (audio)', sourcePath: '/set/keys.wav', gainDb: -8.5, muted: true, clips: [{ startBeat: 8, stopBeat: 9, sourceStartSec: 0.5, sourceStopSec: 1 }] };
    await logic.runSuggestion(luna, session, { ...insert(), target: 'Kicks', action });
    await logic.runSuggestion(luna, session, { ...insert(), target: 'Kicks', action });
    await engine.settled();
    const tracks = session.visibleTracks.filter((t) => t.name.value === 'Keys (audio)');
    expect(tracks).toHaveLength(1);
    await tracks[0].loadStrip();
    expect(tracks[0].gain.real.value).toBe(-8.5);
    expect(tracks[0].mute.on).toBe(true);
  });

  it('reads the numbers in a control display', () => {
    expect(logic.displayNumber('2.50 s')).toBe(2.5);
    expect(logic.displayNumber('8.00 k')).toBe(8000);
    expect(logic.displayNumber('-1.4 dB')).toBe(-1.4);
    expect(logic.displayNumber('Out')).toBeNaN();
  });

  const fakeControl = (displayFor, numSteps = 0) => {
    const control = {
      name: { value: 'Fake' },
      numSteps: { value: numSteps },
      display: { value: displayFor(0.5) },
      normalized: { value: 0.5, set: async (v) => { control.normalized.value = v; control.display.value = displayFor(v); } },
    };
    return control;
  };

  it('finds a switch position by what it displays', async () => {
    const control = fakeControl((v) => (v > 0.5 ? 'In' : 'Out'), 2);
    await logic.setParam(control, { id: 'x', display: 'In' });
    expect(control.display.value).toBe('In');
    await expect(logic.setParam(control, { id: 'x', display: 'Sideways' })).rejects.toThrow('no Sideways');
  });

  it('finds a value by display, rising or falling', async () => {
    const rising = fakeControl((v) => `${(v * 10).toFixed(2)} s`);
    await logic.setParam(rising, { id: 'x', displayNumber: 2.5 });
    expect(logic.displayNumber(rising.display.value)).toBeCloseTo(2.5, 1);
    const falling = fakeControl((v) => `${((1 - v) * 10).toFixed(2)} s`);
    await logic.setParam(falling, { id: 'x', displayNumber: 2.5 });
    expect(logic.displayNumber(falling.display.value)).toBeCloseTo(2.5, 1);
  });

  it('falls back to the display when a control refuses a value in units', async () => {
    const control = fakeControl((v) => (v < 0.1 ? 'Out' : `${Math.round(v * 350)} Hz`));
    control.real = { value: 0, set: async () => { throw Object.assign(new Error('refused'), { code: 'uasrv.invalid_value' }); } };
    await logic.setParam(control, { id: 'hp_freq', real: 170 });
    expect(logic.displayNumber(control.display.value)).toBeCloseTo(170, -1);
  });

  it('makes one filtered copy of the track with both faders automated', async () => {
    const { engine, luna, session } = await imported();
    const action = { kind: 'crossfade', wetName: 'Kicks (filter)', insert: { slot: 'effect_0', pluginId: 'com.appl.au.aufx_nbeq', params: [], automation: [] }, also: [], wetLane: [{ beat: 0, real: 0 }, { beat: 8, real: -144 }], dryLane: [{ beat: 0, real: -144 }, { beat: 8, real: 0 }] };
    await logic.runSuggestion(luna, session, { ...insert(), action });
    await engine.settled();
    await logic.runSuggestion(luna, session, { ...insert(), action });
    await engine.settled();
    const copies = session.visibleTracks.filter((t) => t.name.value === 'Kicks (filter)');
    expect(copies).toHaveLength(1);
    const playlist = session.playlistFor(copies[0]);
    await playlist.clips.load();
    expect(playlist.clips.items).toHaveLength(2);
    for (const track of [copies[0], session.visibleTracks.find((t) => t.name.value === 'Kicks')]) {
      await track.loadStrip();
      await track.gain.automation.load();
      expect(track.gain.automation.items.length).toBeGreaterThanOrEqual(2);
    }
  });

  it('copies colours onto matching tracks', async () => {
    const { engine, luna, session } = await imported();
    await logic.runSuggestion(luna, session, { ...insert(), target: '__session__', action: { kind: 'colours', colours: { Kicks: 0xFFFF94A6, Gone: 1 } } });
    await engine.settled();
    expect(session.visibleTracks[0].color.value).toBe(0xFFFF94A6);
  });
});

describe('InterDAW send back', () => {
  async function importedSet(over = {}) {
    const ctx = await connected();
    ctx.engine.addPlugin('com.ua.eq', { name: 'EQ', categories: ['EQ'] });
    ctx.engine.addPlugin('com.you.verb', { name: 'Verb', categories: ['Reverb'] });
    const p = plan(over);
    await logic.runImport(ctx.session, p);
    await ctx.engine.settled();
    await ctx.session.ready();
    // The mock's stereo pans start at 0; LUNA's centre is hard left and right.
    for (const track of ctx.session.visibleTracks) {
      await track.loadStrip();
      await track.main.pans[0].real.set(-1);
      await track.main.pans[1].real.set(1);
    }
    await ctx.engine.settled();
    return { ...ctx, p };
  }
  const row = (diff, name) => diff.rows.find((r) => r.name === name);

  it('leaves a track alone when nothing changed', async () => {
    const { session, p } = await importedSet();
    const diff = await logic.compareSession(session, p);
    expect(row(diff, 'Kicks').status).toBe('same');
    expect(logic.exportCounts(diff)).toMatchObject({ same: 1, mix: 0, print: 0, added: 0 });
  });

  it('writes a fader or mute change instead of printing', async () => {
    const { engine, session, p } = await importedSet();
    const track = session.visibleTracks.find((t) => t.name.value === 'Kicks');
    await track.loadStrip();
    await track.gain.real.set(-3);
    await track.mute.real.set(true);
    await engine.settled();
    const diff = await logic.compareSession(session, p);
    expect(row(diff, 'Kicks')).toMatchObject({ status: 'mix', move: { track: 'Kicks', gainDb: -3, muted: true } });
  });

  it('prints a track whose clips were edited', async () => {
    const { engine, session, p } = await importedSet();
    const track = session.visibleTracks.find((t) => t.name.value === 'Kicks');
    const lane = session.playlistFor(track);
    await lane.clips.load();
    await lane.clips.items[0].delete();
    await engine.settled();
    const diff = await logic.compareSession(session, p);
    expect(row(diff, 'Kicks')).toMatchObject({ status: 'print', reasons: ['clips edited'] });
  });

  it('prints a track with a plugin you added, but not one InterDAW suggested', async () => {
    const suggestions = [{ id: 's', target: 'Kicks', action: { kind: 'insert', inserts: [{ slot: 'effect_0', pluginId: 'com.ua.eq', params: [] }] } }];
    const { engine, session, p } = await importedSet({ suggestions });
    const track = session.visibleTracks.find((t) => t.name.value === 'Kicks');
    await track.loadStrip();
    await track.insert('com.ua.eq', 'effect_0');
    await engine.settled();
    expect(row(await logic.compareSession(session, p), 'Kicks').status).toBe('same');
    await track.insert('com.you.verb', 'effect_1');
    await engine.settled();
    expect(row(await logic.compareSession(session, p), 'Kicks')).toMatchObject({ status: 'print', reasons: ['plugins added'] });
  });

  it('calls a new track new, and a stand-in copy neither', async () => {
    const suggestions = [{ id: 's', target: 'Kicks', action: { kind: 'crossfade', wetName: 'Kicks (filter)', insert: { pluginId: 'com.ua.eq' }, also: [] } }];
    const { engine, session, p } = await importedSet({ suggestions });
    await session.newTrack({ name: 'Kicks (filter)', trackType: 'audio', stemFormat: 'stereo' });
    await session.newTrack({ name: 'Vocal', trackType: 'audio', stemFormat: 'mono' });
    await engine.settled();
    await new Promise((r) => setTimeout(r, 50));
    const diff = await logic.compareSession(session, p);
    expect(row(diff, 'Kicks (filter)').status).toBe('standIn');
    expect(row(diff, 'Vocal').status).toBe('new');
  });

  it('notices a removed track and a changed tempo', async () => {
    const { engine, session, p } = await importedSet();
    await session.musical.setTempoInEffectAt(120, session.musical.timeline.tickAtBar(1));
    await session.deleteTracks([session.visibleTracks.find((t) => t.name.value === 'Kicks').id]);
    await engine.settled();
    await new Promise((r) => setTimeout(r, 50));
    const diff = await logic.compareSession(session, p);
    expect(diff.removed).toEqual(['Kicks']);
    expect(diff.warnings[0]).toContain('now 120 BPM');
  });

  it('prints a few stems at a time', async () => {
    const tracks = ['A', 'B', 'C', 'D', 'E', 'F'].map((name) => ({ name, kind: 'audio', stemFormat: 'stereo', gainDb: 0, muted: false, clips: [clip()], notes: [] }));
    const { engine, session, p } = await importedSet({ tracks });
    const seen = [];
    const realBounce = session.bounce.bind(session);
    session.bounce = async (o) => {
      seen.push(o.outputPaths.length);
      const result = await realBounce(o);
      setTimeout(() => engine.finishRender(result.render.id, { state: 'completed' }), 5);
      return result;
    };
    const fetchFn = async (url) => ({ ok: true, json: async () => (url.endsWith('prepare') ? { folder: '/o' } : { folder: '/o', stems: 6, missing: [] }) });
    await logic.runExport(session, p, '/out', () => {}, fetchFn);
    expect(seen).toEqual([4, 2, 1]);
  });

  it('cancels a render that stops moving and says so', async () => {
    const { engine, session, p } = await importedSet();
    const stall = logic.STALL_MS;
    logic.STALL_MS = 20;
    let aborted = false;
    const realBounce = session.bounce.bind(session);
    session.bounce = async (o) => {
      const result = await realBounce(o);
      const abort = result.render.abort.bind(result.render);
      result.render.abort = async () => { aborted = true; engine.finishRender(result.render.id, { state: 'aborted' }); return abort().catch(() => {}); };
      return result;
    };
    const fetchFn = async () => ({ ok: true, json: async () => ({ folder: '/o' }) });
    await expect(logic.runExport(session, p, '/out', () => {}, fetchFn)).rejects.toThrow('stopped rendering');
    logic.STALL_MS = stall;
    expect(aborted).toBe(true);
  }, 8000);

  describe('export formats', () => {
    async function exportAs(format) {
      const tracks = [
        { name: 'Same', kind: 'audio', stemFormat: 'stereo', gainDb: 0, muted: false, clips: [clip()], notes: [] },
        { name: 'Quiet', kind: 'audio', stemFormat: 'stereo', gainDb: 0, muted: true, clips: [clip()], notes: [] },
      ];
      const { engine, session, p } = await importedSet({ tracks });
      await session.newTrack({ name: 'Vocal', trackType: 'audio', stemFormat: 'mono' });
      await engine.settled();
      await new Promise((r) => setTimeout(r, 50));
      await session.musical.setTempoInEffectAt(120, session.musical.timeline.tickAtBar(1));
      await engine.settled();
      const rendered = [];
      const realBounce = session.bounce.bind(session);
      session.bounce = async (o) => {
        rendered.push(...o.outputPaths.map((path) => path.slice(path.lastIndexOf('/') + 1)));
        const result = await realBounce(o);
        setTimeout(() => engine.finishRender(result.render.id, { state: 'completed' }), 5);
        return result;
      };
      const posts = {};
      const fetchFn = async (url, init) => {
        posts[url.replace(logic.HELPER_URL, '')] = JSON.parse(init.body);
        return { ok: true, json: async () => ({ folder: '/o', stems: 0, missing: [] }) };
      };
      await logic.runExport(session, p, '/out', () => {}, fetchFn, format);
      return { rendered, posts };
    }

    it('both prints everything that sounds and builds the set', async () => {
      const { rendered, posts } = await exportAs('both');
      expect(rendered).toEqual(['01 Same.wav', '02 Vocal.wav', 'Mix Reference.wav']);
      expect(posts['/return/prepare'].format).toBe('both');
      expect(posts['/return/build'].prints).toEqual([{ file: '02 Vocal.wav', name: 'Vocal', original: null }]);
      expect(posts['/return/build'].warnings[0]).toContain('now 120 BPM');
    });

    it('a Live set only prints what the set cannot already play', async () => {
      const { rendered, posts } = await exportAs('live');
      expect(rendered).toEqual(['01 Vocal.wav', 'Mix Reference.wav']);
      expect(posts['/return/build'].format).toBe('live');
      expect(posts['/return/build'].prints).toEqual([{ file: '01 Vocal.wav', name: 'Vocal', original: null }]);
    });

    it('stems leave out silence, need no set, and carry the session tempo', async () => {
      const { rendered, posts } = await exportAs('stems');
      expect(rendered).toEqual(['01 Same.wav', 'Mix Reference.wav']); // Quiet is muted, Vocal is empty
      const build = posts['/return/build'];
      expect(build).toMatchObject({ format: 'stems', prints: [], moves: [], removed: [], warnings: [], sessionTempo: 120 });
    });
  });

  it('has nothing to send back until the set is in the session', async () => {
    const { session } = await connected();
    await session.ready();
    expect(logic.isImported(session, plan())).toBe(false);
    const { session: imported, p } = await importedSet();
    expect(logic.isImported(imported, p)).toBe(true);
  });

  it('names stems so they sort in track order and are safe on disk', () => {
    expect(logic.stemName(0, 'Voc Enjoy/Yourself: Rec')).toBe('01 Voc Enjoy-Yourself- Rec.wav');
    expect(logic.stemName(11, '..')).toBe('12 Untitled.wav');
  });

  it('sends what changed to the helper and prints from bar 1', async () => {
    const { engine, session, p } = await importedSet();
    await session.newTrack({ name: 'Vocal', trackType: 'audio', stemFormat: 'mono' });
    await engine.settled();
    await new Promise((r) => setTimeout(r, 50));
    const posts = {};
    const fetchFn = async (url, init) => {
      const path = url.replace(logic.HELPER_URL, '');
      posts[path] = JSON.parse(init.body);
      return { ok: true, json: async () => (path === '/return/prepare' ? { folder: '/out/Song - LUNA Return' } : { folder: '/out/Song - LUNA Return', set: 'Song LUNA.als', stems: 2, missing: [] }) };
    };
    const seen = [];
    const realBounce = session.bounce.bind(session);
    session.bounce = async (o) => {
      seen.push(o);
      const result = await realBounce(o);
      setTimeout(() => engine.finishRender(result.render.id, { state: 'completed' }), 5);
      return result;
    };
    const result = await logic.runExport(session, p, '/out', () => {}, fetchFn);
    const origin = session.musical.timeline.tickAtBar(1);
    expect(posts['/return/prepare']).toEqual({ setPath: '/set/Song.als', parent: '/out', format: 'both' });
    expect(seen[0].range.startTick).toBe(origin);
    expect(seen[0].recordPoint).toBe('post_pan');
    expect(seen[0].outputPaths).toEqual(['/out/Song - LUNA Return/Stems/01 Kicks.wav', '/out/Song - LUNA Return/Stems/02 Vocal.wav']);
    expect(seen[1].outputPaths).toEqual(['/out/Song - LUNA Return/Mix Reference.wav']);
    expect(posts['/return/build'].prints).toEqual([{ file: '02 Vocal.wav', name: 'Vocal', original: null }]);
    expect(result.counts).toMatchObject({ same: 1, added: 1 });
  });
});

describe('InterDAW panel data', () => {
  it('moves a suggestion that failed out of the list and into a note', () => {
    const suggestions = [{ id: 'a', title: 'Add EQ', audible: true }, { id: 'b', title: 'Add OneKnob', audible: false }, { id: 'c', title: 'Colours', audible: false }, { id: 'd', title: 'Gone', audible: true }];
    const state = { b: { status: 'failed', error: "It wouldn't load." }, a: { status: 'done' }, d: { status: 'dismissed' } };
    const groups = logic.suggestionGroups({ suggestions }, state);
    expect(groups.audible.map((s) => s.id)).toEqual(['a']);
    expect(groups.optional.map((s) => s.id)).toEqual(['c']);
    expect(groups.failed).toEqual(["Add OneKnob: It wouldn't load."]);
  });

  it('has nothing to check for a complete set and an empty session', () => {
    expect(logic.checks(plan(), 0)).toEqual([]);
  });

  it('asks for a second look at a session with tracks or a set with missing audio', () => {
    expect(logic.checks(plan(), 1)[0]).toContain('already has 1 track.');
    expect(logic.checks(plan({ missing: [{ track: 'A', clip: 'B', path: '/x' }, { track: 'A', clip: 'C', path: '/y' }] }), 0)).toEqual(['2 clips have missing audio and will be skipped.']);
  });

  it('lists the set in rows, leaving out what it does not have', () => {
    expect(logic.factRows(plan())).toEqual([['TEMPO', '136 BPM, 4/4'], ['TRACKS', '1'], ['CLIPS', '2 from 1 audio file'], ['MARKS', 'BREAK']]);
    expect(logic.factRows(plan({ locators: [] })).map((r) => r[0])).toEqual(['TEMPO', 'TRACKS', 'CLIPS']);
  });
});

describe('InterDAW helper calls', () => {
  it('knows whether the helper is running, missing or too old for this panel', async () => {
    const health = (body) => async () => ({ ok: true, json: async () => body });
    expect(await logic.helperStatus(health({ ok: true, api: 1 }))).toBe('running');
    expect(await logic.helperStatus(health({ ok: true, version: '0.2.0' }))).toBe('outdated');
    expect(await logic.helperStatus(async () => ({ ok: false, status: 404 }))).toBe('missing');
    expect(await logic.helperStatus(async () => { throw new TypeError('failed'); })).toBe('missing');
  });

  it('passes the path and returns the plan', async () => {
    const seen = [];
    const fetchFn = async (url, init) => { seen.push([url, JSON.parse(init.body)]); return { ok: true, json: async () => plan() }; };
    expect((await logic.readSet('/set/Song.als', fetchFn)).tempo).toBe(136);
    expect(seen[0]).toEqual(['http://127.0.0.1:8792/als/read', { path: '/set/Song.als' }]);
  });

  it('says when the helper is not running', async () => {
    await expect(logic.readSet('/x.als', async () => { throw new TypeError('failed'); })).rejects.toThrow('helper is not running');
  });

  it('says the helper stopped, not that it is missing, when only the read fails', async () => {
    const fetchFn = async (url) => {
      if (url.endsWith('/health')) return { ok: true, status: 200, json: async () => ({ ok: true, api: 1 }) };
      throw new TypeError('connection dropped');
    };
    await expect(logic.readSet('/x.als', fetchFn)).rejects.toThrow("running but didn't finish");
  });

  it('says when the helper refuses this panel', async () => {
    const fetchFn = async (url) => {
      if (url.endsWith('/health')) return { ok: false, status: 403, json: async () => ({ error: 'origin not allowed' }) };
      throw new TypeError('preflight refused');
    };
    expect(await logic.helperStatus(fetchFn)).toBe('refused');
    await expect(logic.readSet('/x.als', fetchFn)).rejects.toThrow('refused this panel');
  });

  it("shows the helper's own error", async () => {
    const fetchFn = async () => ({ ok: false, status: 422, json: async () => ({ error: 'Not a set.' }) });
    await expect(logic.readSet('/x.als', fetchFn)).rejects.toThrow('Not a set.');
  });
});
