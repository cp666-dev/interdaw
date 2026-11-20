// InterDAW: logic that does not touch the DOM, so tests can exercise it against Luna.MockEngine.
//
// The helper turns a project file into a plan of plain data; runImport builds it in a session.

const AddOnLogic = {
  HELPER_URL: 'http://127.0.0.1:8792',
  /** The helper API this panel speaks; the helper reports its own on /health. */
  HELPER_API: 1,
  /** Installs or updates the helper from the latest release. Shown in the panel. */
  HELPER_INSTALL: 'curl -fsSL https://github.com/cp666-dev/interdaw/releases/latest/download/install-helper.sh | bash',
  HELP_URL: 'https://github.com/cp666-dev/interdaw#install-the-helper',

  /** Ask the helper to read a set. Rejects with a message the panel can show. */
  async readSet(path, fetchFn = fetch) {
    let answer;
    try {
      answer = await fetchFn(`${AddOnLogic.HELPER_URL}/als/read`, {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ path }),
      });
    } catch {
      throw new Error('The InterDAW helper is not running. Install it as the panel shows, then try again.');
    }
    const body = await answer.json();
    if (!answer.ok) throw new Error(body.error ?? `The helper answered ${answer.status}.`);
    return body;
  },

  /** 'running', 'missing' (nothing answers) or 'outdated' (a helper for another panel version). */
  async helperStatus(fetchFn = fetch) {
    try {
      const answer = await fetchFn(`${AddOnLogic.HELPER_URL}/health`);
      if (!answer.ok) return 'missing';
      const body = await answer.json();
      return body.api === AddOnLogic.HELPER_API ? 'running' : 'outdated';
    } catch {
      return 'missing';
    }
  },

  /** The reasons to look twice before importing. Any reason turns IMPORT from blue to grey. */
  checks(plan, existingTracks) {
    const reasons = [];
    if (existingTracks > 0) reasons.push(`This session already has ${existingTracks} track${existingTracks === 1 ? '' : 's'}. Importing changes its tempo and meter and isn't one undo step, so a new empty session is safest.`);
    if (plan.missing.length) reasons.push(`${plan.missing.length} clip${plan.missing.length === 1 ? ' has' : 's have'} missing audio and will be skipped.`);
    return reasons;
  },

  /** The rows of the set's slot. */
  factRows(plan) {
    const s = AddOnLogic.summary(plan);
    const rows = [['TEMPO', `${s.tempo} BPM, ${s.meter}`], ['TRACKS', String(s.tracks)], ['CLIPS', `${s.clips} from ${s.files} audio file${s.files === 1 ? '' : 's'}`]];
    if (s.notes) rows.push(['MIDI', `${s.notes} note${s.notes === 1 ? '' : 's'}`]);
    if (s.markers) rows.push(['MARKS', plan.locators.map((l) => l.name).join(', ')]);
    return rows;
  },

  /** The numbers the panel shows before anything is imported. */
  summary(plan) {
    return {
      name: plan.source.name,
      tempo: plan.tempo,
      meter: `${plan.timeSignature.numerator}/${plan.timeSignature.denominator}`,
      tracks: plan.tracks.length,
      clips: plan.tracks.reduce((n, t) => n + t.clips.length, 0),
      notes: plan.tracks.reduce((n, t) => n + t.notes.length, 0),
      files: plan.sources.length,
      markers: plan.locators.length,
      warnings: plan.warnings,
      missing: plan.missing,
    };
  },

  /** How many steps runImport reports, for a progress bar. */
  stepCount(plan) {
    return 2 + plan.sources.length + plan.tracks.length + plan.locators.length;
  },

  /** Resolve once `find()` answers something, re-checking whenever `prop` changes. */
  whenFound(prop, find, what, timeoutMs = 10000) {
    const found = find();
    if (found) return Promise.resolve(found);
    return new Promise((resolve, reject) => {
      let stop = () => {};
      const timer = setTimeout(() => {
        stop();
        reject(new Error(`LUNA did not list ${what} in time.`));
      }, timeoutMs);
      stop = prop.onChange(() => {
        const value = find();
        if (!value) return;
        clearTimeout(timer);
        stop();
        resolve(value);
      });
    });
  },

  /** One track: create it, place its clips as one undo step, then apply what needs the clips. */
  async importTrack(session, spec, originTick, sourceUids) {
    const tick = (beat) => originTick + Math.round(beat * Luna.TICKS_PER_BEAT);
    const [track] = await session.newTrack({ name: spec.name, trackType: spec.kind, stemFormat: spec.stemFormat });
    await track.loadStrip();

    if (spec.clips.length) {
      await session.transaction(`Import ${spec.name}`, async () => {
        for (const clip of spec.clips) {
          await session.insertClip({
            trackUid: track.id,
            sourceUid: sourceUids.get(clip.sourcePath),
            name: clip.name,
            startTick: tick(clip.startBeat),
            stopTick: tick(clip.stopBeat),
            sourceStartNs: Math.round(clip.sourceStartSec * 1e9),
            sourceStopNs: Math.round(clip.sourceStopSec * 1e9),
          });
        }
      });
    }

    const needsClips = spec.clips.filter((c) => Math.abs(c.gainDb) > 0.01 || c.muted || c.fadeInBeats > 0 || c.fadeOutBeats > 0);
    if (needsClips.length || spec.notes.length) {
      const playlist = await AddOnLogic.whenFound(session.playlists, () => session.playlistFor(track), `the lane of ${spec.name}`);
      await playlist.clips.reload();
      for (const wanted of needsClips) {
        const clip = playlist.clips.items.find((c) => c.start.value === tick(wanted.startBeat));
        if (!clip) throw new Error(`${spec.name}: LUNA did not list the clip at beat ${wanted.startBeat}.`);
        if (Math.abs(wanted.gainDb) > 0.01) await clip.gain.set(wanted.gainDb);
        if (wanted.muted) await clip.setMuted(true);
        if (wanted.fadeInBeats > 0) await clip.setFadeIn(Math.round(wanted.fadeInBeats * Luna.TICKS_PER_BEAT));
        if (wanted.fadeOutBeats > 0) await clip.setFadeOut(Math.round(wanted.fadeOutBeats * Luna.TICKS_PER_BEAT));
      }
      if (spec.notes.length) {
        await playlist.createNotes(spec.notes.map((n) => ({
          pitch: n.pitch,
          startTick: tick(n.startBeat),
          stopTick: tick(n.stopBeat),
          velocity: n.velocity,
        })));
      }
    }

    if (Math.abs(spec.gainDb) > 0.01) await track.gain.real.set(spec.gainDb);
    if (spec.muted) await track.mute.real.set(true);
    return track;
  },

  STORAGE_ID: 'com.cp.interdaw',

  /** Remember which set this session came from, so it reopens without asking. */
  async rememberSet(session, path) {
    const storage = session.storage.context(AddOnLogic.STORAGE_ID);
    await storage.load();
    await storage.add('import', 'setPath', path);
    await storage.set('import', 'setPath', path);
  },

  async rememberedSet(session) {
    const storage = session.storage.context(AddOnLogic.STORAGE_ID);
    await storage.load();
    return storage.value('import', 'setPath').value;
  },

  /** The channel a suggestion acts on: a track by name, or the master bus. */
  async channelFor(session, target) {
    await session.ready();
    if (target === '__main__') {
      await session.buses.load();
      return session.master;
    }
    return session.visibleTracks.find((t) => t.name.value === target);
  },

  /** Sort suggestions for the panel. One that couldn't run leaves the list and becomes a note. */
  suggestionGroups(plan, state) {
    const all = plan.suggestions ?? [];
    const listed = all.filter((s) => !['dismissed', 'failed'].includes(state[s.id]?.status));
    return {
      audible: listed.filter((s) => s.audible),
      optional: listed.filter((s) => !s.audible),
      failed: all.filter((s) => state[s.id]?.status === 'failed').map((s) => `${s.title}: ${state[s.id].error}`),
    };
  },

  /** Whether a suggestion can run yet: what it acts on has to be in the session. */
  canRun(session, suggestion) {
    if (suggestion.target === '__session__' || suggestion.target === '__main__') return true;
    return session.visibleTracks.some((t) => t.name.value === suggestion.target);
  },

  /** Load a plugin into a slot, reusing it if a previous GO already put it there. */
  async loadEffect(luna, channel, insert) {
    await luna.plugins.load();
    const plugin = insert.pluginId
      ? luna.plugins.items.find((p) => p.id === insert.pluginId)
      : luna.plugins.items.find((p) => p.name.value === insert.pluginName);
    const label = insert.pluginName ?? plugin?.name.value ?? insert.pluginId;
    if (!plugin) throw new Error(`LUNA doesn't have ${label}.`);
    await channel.effects.load();
    const existing = channel.effects.byId(insert.slot);
    if (existing?.pluginId.value === plugin.id && existing.loaded.value) return existing;
    try {
      return await channel.insert(plugin.id, insert.slot);
    } catch (e) {
      await channel.effects.byId(insert.slot)?.clear();
      throw new Error(`${label} wouldn't load in LUNA (${e.code ?? e.message}).`);
    }
  },

  SETTLE_MS: 80,
  settle() {
    return new Promise((r) => setTimeout(r, AddOnLogic.SETTLE_MS));
  },

  /** The number in a control's display, such as 2.50 from "2.50 s" or 8000 from "8.00 k". */
  displayNumber(text) {
    const match = /(-?[\d.]+)\s*(k)?/i.exec(text ?? '');
    return match ? parseFloat(match[1]) * (match[2] ? 1000 : 1) : NaN;
  },

  /** Find a switch or menu position by what it displays. */
  async setDisplay(control, wanted) {
    if (control.display.value === wanted) return;
    const steps = control.numSteps.value || 2;
    for (let step = 0; step < steps; step += 1) {
      await control.normalized.set(step / (steps - 1));
      await AddOnLogic.settle();
      if (control.display.value === wanted) return;
    }
    throw new Error(`${control.name.value} has no ${wanted} setting.`);
  },

  /** Find a value by what the control displays, for one with no writable units. A position
   * that shows a word, like a filter's Out, is treated as the bottom of its travel. */
  async setDisplayNumber(control, wanted) {
    const read = async (position) => {
      await control.normalized.set(position);
      await AddOnLogic.settle();
      return AddOnLogic.displayNumber(control.display.value);
    };
    const bottom = await read(0);
    const rising = Number.isNaN(bottom) || (await read(1)) > bottom;
    let low = 0;
    let high = 1;
    for (let step = 0; step < 9; step += 1) {
      const middle = (low + high) / 2;
      const shown = await read(middle);
      if (Number.isNaN(shown) || shown < wanted === rising) low = middle;
      else high = middle;
    }
  },

  async setParam(control, param) {
    if (param.off) await control.normalized.set(0);
    else if (param.normalized !== undefined) await control.normalized.set(param.normalized);
    else if (param.display !== undefined) await AddOnLogic.setDisplay(control, param.display);
    else if (param.displayNumber !== undefined) await AddOnLogic.setDisplayNumber(control, param.displayNumber);
    else {
      try {
        await control.real.set(typeof control.real.value === 'boolean' ? Boolean(param.real) : param.real);
      } catch (e) {
        // Some plugin controls refuse a value in units, though they show and accept the same one.
        if (e.code !== 'uasrv.invalid_value') throw e;
        await AddOnLogic.setDisplayNumber(control, param.real);
      }
    }
  },

  /** Write plugin parameters by id. A parameter the plugin doesn't have is left alone. */
  async setParams(effect, params) {
    await effect.controls.load();
    for (const param of params) {
      const control = effect.params.find((p) => p.id === param.id);
      if (control) await AddOnLogic.setParam(control, param);
    }
  },

  /** Points hold values in the control's units. "Off" is the bottom of its travel, whatever
   * that reads as, so that one value is read back from the control. */
  async writeAutomation(session, channel, effect, lanes) {
    if (!lanes?.length) return;
    const origin = session.musical.timeline.tickAtBar(1);
    const tick = (beat) => origin + Math.round(beat * Luna.TICKS_PER_BEAT);
    await effect.controls.load();
    for (const lane of lanes) {
      const control = effect.params.find((p) => p.id === lane.id);
      if (!control) continue;
      let off = 0;
      if (lane.points.some((p) => p.off)) {
        await control.normalized.set(0);
        await AddOnLogic.settle();
        off = control.real.value;
      }
      const points = lane.points.map((p) => ({ tick: tick(p.beat), value: p.off ? off : p.real }));
      await control.automation.insert(points, { startTick: points[0].tick, stopTick: points.at(-1).tick });
    }
    if (channel.automationMode.value !== 'read') await channel.setAutomationMode('read');
  },

  async insertAll(luna, session, channel, inserts) {
    for (const insert of inserts) {
      const effect = await AddOnLogic.loadEffect(luna, channel, insert);
      await AddOnLogic.writeAutomation(session, channel, effect, insert.automation);
      await AddOnLogic.setParams(effect, insert.params);
    }
  },

  async runInsert(luna, session, suggestion) {
    const channel = await AddOnLogic.channelFor(session, suggestion.target);
    if (!channel) throw new Error(`There's no track called ${suggestion.target}. Import the set first.`);
    await channel.loadStrip?.();
    await AddOnLogic.insertAll(luna, session, channel, suggestion.action.inserts);
    for (const name of suggestion.action.alsoOn ?? []) {
      const copy = await AddOnLogic.channelFor(session, name);
      if (!copy) continue;
      await copy.loadStrip();
      await AddOnLogic.insertAll(luna, session, copy, suggestion.action.inserts.map((i) => ({ ...i, slot: `effect_${Number(i.slot.split('_')[1]) + 1}` })));
    }
  },

  /** A filtered copy of a track, crossfaded against the original the way Live's Dry/Wet does. */
  async runCrossfade(luna, session, suggestion) {
    const { wetName, insert, also, wetLane, dryLane } = suggestion.action;
    const dry = await AddOnLogic.channelFor(session, suggestion.target);
    if (!dry) throw new Error(`There's no track called ${suggestion.target}. Import the set first.`);
    await dry.loadStrip();
    await session.playlists.load();
    const origin = session.musical.timeline.tickAtBar(1);
    let wet = session.visibleTracks.find((t) => t.name.value === wetName);
    if (!wet) {
      [wet] = await session.newTrack({ name: wetName, trackType: 'audio', stemFormat: dry.stemFormat.value ?? 'stereo' });
      await wet.loadStrip();
      const lane = await AddOnLogic.whenFound(session.playlists, () => session.playlistFor(dry), `the lane of ${suggestion.target}`);
      await lane.clips.load();
      const clips = [...lane.clips.items];
      await session.transaction(`Copy ${suggestion.target}`, async () => {
        for (const clip of clips) {
          await session.insertClip({ trackUid: wet.id, sourceUid: clip.sourceUid.value, name: clip.name.value, startTick: clip.start.value, stopTick: clip.stop.value, sourceStartNs: clip.sourceStart.value, sourceStopNs: clip.sourceStop.value });
        }
      });
      await wet.color.set(dry.color.value);
    }
    await AddOnLogic.insertAll(luna, session, wet, [insert, ...also]);
    const tick = (beat) => origin + Math.round(beat * Luna.TICKS_PER_BEAT);
    for (const [channel, lane] of [[wet, wetLane], [dry, dryLane]]) {
      const points = lane.map((p) => ({ tick: tick(p.beat), value: p.real }));
      await channel.gain.automation.insert(points, { startTick: points[0].tick, stopTick: points.at(-1).tick });
      if (channel.automationMode.value !== 'read') await channel.setAutomationMode('read');
    }
  },

  /** A return bus with its plugins, and every track's send at Live's level. */
  async runReturn(luna, session, { name, send, inserts, levels }) {
    await session.buses.load();
    const bus = session.buses.items.find((b) => b.name.value === name) ?? await session.newBus({ name, stemFormat: 'stereo' });
    for (const [index, insert] of inserts.entries()) {
      const effect = await AddOnLogic.loadEffect(luna, bus, { ...insert, slot: `effect_${index}` });
      await AddOnLogic.setParams(effect, insert.params);
    }
    for (const level of levels) {
      const track = session.visibleTracks.find((t) => t.name.value === level.track);
      if (!track) continue;
      await track.outputs.load();
      const output = track.outputs.byId(send);
      await output.controls.load();
      // The level goes first, so the send can never pass audio louder than Live's.
      await output.gain.real.set(level.gainDb);
      if (!output.destinations.value?.some((d) => d.busUid === bus.id)) await output.retarget(bus.id);
    }
  },

  /** A new audio track holding the slices a sampler would have played. */
  async runSlices(session, { name, sourcePath, clips, gainDb, muted }) {
    if (session.visibleTracks.some((t) => t.name.value === name)) return;
    const source = await session.importPath(sourcePath);
    const [track] = await session.newTrack({ name, trackType: 'audio', stemFormat: source.stemFormat.value ?? 'stereo' });
    await track.loadStrip();
    const origin = session.musical.timeline.tickAtBar(1);
    const tick = (beat) => origin + Math.round(beat * Luna.TICKS_PER_BEAT);
    await session.transaction(`Place ${name}`, async () => {
      for (const clip of clips) {
        await session.insertClip({ trackUid: track.id, sourceUid: source.id, name, startTick: tick(clip.startBeat), stopTick: tick(clip.stopBeat), sourceStartNs: Math.round(clip.sourceStartSec * 1e9), sourceStopNs: Math.round(clip.sourceStopSec * 1e9) });
      }
    });
    if (Math.abs(gainDb) > 0.01) await track.gain.real.set(gainDb);
    if (muted) await track.mute.real.set(true);
  },

  async runColours(session, { colours }) {
    await session.transaction('Copy track colours', async () => {
      for (const track of session.visibleTracks) {
        const colour = colours[track.name.value];
        if (colour !== undefined) await track.color.set(colour);
      }
    });
  },

  /** Run one suggestion. Safe to run twice: it reuses what an earlier GO made. */
  async runSuggestion(luna, session, suggestion) {
    await session.ready();
    await session.musical.ready();
    const { action } = suggestion;
    if (action.kind === 'insert') return AddOnLogic.runInsert(luna, session, suggestion);
    if (action.kind === 'crossfade') return AddOnLogic.runCrossfade(luna, session, suggestion);
    if (action.kind === 'return') return AddOnLogic.runReturn(luna, session, action);
    if (action.kind === 'slices') return AddOnLogic.runSlices(session, action);
    if (action.kind === 'colours') return AddOnLogic.runColours(session, action);
    throw new Error(`Unknown suggestion ${action.kind}.`);
  },

  /** What InterDAW itself added, which isn't your work and doesn't go back as such. */
  standIns(plan) {
    const plugins = new Map();
    const tracks = new Set();
    const buses = new Set();
    const allow = (name, inserts) => {
      if (!plugins.has(name)) plugins.set(name, new Set());
      for (const insert of inserts) plugins.get(name).add(insert.pluginId);
    };
    const faderLanes = new Set();
    for (const { target, action } of plan.suggestions ?? []) {
      if (action.kind === 'insert') {
        allow(target, action.inserts);
        for (const name of action.alsoOn ?? []) allow(name, action.inserts);
      } else if (action.kind === 'crossfade') {
        tracks.add(action.wetName);
        allow(action.wetName, [action.insert, ...action.also]);
        faderLanes.add(target);
        faderLanes.add(action.wetName);
      } else if (action.kind === 'slices') {
        tracks.add(action.name);
      } else if (action.kind === 'return') {
        buses.add(action.name);
      }
    }
    return { plugins, tracks, buses, faderLanes };
  },

  /** Compare a track's clips with what was imported, in ticks and nanoseconds. */
  clipsMatch(clips, spec, origin) {
    if (clips.length !== spec.clips.length) return false;
    const tick = (beat) => origin + Math.round(beat * Luna.TICKS_PER_BEAT);
    return spec.clips.every((want) => {
      const clip = clips.find((c) => c.start.value === tick(want.startBeat));
      return clip
        && clip.stop.value === tick(want.stopBeat)
        && Math.abs(clip.sourceStart.value - want.sourceStartSec * 1e9) < 1e5
        && Math.abs(clip.sourceStop.value - want.sourceStopSec * 1e9) < 1e5
        && Math.abs((clip.gain.value ?? 0) - want.gainDb) < 0.05
        && Boolean(clip.muted.value) === want.muted;
    });
  },

  /** Your plugins on a channel: anything loaded that InterDAW didn't suggest for it. */
  async ownPlugins(channel, allowed) {
    await channel.effects.load();
    return channel.effects.items.filter((e) => e.isEffectSlot && e.pluginId.value && !allowed?.has(e.pluginId.value));
  },

  /** How one imported track differs from the set: answers what has to go back for it. */
  async compareTrack(session, plan, track, spec, known, origin) {
    await track.loadStrip();
    const lane = session.playlistFor(track);
    if (lane) await lane.clips.load();
    const clips = lane?.clips.items ?? [];
    const reasons = [];
    const name = spec.name;

    if (spec.kind === 'audio') {
      if (!AddOnLogic.clipsMatch(clips, spec, origin)) reasons.push('clips edited');
    } else {
      let notes = 0;
      for (const clip of clips) {
        if (clip.midi) {
          await clip.midi.ready();
          notes += clip.midi.notes.items.length;
        }
      }
      if (notes !== spec.notes.length) reasons.push('notes edited');
    }
    const own = await AddOnLogic.ownPlugins(track, known.plugins.get(name));
    if (own.length) reasons.push('plugins added');
    for (const pan of track.main.pans) {
      const centre = pan.id === 'pan' ? 0 : pan.id === 'pan_left' ? -1 : 1;
      if (Math.abs((pan.real.value ?? centre) - centre) > 0.01) {
        reasons.push('panned');
        break;
      }
    }
    await track.gain.automation.load();
    if (track.gain.automation.items.length && !known.faderLanes.has(name)) reasons.push('fader automated');
    if (reasons.length) return { name, id: track.id, status: 'print', reasons };

    const move = { track: name };
    const gain = track.gain.real.value ?? 0;
    if (!known.faderLanes.has(name) && Math.abs(gain - spec.gainDb) > 0.05) move.gainDb = gain;
    if (track.mute.on !== spec.muted) move.muted = track.mute.on;
    const sends = [];
    for (const { action } of plan.suggestions ?? []) {
      if (action.kind !== 'return' || !known.buses.has(action.name)) continue;
      const level = action.levels.find((l) => l.track === name);
      if (!session.buses.items.some((b) => b.name.value === action.name) || !level) continue;
      await track.outputs.load();
      const output = track.outputs.byId(action.send);
      if (!output) continue;
      await output.controls.load();
      const now = output.gain.real.value;
      if (typeof now === 'number' && now > -100 && Math.abs(now - level.gainDb) > 0.5) sends.push({ index: Number(action.send.split('_')[1]), gainDb: now });
    }
    if (sends.length) move.sends = sends;
    if (Object.keys(move).length > 1) return { name, id: track.id, status: 'mix', reasons: [], move };
    return { name, id: track.id, status: 'same', reasons: [] };
  },

  /** Everything that has to go back, by comparing the session with the set it came from. */
  async compareSession(session, plan) {
    await session.ready();
    await session.musical.ready();
    await session.buses.load();
    await session.playlists.load();
    const origin = session.musical.timeline.tickAtBar(1);
    const known = AddOnLogic.standIns(plan);
    const byName = new Map(plan.tracks.map((t) => [t.name, t]));
    const rows = [];
    for (const track of session.visibleTracks) {
      const name = track.name.value;
      const spec = byName.get(name);
      if (spec) rows.push(await AddOnLogic.compareTrack(session, plan, track, spec, known, origin));
      else if (known.tracks.has(name)) rows.push({ name, id: track.id, status: 'standIn', reasons: [] });
      else rows.push({ name, id: track.id, status: 'new', reasons: ['new in LUNA'] });
    }
    // A filter copy has to print if the track it crossfades with does, or the sound would change.
    for (const { target, action } of plan.suggestions ?? []) {
      if (action.kind !== 'crossfade' || rows.find((r) => r.name === target)?.status !== 'print') continue;
      const copy = rows.find((r) => r.name === action.wetName);
      if (copy) copy.status = 'new';
    }
    for (const bus of session.buses.items) {
      if (!bus.isMaster && bus.busType.value === 'aux' && !known.buses.has(bus.name.value)) {
        rows.push({ name: bus.name.value, id: bus.id, status: 'new', reasons: ['new bus'], isBus: true });
      }
    }
    const removed = plan.tracks.map((t) => t.name).filter((n) => !session.visibleTracks.some((t) => t.name.value === n));
    const warnings = [];
    if (Math.abs(session.musical.tempoAt(origin) - plan.tempo) > 0.001) {
      warnings.push(`The session is now ${session.musical.tempoAt(origin)} BPM, but the set is still ${plan.tempo} BPM, so printed tracks won't line up with untouched ones. Set the tempo back, or send stems only.`);
    }
    return { rows, removed, warnings };
  },

  /** Whether this session holds the set yet. Until it does, there's nothing to send back. */
  isImported(session, plan) {
    return plan.tracks.some((t) => session.visibleTracks.some((v) => v.name.value === t.name));
  },

  /** The counts the panel shows before sending. */
  exportCounts(diff) {
    const count = (status) => diff.rows.filter((r) => r.status === status).length;
    return { same: count('same'), mix: count('mix'), print: count('print'), added: count('new'), removed: diff.removed.length };
  },

  /** A file name per stem, numbered in track order so any DAW lists them the same way. */
  stemName(index, name) {
    const safe = name.replace(/[\\/:*?"<>|\u0000-\u001f]/g, '-').trim().replace(/^\.+|\.+$/g, '') || 'Untitled';
    return `${String(index + 1).padStart(2, '0')} ${safe}.wav`;
  },

  /** Whether a channel gets rendered. A Live set only needs what it can't already play itself. */
  wantsStem(row, silent, format) {
    const changed = row.status === 'print' || row.status === 'new';
    if (format === 'live') return changed;
    if (format === 'stems') return !silent;
    return changed || !silent;
  },

  /** The last tick anything plays, with a bar of room for plugin tails. */
  async endTick(session, origin) {
    let end = origin;
    for (const track of session.visibleTracks) {
      const lane = session.playlistFor(track);
      if (!lane) continue;
      await lane.clips.load();
      for (const clip of lane.clips.items) end = Math.max(end, clip.stop.value);
    }
    return end + 4 * Luna.TICKS_PER_BEAT;
  },

  async helperPost(path, body, fetchFn = fetch) {
    let answer;
    try {
      answer = await fetchFn(`${AddOnLogic.HELPER_URL}${path}`, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body) });
    } catch {
      throw new Error('The InterDAW helper is not running. Install it as the panel shows, then try again.');
    }
    const reply = await answer.json();
    if (!answer.ok) throw new Error(reply.error ?? `The helper answered ${answer.status}.`);
    return reply;
  },

  /** How long a render may show no progress before it's treated as stuck. */
  STALL_MS: 90000,
  STEMS_PER_RENDER: 4,

  /** A render removes itself once finished, sometimes before it's seen to finish. The helper
   * checks each file really arrived, so that one rejection isn't a failure. A render that stops
   * moving is cancelled, because a stuck one keeps LUNA itself busy until it's stopped. */
  async finishRender(result, onProgress) {
    const render = result.render;
    let lastMove = Date.now();
    const stop = render.progress.onChange(() => {
      lastMove = Date.now();
      onProgress?.(render.progress.value ?? 0);
    });
    let stalled = false;
    const watchdog = setInterval(() => {
      if (Date.now() - lastMove > AddOnLogic.STALL_MS) {
        stalled = true;
        clearInterval(watchdog);
        render.abort().catch(() => {});
      }
    }, 1000);
    try {
      await render.done(3 * 60 * 60 * 1000);
    } catch (e) {
      if (stalled) throw new Error('LUNA stopped rendering, so the send was cancelled. Nothing else in the session changed. Try again without touching the transport.');
      if (e.code !== 'removed') throw e;
    } finally {
      clearInterval(watchdog);
      stop();
    }
  },

  /**
   * Make the return folder: stems of everything that sounds, a mix reference, and their set with
   * your work added. Audio is rendered offline and bar 1 lines up with the set's first beat.
   */
  async runExport(session, plan, parent, onStep = () => {}, fetchFn = fetch, format = 'both') {
    const diff = await AddOnLogic.compareSession(session, plan);
    const origin = session.musical.timeline.tickAtBar(1);
    const range = { startTick: origin, stopTick: await AddOnLogic.endTick(session, origin) };
    const { folder } = await AddOnLogic.helperPost('/return/prepare', { setPath: plan.source.path, parent, format }, fetchFn);

    const stems = [];
    for (const row of diff.rows) {
      const channel = row.isBus ? session.buses.byId(row.id) : session.tracks.byId(row.id);
      if (!row.isBus) {
        await channel.loadStrip();
        const lane = session.playlistFor(channel);
        if (lane) await lane.clips.load();
        const silent = !lane?.clips.items.length || channel.mute.on;
        if (!AddOnLogic.wantsStem(row, silent, format)) continue;
      } else if (!AddOnLogic.wantsStem(row, false, format)) continue;
      stems.push({ row, file: AddOnLogic.stemName(stems.length, row.name) });
    }
    if (session.transport.playing.value) await session.transport.stop();
    // A few at a time: printing everything in one render has stalled LUNA's engine.
    for (let start = 0; start < stems.length; start += AddOnLogic.STEMS_PER_RENDER) {
      const batch = stems.slice(start, start + AddOnLogic.STEMS_PER_RENDER);
      const text = `Printing stems ${start + 1}-${start + batch.length} of ${stems.length}`;
      const before = start / stems.length;
      onStep({ text, fraction: before * 0.8 });
      const result = await session.bounce({ channelUids: batch.map((s) => s.row.id), range, outputPaths: batch.map((s) => `${folder}/Stems/${s.file}`), recordPoint: 'post_pan', name: 'InterDAW stems' });
      await AddOnLogic.finishRender(result, (fraction) => onStep({ text, fraction: (before + (fraction * batch.length) / stems.length) * 0.8 }));
    }
    onStep({ text: 'Printing the mix reference', fraction: 0.8 });
    const reference = await session.bounce({ channelUids: [session.master.id], range, outputPaths: [`${folder}/Mix Reference.wav`], recordPoint: 'post_pan', name: 'InterDAW mix reference' });
    await AddOnLogic.finishRender(reference, (fraction) => onStep({ text: 'Printing the mix reference', fraction: 0.8 + fraction * 0.1 }));

    onStep({ text: 'Copying audio and writing the set', fraction: 0.9 });
    const prints = format === 'stems' ? [] : stems.filter((s) => s.row.status === 'print' || s.row.status === 'new')
      .map((s) => ({ file: s.file, name: s.row.name, original: s.row.status === 'print' ? s.row.name : null }));
    const moves = format === 'stems' ? [] : diff.rows.filter((r) => r.status === 'mix').map((r) => r.move);
    // Stems carry the session's own tempo, so a tempo change only matters to a set.
    const warnings = format === 'stems' ? [] : diff.warnings;
    const built = await AddOnLogic.helperPost('/return/build', { folder, setPath: plan.source.path, prints, moves, removed: format === 'stems' ? [] : diff.removed, warnings, format, sessionTempo: session.musical.tempoAt(origin) }, fetchFn);
    return { ...built, counts: AddOnLogic.exportCounts(diff) };
  },

  /**
   * Build a plan in the session. A track that fails is recorded and the rest carry on, so one
   * bad clip doesn't cost the whole import. Audio is copied into the session, never moved.
   */
  async runImport(session, plan, onStep = () => {}) {
    let step = 0;
    const done = (text) => onStep({ step: ++step, total: AddOnLogic.stepCount(plan), text });
    const failed = [];

    await session.ready();
    await session.musical.ready();
    const originTick = session.musical.timeline.tickAtBar(1);
    await session.musical.setTempoInEffectAt(plan.tempo, originTick);
    done(`Tempo ${plan.tempo}`);
    await session.musical.setTimeSignature(plan.timeSignature.numerator, plan.timeSignature.denominator, 1);
    done(`Meter ${plan.timeSignature.numerator}/${plan.timeSignature.denominator}`);

    const sourceUids = new Map();
    for (const path of plan.sources) {
      try {
        sourceUids.set(path, (await session.importPath(path)).id);
      } catch (e) {
        failed.push(`${path}: ${e.message}`);
      }
      done(`Copied ${path.slice(path.lastIndexOf('/') + 1)}`);
    }

    let tracks = 0;
    for (const spec of plan.tracks) {
      const placeable = { ...spec, clips: spec.clips.filter((c) => sourceUids.has(c.sourcePath)) };
      try {
        await AddOnLogic.importTrack(session, placeable, originTick, sourceUids);
        tracks += 1;
      } catch (e) {
        failed.push(`${spec.name}: ${e.message}`);
      }
      done(`Track ${spec.name}`);
    }

    for (const locator of plan.locators) {
      try {
        await session.addMarker(originTick + Math.round(locator.beat * Luna.TICKS_PER_BEAT), locator.name);
      } catch (e) {
        failed.push(`Marker ${locator.name}: ${e.message}`);
      }
      done(`Marker ${locator.name}`);
    }
    return { tracks, failed };
  },
};

globalThis.AddOnLogic = AddOnLogic;
