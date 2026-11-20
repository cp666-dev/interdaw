"""Turn what a Live set does into suggestions the InterDAW panel can run with GO.

Each suggestion carries a plain-data action for logic.js. `match` is honest about the result:
  exact   the same data, such as colours
  close   a stand-in set to the same values, which won't null against Live
  check   a stand-in that can't reach Live's values, or a setting that needs your ear
Anything with no audible effect goes in `notes`, so the panel doesn't ask you to act on it.
"""

import math

# UADx plugins. Apple's Audio Units load in LUNA here but measured as having no effect on the
# audio when loaded this way, so only plugins a render has proved are used.
SSL = 'uaudio_ssl_e_channel_strip'
API = 'uaudio_api_vision_channel_strip'
PLATE = 'uaudio_pure_plate'
ECHO = 'uaudio_galaxy_tape_echo'

LOWPASS, HIGHPASS, BELL, NOTCH, LOW_SHELF, HIGH_SHELF = 'low pass', 'high pass', 'bell', 'notch', 'low shelf', 'high shelf'
# Live's Eq8 modes, as (kind, slope in dB per octave).
EQ8_MODES = {0: (HIGHPASS, 48), 1: (HIGHPASS, 12), 2: (LOW_SHELF, 0), 3: (BELL, 0), 4: (NOTCH, 0),
             5: (HIGH_SHELF, 0), 6: (LOWPASS, 12), 7: (LOWPASS, 48)}
CUTS = (LOWPASS, HIGHPASS, NOTCH)
SILENT = 0.01
STEP = 1 / 960
SSL_HP_MAX, SSL_LP_MIN, SSL_SLOPE = 350.0, 3000.0, 18
API_HP_MAX, API_LP_MIN, API_LP_OPEN, API_HP_OPEN, API_SLOPE = 596.0, 643.0, 40800.0, 12.0, 12
SSL_Q_MIN, SSL_Q_MAX = 0.5, 3.0
# (control prefix, lowest Hz, highest Hz, can it be a bell, can it be a shelf)
SSL_BANDS = (('lf', 30.0, 450.0, True, True), ('lmf', 200.0, 2500.0, True, False),
             ('hmf', 600.0, 7000.0, True, False), ('hf', 1500.0, 16000.0, True, True))

# Live's 70 track colours, in its own order.
LIVE_COLOURS = (
    'FF94A6 FFA529 CC9927 F7F47C BFFB00 1AFF2F 25FFA8 5CFFE8 8BC5FF 5480E4 92A7FF D86CE4 E553A0 FFFFFF '
    'FF3636 F66C03 99724B FFF034 87FF67 3DC300 00BFAF 19E9FF 10A4EE 007DC0 886CE4 B677C6 FF39D4 D0D0D0 '
    'E2675A FFA374 D3AD71 EDFFAE D2E498 BAD074 9BC48D D4FDE1 CDF1F8 B9C1E3 CDBBE4 AE98E5 E5DCE1 A9A9A9 '
    'C6928B B78256 99836A BFBA69 A6BE00 7DB04B 88C2BA 9BB3C4 85A5C2 8393CC A595B5 BF9FBE BC7196 7B7B7B '
    'AF3333 A95131 724F41 DBC300 85961F 539F31 0A9C8E 236384 1A2F96 2F52A2 624BAD A34BAD CC2E6E 3C3C3C'
).split()


def _value(element, path, default=None):
    found = None if element is None else element.find(path)
    return default if found is None else found.get('Value', default)


def _float(element, path, default=0.0):
    raw = _value(element, path)
    return default if raw is None else float(raw)


def _on(device):
    return _value(device, 'On/Manual', 'true') == 'true'


def _db(linear):
    return 20 * math.log10(linear) if linear > 0 else -144.0


def fourcc(number):
    return int(number).to_bytes(4, 'big').decode('latin-1')


def eq8_bands(device):
    """The bands that change the sound, as (kind, Hz, dB, Q, slope)."""
    bands = []
    for index in range(8):
        band = device.find(f'Bands.{index}/ParameterA')
        if band is None or _value(band, 'IsOn/Manual') != 'true':
            continue
        kind, slope = EQ8_MODES[int(_value(band, 'Mode/Manual', '3'))]
        gain = _float(band, 'Gain/Manual')
        if kind not in CUTS and abs(gain) < SILENT:
            continue
        bands.append((kind, _float(band, 'Freq/Manual'), gain, _float(band, 'Q/Manual', 0.707), slope))
    return bands


def _describe(bands):
    parts = []
    for kind, freq, gain, _, slope in bands:
        parts.append(f'{kind} {freq:.0f} Hz' + (f' {slope} dB/oct' if kind in (LOWPASS, HIGHPASS) else '' if kind == NOTCH else f' {gain:+.1f} dB'))
    return ', '.join(parts)


def _ssl_base():
    """The SSL as a clean EQ: its dynamics and transformer colour out of the path."""
    return [{'id': 'transformer', 'display': 'Off'}, {'id': 'dyn_in', 'display': 'Out'}, {'id': 'eq_in', 'display': 'In'}]


def fit_eq(bands, global_gain=0.0):
    """Fit bands onto the SSL and API filters. Answers (inserts, limits), where limits say in plain
    words what the plugins can't quite do, or None if nothing fits."""
    ssl = []
    api = []
    limits = []
    free = list(SSL_BANDS)
    for kind, freq, gain, q, slope in sorted(bands, key=lambda b: b[1]):
        if kind == HIGHPASS:
            if freq <= SSL_HP_MAX:
                ssl.append({'id': 'hp_freq', 'real': freq})
                if slope != SSL_SLOPE:
                    limits.append(f'its high pass is {SSL_SLOPE} dB/oct, not {slope}')
            else:
                api.append({'id': '215_hp_filter', 'real': min(freq, API_HP_MAX)})
                if freq > API_HP_MAX:
                    limits.append(f'its high pass stops at {API_HP_MAX:.0f} Hz, not {freq:.0f}')
                if slope != API_SLOPE:
                    limits.append(f'its high pass is {API_SLOPE} dB/oct, not {slope}')
        elif kind == LOWPASS:
            if freq >= SSL_LP_MIN:
                ssl.append({'id': 'lp_freq', 'real': freq})
                if slope != SSL_SLOPE:
                    limits.append(f'its low pass is {SSL_SLOPE} dB/oct, not {slope}')
            else:
                api.append({'id': '215_lp_filter', 'real': max(freq, API_LP_MIN)})
                if freq < API_LP_MIN:
                    limits.append(f'its low pass only reaches down to {API_LP_MIN:.0f} Hz, not {freq:.0f}, so the top end stays brighter than Live')
                if slope != API_SLOPE:
                    limits.append(f'its low pass is {API_SLOPE} dB/oct, not {slope}')
        else:
            shelf = kind in (LOW_SHELF, HIGH_SHELF)
            slot = next((b for b in free if b[1] <= freq <= b[2] and (b[4] if shelf else b[3])), None)
            if slot is None:
                limits.append(f'its EQ has no free band for the {kind} at {freq:.0f} Hz')
                continue
            free.remove(slot)
            name = slot[0]
            ssl += [{'id': f'{name}_freq', 'real': freq}, {'id': f'{name}_gain', 'real': gain}]
            if name in ('lf', 'hf'):
                ssl.append({'id': f'{name}_bell', 'display': 'Shelf' if shelf else 'Bell'})
            if name in ('lmf', 'hmf'):
                ssl.append({'id': f'{name}_q', 'real': min(max(q, SSL_Q_MIN), SSL_Q_MAX)})
                if not SSL_Q_MIN <= q <= SSL_Q_MAX:
                    limits.append(f'its Q stops short of {q:.2f} at {freq:.0f} Hz')
            if kind == NOTCH:
                limits.append(f'its notch at {freq:.0f} Hz is a deep bell')
    inserts = []
    if ssl or abs(global_gain) >= SILENT:
        inserts.append({'pluginId': SSL, 'params': _ssl_base() + ssl + ([{'id': 'output', 'real': global_gain}] if abs(global_gain) >= SILENT else []), 'automation': []})
    if api:
        inserts.append({'pluginId': API, 'params': [{'id': '215_on', 'display': 'On'}] + api, 'automation': []})
    return inserts, limits


def _limits_text(limits):
    return f' LUNA\'s closest: {"; ".join(dict.fromkeys(limits))}.' if limits else ''


def _events(target_id, envelopes):
    """(beat, value) events of the lane aimed at an automation target, leaving out Live's default."""
    for envelope in envelopes:
        if _value(envelope, 'EnvelopeTarget/PointeeId') == target_id:
            found = []
            for event in envelope.find('Automation/Events'):
                time = float(event.get('Time'))
                raw = event.get('Value')
                found.append((time, 1.0 if raw == 'true' else 0.0 if raw == 'false' else float(raw)))
            return found
    return []


def _target_id(device, parameter):
    target = device.find(f'{parameter}/AutomationTarget')
    return None if target is None else target.get('Id')


def _on_lane_points(events, value, off):
    """Automation that holds a control at `off` or `value` the way Live's device On lane does."""
    start = next((v for time, v in events if time < 0), 1.0)
    current = start
    points = [{'beat': 0.0, **(value if start else off)}]
    for time, on in events:
        if time < 0 or on == current:
            continue
        points.append({'beat': time - STEP, **(value if current else off)})
        points.append({'beat': time, **(value if on else off)})
        current = on
    return points, start


def _fader_db(level):
    return 20 * math.log10(level) if level > 0.0001 else -144.0


def _crossfade_lanes(events):
    """Fader lanes for a filtered copy and the dry track that together do what Live's Dry/Wet does:
    a linear crossfade. Faders work in dB, so each ramp is drawn in small steps."""
    wet, dry = [], []
    for (t0, w0), (t1, w1) in zip(events, events[1:]):
        if t0 < 0:
            # LUNA doesn't hold a lane's first value before its first point, so anchor it at bar 1.
            wet.append({'beat': 0.0, 'real': _fader_db(w0)})
            dry.append({'beat': 0.0, 'real': _fader_db(1 - w0)})
            t0 = t1
        steps = 16 if w0 != w1 else 1
        for step in range(steps + 1):
            level = w0 + (w1 - w0) * step / steps
            beat = t0 + (t1 - t0) * step / steps
            wet.append({'beat': beat, 'real': _fader_db(level)})
            dry.append({'beat': beat, 'real': _fader_db(1 - level)})
    return wet, dry


def _slice_list(simpler):
    part = simpler.find('.//MultiSamplePart')
    style = _value(simpler, 'SimplerSlicing/Style', '0')
    wrapper = {'0': 'SlicePoints', '1': 'BeatSlicePoints', '2': 'RegionSlicePoints', '3': 'ManualSlicePoints'}.get(style, 'SlicePoints')
    found = part.find(f'.//{wrapper}')
    times = [] if found is None else sorted({float(p.get('TimeInSeconds')) for p in found.findall('SlicePoint')})
    return part, times


def simpler_slices(simpler, notes, seconds_per_beat):
    """The audio a slicing Simpler plays for these notes, as clips. Slices start at note 36."""
    if _value(simpler, 'Globals/PlaybackMode') != '2':
        return None
    part, times = _slice_list(simpler)
    if not times:
        return None
    rate = _float(part, 'SampleRef/DefaultSampleRate', 44100.0) or 44100.0
    end = _float(part, 'SampleEnd') / rate
    clips = []
    for note in notes:
        index = note['pitch'] - 36
        if 0 <= index < len(times):
            start = times[index]
            stop = times[index + 1] if index + 1 < len(times) else end
            clips.append({'startBeat': note['startBeat'], 'stopBeat': note['startBeat'] + (stop - start) / seconds_per_beat,
                          'sourceStartSec': start, 'sourceStopSec': stop})
    return clips or None


class Builder:
    def __init__(self, tempo):
        self.tempo = tempo
        self.suggestions = []
        self.notes = []
        self.unmatched = []

    def add(self, key, title, why, match, target, action, audible=True):
        self.suggestions.append({'id': key, 'title': title, 'why': why, 'match': match, 'target': target,
                                 'audible': audible, 'action': action})

    def device(self, device, slot, target, label, envelopes, audible):
        """Add a suggestion for one device. Answers how many insert slots it takes."""
        tag = device.tag
        on_events = _events(_target_id(device, 'On'), envelopes)
        toggles = len({value for _, value in on_events}) > 1
        if not _on(device) and not toggles:
            self.notes.append(f'{label}: {tag} is switched off in Live, so there is nothing to match.')
            return 0
        key = f'{target}:{slot}:{tag}'
        where = 'the main output' if target == '__main__' else label

        def action(inserts):
            for offset, insert in enumerate(inserts):
                insert['slot'] = f'effect_{slot + offset}'
            return {'kind': 'insert', 'inserts': inserts}

        if tag == 'Eq8':
            bands = eq8_bands(device)
            gain = _float(device, 'GlobalGain/Manual')
            if not bands and abs(gain) < SILENT:
                self.notes.append(f'{label}: its EQ Eight changes nothing at these settings.')
                return 0
            inserts, limits = fit_eq(bands, gain)
            self.add(key, f'Add EQ on {where}', f'Live\'s EQ Eight: {_describe(bands)}.{_limits_text(limits)}', 'check' if limits else 'close', target, action(inserts), audible)
            return len(inserts)

        if tag == 'AutoFilter2':
            kind = {0: LOWPASS, 1: HIGHPASS}.get(int(_value(device, 'Filter_Type/Manual', '-1')))
            if kind is None or _float(device, 'Lfo_Amount/Manual') > 0:
                self.unmatched.append(f'{label}: this Auto Filter setting has no stand-in yet. Print it in Live.')
                return 0
            slope = 24 if _value(device, 'Filter_Slope/Manual') == '1' else 12
            freq = _float(device, 'Filter_Frequency/Manual')
            control, reach, open_freq = ('215_lp_filter', max(freq, API_LP_MIN), API_LP_OPEN) if kind == LOWPASS else ('215_hp_filter', min(freq, API_HP_MAX), API_HP_OPEN)
            limits = [] if slope == API_SLOPE else [f'its filter is {API_SLOPE} dB/oct, not {slope}']
            if reach != freq:
                limits.append(f'its filter only reaches {reach:.0f} Hz, not {freq:.0f}')
            why = f'Live\'s Auto Filter: {kind} {freq:.0f} Hz.'
            insert = {'pluginId': API, 'slot': 'effect_0', 'params': [{'id': '215_on', 'display': 'On'}, {'id': control, 'real': reach}], 'automation': []}
            events = _events(_target_id(device, 'DryWet'), envelopes)
            if len({value for _, value in events}) > 1 and target != '__main__':
                wet_name = f'{target} (filter)'
                wet, dry = _crossfade_lanes(events)
                moments = sorted({int(t // 4) + 1 for (t, a), (_, b) in zip(events, events[1:]) if t >= 0 and a != b})
                why += f' Live crossfades its Dry/Wet around bar {moments[0]}, so this adds a filtered copy of the track and crossfades the two the same way.' if moments else ''
                self.add(key, f'Add filter crossfade on {where}', why + _limits_text(limits), 'check', target,
                         {'kind': 'crossfade', 'wetName': wet_name, 'insert': insert, 'also': [], 'wetLane': wet, 'dryLane': dry}, audible)
                return 0
            self.add(key, f'Add filter on {where}', why + _limits_text(limits), 'check', target, action([insert]), audible)
            return 1

        if tag == 'FilterEQ3':
            low, mid, high = (_float(device, f'Gain{n}/Manual', 1.0) for n in ('Lo', 'Mid', 'Hi'))
            f_low, f_high = _float(device, 'FreqLo/Manual', 250.0), _float(device, 'FreqHi/Manual', 2500.0)
            slope = 48 if _value(device, 'Slope/Manual') == '1' else 24
            bands = []
            if low < SILENT:
                bands.append((HIGHPASS, f_low, 0.0, 0.7, slope))
            elif abs(low - 1) > SILENT:
                bands.append((LOW_SHELF, f_low, _db(low), 0.7, 0))
            if high < SILENT:
                bands.append((LOWPASS, f_high, 0.0, 0.7, slope))
            elif abs(high - 1) > SILENT:
                bands.append((HIGH_SHELF, f_high, _db(high), 0.7, 0))
            if abs(mid - 1) > SILENT:
                bands.append((BELL, math.sqrt(f_low * f_high), _db(mid), 0.7, 0))
            if not bands:
                self.notes.append(f'{label}: its EQ Three changes nothing at these settings.')
                return 0
            inserts, limits = fit_eq(bands)
            why = f'Live\'s EQ Three: {_describe(bands)}.'
            if toggles:
                # A cut switches between its value and fully open; gain bands switch to 0 dB.
                for insert in inserts:
                    for param in insert['params']:
                        if param['id'] in ('hp_freq', 'lp_freq', '215_hp_filter', '215_lp_filter'):
                            off = {'off': True} if param['id'] in ('hp_freq', 'lp_freq') else {'real': API_HP_OPEN if 'hp' in param['id'] else API_LP_OPEN}
                        elif param['id'].endswith('_gain'):
                            off = {'real': 0.0}
                        else:
                            continue
                        points, start = _on_lane_points(on_events, {'real': param['real']}, off)
                        insert['automation'].append({'id': param['id'], 'points': points})
                        if not start:
                            param.pop('real', None)
                            param.update(off)
                bars = ', '.join(f'bar {int(t // 4) + 1}' for t, on in on_events if t >= 0 and on)
                why += f' It switches on at {bars}, as in Live.'
            self.add(key, f'Add EQ on {where}', why + _limits_text(limits), 'check' if limits else 'close', target, action(inserts), audible)
            return len(inserts)

        if tag == 'Limiter':
            gain = _float(device, 'Gain/Manual')
            ceiling = _float(device, 'Ceiling/Manual')
            if abs(gain) < SILENT and abs(ceiling) < SILENT:
                self.unmatched.append(f'{label}: its Limiter only catches peaks above 0 dB, and you have no limiter in LUNA that loads here. Keep an eye on the master meter.')
            else:
                self.unmatched.append(f'{label}: its Limiter adds {gain:+.1f} dB into a {ceiling:+.1f} dB ceiling, and you have no limiter in LUNA that loads here. Print it in Live.')
            return 0

        if tag == 'AuPluginDevice':
            info = device.find('PluginDesc/AuPluginInfo')
            name = _value(info, 'Name')
            plugin_id = 'com.{}.au.{}_{}'.format(*(fourcc(_value(info, n)) for n in ('ComponentManufacturer', 'ComponentType', 'ComponentSubType')))
            params = [{'id': str(p), 'real': v} for p, v in _au_parameters(device)]
            self.add(key, f'Add {name} on {where}', 'LUNA lists the same Audio Unit. Live\'s saved value is copied across, so listen to confirm it.', 'check', target,
                     action([{'pluginId': plugin_id, 'pluginName': name, 'params': params, 'automation': []}]), audible)
            return 1

        if tag == 'PluginDevice':
            name = _value(device, 'PluginDesc/VstPluginInfo/PlugName') or _value(device, 'PluginDesc/Vst3PluginInfo/Name')
            self.add(key, f'Add {name} on {where}', 'Loads LUNA\'s version of this plug-in if you have it. Live\'s settings can\'t be read, so match them by ear.', 'check', target,
                     action([{'pluginId': None, 'pluginName': name, 'params': [], 'automation': []}]), audible)
            return 1

        if tag in ('OriginalSimpler', 'InstrumentVector', 'MultiSampler', 'MxDeviceMidiEffect'):
            return 0
        self.unmatched.append(f'{label}: {tag} has no stand-in yet. Print it in Live to keep its sound.')
        return 0

    def returns(self, track, index, track_elements):
        name = _value(track, 'Name/EffectiveName')
        devices = track.find('DeviceChain/DeviceChain/Devices')
        levels = []
        for element in track_elements:
            holders = element.findall('DeviceChain/Mixer/Sends/TrackSendHolder')
            if index < len(holders):
                levels.append({'track': _value(element, 'Name/EffectiveName'), 'gainDb': _db(_float(holders[index], 'Send/Manual'))})
        audible = any(level['gainDb'] > -60 for level in levels)
        inserts = []
        details = []
        check = False
        for device in ([] if devices is None else devices):
            if device.tag == 'Reverb' and _on(device):
                decay = _float(device, 'DecayTime/Manual') / 1000
                predelay = _float(device, 'PreDelay/Manual')
                inserts.append({'pluginId': PLATE, 'params': [{'id': 'mix', 'normalized': 1.0}, {'id': 'wet_solo', 'display': 'On'}, {'id': 'predelay', 'displayNumber': predelay}, {'id': 'decay', 'displayNumber': decay}]})
                details.append(f'Pure Plate with Live\'s {decay:.1f} s decay and {predelay:.1f} ms predelay; a plate, so the character differs')
                check = True
            elif device.tag == 'Delay' and _on(device):
                synced = _value(device, 'DelayLine_SyncL/Manual') == 'true'
                sixteenths = _float(device, 'DelayLine_SyncedSixteenthL/Manual') + 1
                seconds = sixteenths * 15 / self.tempo if synced else _float(device, 'DelayLine_TimeL/Manual')
                feedback = _float(device, 'Feedback/Manual')
                inserts.append({'pluginId': ECHO, 'params': [{'id': 'wet_solo', 'display': 'On'}, {'id': 'sync', 'display': 'Off'}, {'id': 'feedback', 'normalized': feedback}]})
                details.append(f'Galaxy Tape Echo with {feedback * 100:.0f}% feedback. Its time can\'t be set from here, so dial in {seconds * 1000:.0f} ms by ear')
                check = True
            elif _on(device):
                self.unmatched.append(f'Return {name}: {device.tag} has no stand-in yet.')
        why = f'{". ".join(details)}. ' if details else ''
        why += 'Every send is wired at Live\'s level.' if audible else 'Every send sits at -70 dB, as in Live, so this is silent until you turn one up.'
        self.add(f'return:{index}', f'Build return {name}', why, 'check' if check else 'close', '__session__',
                 {'kind': 'return', 'name': name, 'send': f'send_{index}', 'inserts': inserts, 'levels': levels}, audible)


def _au_parameters(device):
    """Parameters Live saved with a real id. Live pads the rest of the list with -1."""
    found = []
    parameters = device.find('ParameterList')
    for parameter in ([] if parameters is None else parameters):
        identifier = int(_value(parameter, 'ParameterId', '-1'))
        if identifier >= 0:
            found.append((identifier, float(_value(parameter, 'ParameterValue/Manual', '0'))))
    return found


def _share_with_filter_copy(suggestions, name):
    """Plugins after a crossfaded filter hear both sides in Live, so the copy needs them too. The
    copy keeps its filter in the first slot, and later plugins move along by one on both."""
    crossfade = next((s for s in suggestions if s['target'] == name and s['action']['kind'] == 'crossfade'), None)
    if crossfade is None:
        return
    for suggestion in suggestions:
        if suggestion['target'] == name and suggestion['action']['kind'] == 'insert':
            suggestion['action']['alsoOn'] = [crossfade['action']['wetName']]
            for insert in suggestion['action']['inserts']:
                wet_slot = int(insert['slot'].split('_')[1]) + 1
                crossfade['action']['also'].append({**insert, 'slot': f'effect_{wet_slot}'})


def _chain(track):
    devices = track.find('DeviceChain/DeviceChain/Devices')
    return [] if devices is None else list(devices)


def build(live_set, tempo, plan_tracks):
    """Answer (suggestions, notes, unmatched) for a whole set."""
    builder = Builder(tempo)
    seconds_per_beat = 60.0 / tempo
    main = live_set.find('MainTrack')
    if main is None:
        main = live_set.find('MasterTrack')
    envelopes = main.findall('AutomationEnvelopes/Envelopes/AutomationEnvelope')
    slot = 0
    for device in _chain(main):
        slot += builder.device(device, slot, '__main__', 'Main track', envelopes, True)

    elements = [t for t in live_set.find('Tracks') if t.tag in ('AudioTrack', 'MidiTrack')]
    by_name = {t['name']: t for t in plan_tracks}
    order = sorted(elements, key=lambda t: by_name[_value(t, 'Name/EffectiveName')]['muted'])
    for element in order:
        name = _value(element, 'Name/EffectiveName')
        plan_track = by_name[name]
        audible = not plan_track['muted']
        envelopes = element.findall('AutomationEnvelopes/Envelopes/AutomationEnvelope')
        slot = 0
        for device in _chain(element):
            slot += builder.device(device, slot, name, name, envelopes, audible)
        _share_with_filter_copy(builder.suggestions, name)
        if element.tag == 'MidiTrack':
            simpler = element.find('DeviceChain/DeviceChain/Devices/OriginalSimpler')
            notes = plan_track['notes']
            if simpler is not None and notes:
                clips = simpler_slices(simpler, notes, seconds_per_beat)
                sample = plan_track.get('samplePath')
                if clips and sample:
                    gain = _float(simpler, 'VolumeAndPan/Volume/Manual')
                    builder.add(f'{name}:slices', f'Play {name} as audio', f'Its {len(notes)} MIDI notes trigger slices of a sample in Simpler, so this places those same slices as audio clips on a new track and keeps the MIDI. Lengths assume Simpler plays each slice to its end.', 'check', name,
                                {'kind': 'slices', 'name': f'{name} (audio)', 'sourcePath': sample, 'clips': clips, 'gainDb': gain, 'muted': plan_track['muted']}, audible)
                elif sample or notes:
                    builder.unmatched.append(f'{name}: its MIDI needs an instrument in LUNA. Print it in Live to keep its sound.')
            elif not notes:
                builder.notes.append(f'{name}: no MIDI notes on the timeline, so there is nothing to play.')

    returns = [t for t in live_set.find('Tracks') if t.tag == 'ReturnTrack']
    for index, track in enumerate(returns):
        builder.returns(track, index, elements)

    colours = {}
    for element in live_set.find('Tracks'):
        index = int(_value(element, 'Color', '-1'))
        if element.tag in ('AudioTrack', 'MidiTrack') and 0 <= index < len(LIVE_COLOURS):
            colours[_value(element, 'Name/EffectiveName')] = int('FF' + LIVE_COLOURS[index], 16)
    if colours:
        builder.add('colours', 'Copy Live\'s track colours', f'Colours {len(colours)} tracks to match the set.', 'exact', '__session__',
                    {'kind': 'colours', 'colours': colours}, False)
    return builder.suggestions, builder.notes, builder.unmatched
