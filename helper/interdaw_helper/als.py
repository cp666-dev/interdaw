"""Read an Ableton Live set into an import plan the InterDAW panel can run.

A .als file is gzipped XML. The format is undocumented, so everything here comes from reading
real sets; anything the plan can't carry is reported in `warnings`, never dropped silently.
Times in the plan are Live beats on the arrangement, and seconds into the audio file.
"""

import gzip
import math
import os
import struct
import traceback
import xml.etree.ElementTree as ET

from .suggest import build

MAX_XML_BYTES = 512 * 1024 * 1024
# Above this a clip counts as stretched rather than playing at its recorded speed.
STRETCH_TOLERANCE = 0.001
# Live's send at minimum is -70 dB (0.000316), which is silent for this purpose.
SILENT_SEND = 0.001
MAX_SEGMENTS = 50000


class AlsError(Exception):
    pass


def _value(element, path, default=None):
    if element is None:
        return default
    found = element.find(path)
    return default if found is None else found.get('Value', default)


def _float(element, path, default=0.0):
    raw = _value(element, path)
    return default if raw is None else float(raw)


def _true(element, path):
    return _value(element, path) == 'true'


def _db(linear):
    return 20 * math.log10(linear) if linear > 0 else -144.0


def _load_xml(path):
    with gzip.open(path, 'rb') as f:
        data = f.read(MAX_XML_BYTES + 1)
    if len(data) > MAX_XML_BYTES:
        raise AlsError('This set is too large to read.')
    return ET.fromstring(data)


def _describe(error):
    """An unexpected error, short enough for the panel, with its type for a bug report."""
    return f'{type(error).__name__}: {error}'[:200]


def decode_time_signature(value):
    """Live packs a meter as denominator_index * 99 + numerator - 1 (4/4 is 201)."""
    index, numerator = divmod(int(value), 99)
    return {'numerator': numerator + 1, 'denominator': 2 ** index}


def audio_channels(path):
    """Channel count from a WAV or AIFF header, or None if it isn't readable."""
    try:
        with open(path, 'rb') as f:
            head = f.read(12)
            if head[:4] in (b'RIFF', b'RF64') and head[8:12] == b'WAVE':
                return _chunk_channels(f, b'fmt ', '<', 2)
            if head[:4] == b'FORM' and head[8:12] in (b'AIFF', b'AIFC'):
                return _chunk_channels(f, b'COMM', '>', 0)
    except OSError:
        pass
    return None


def _chunk_channels(f, wanted, endian, offset):
    while True:
        header = f.read(8)
        if len(header) < 8:
            return None
        size = struct.unpack(endian + 'I', header[4:])[0]
        if header[:4] == wanted:
            body = f.read(offset + 2)
            return struct.unpack(endian + 'H', body[offset:offset + 2])[0]
        f.seek(size + (size & 1), 1)


def resolve_sample(file_ref, als_dir):
    """The audio file a FileRef points at. Saved absolute paths are often stale, so the path
    relative to the set is tried first, then relative to its Live project folder."""
    relative = _value(file_ref, 'RelativePath') or ''
    absolute = _value(file_ref, 'Path') or ''
    candidates = []
    if relative:
        candidates.append(os.path.join(als_dir, relative))
        folder = als_dir
        while folder != os.path.dirname(folder):
            if os.path.isdir(os.path.join(folder, 'Ableton Project Info')):
                candidates.append(os.path.join(folder, relative))
                break
            folder = os.path.dirname(folder)
    if absolute:
        candidates.append(absolute)
    for candidate in candidates:
        if os.path.isfile(candidate):
            return os.path.normpath(candidate), None
    return None, absolute or relative


def expand_loop(start, end, loop_on, loop_start, loop_end, start_relative):
    """Split a clip into (timeline start, timeline stop, clip beat at start) runs. Live plays a
    looped clip from loop_start + start_relative to loop_end, then wraps to loop_start."""
    position = loop_start + start_relative
    if not loop_on or loop_end <= loop_start:
        return [(start, end, position)]
    if position >= loop_end:
        position = loop_start + (position - loop_start) % (loop_end - loop_start)
    runs = []
    cursor = start
    while cursor < end and len(runs) < MAX_SEGMENTS:
        length = min(end - cursor, loop_end - position)
        runs.append((cursor, cursor + length, position))
        cursor += length
        position = loop_start
    return runs


class WarpMap:
    """Clip beats to seconds in the file, through a clip's warp markers. Beyond the markers the
    nearest segment's slope carries on, as it does in Live."""

    def __init__(self, clip, seconds_per_beat):
        points = []
        for marker in clip.findall('WarpMarkers/WarpMarker'):
            points.append((float(marker.get('BeatTime')), float(marker.get('SecTime'))))
        points = sorted(set(points))
        if len(points) < 2:
            offset = points[0] if points else (0.0, 0.0)
            points = [offset, (offset[0] + 1, offset[1] + seconds_per_beat)]
        self.points = points

    def seconds(self, beat):
        points = self.points
        index = 0
        while index < len(points) - 2 and beat >= points[index + 1][0]:
            index += 1
        (b0, s0), (b1, s1) = points[index], points[index + 1]
        return s0 + (beat - b0) * (s1 - s0) / (b1 - b0)


def _audio_clip(clip, track_name, tempo, als_dir, warnings, missing):
    file_ref = clip.find('SampleRef/FileRef')
    path, unresolved = resolve_sample(file_ref, als_dir)
    name = _value(clip, 'Name') or track_name
    if path is None:
        missing.append({'track': track_name, 'clip': name, 'path': unresolved})
        return [], None

    rate = _float(clip, 'SampleRef/DefaultSampleRate', 44100.0) or 44100.0
    duration = _float(clip, 'SampleRef/DefaultDuration') / rate
    start = float(clip.get('Time', _value(clip, 'CurrentStart') or 0))
    end = _float(clip, 'CurrentEnd')
    warped = _true(clip, 'IsWarped')
    seconds_per_beat = 60.0 / tempo
    warp = WarpMap(clip, seconds_per_beat)

    stretched = False
    if warped:
        for (b0, s0), (b1, s1) in zip(warp.points, warp.points[1:]):
            if abs((s1 - s0) / (b1 - b0) / seconds_per_beat - 1) > STRETCH_TOLERANCE:
                stretched = True
    if stretched:
        warnings.append(f'{track_name}: "{name}" is warped away from the set tempo. It comes in at '
                        'its recorded speed, so print it in Live first for an exact match.')
    if _value(clip, 'PitchCoarse', '0') != '0' or _float(clip, 'PitchFine') != 0:
        warnings.append(f'{track_name}: "{name}" is transposed in Live. It comes in untransposed.')

    gain_db = _db(_float(clip, 'SampleVolume', 1.0))
    muted = _true(clip, 'Disabled')
    runs = expand_loop(start, end, _true(clip, 'Loop/LoopOn'), _float(clip, 'Loop/LoopStart'),
                       _float(clip, 'Loop/LoopEnd'), _float(clip, 'Loop/StartRelative'))
    segments = []
    for run_start, run_stop, clip_beat in runs:
        if warped:
            source_start = warp.seconds(clip_beat)
            source_stop = warp.seconds(clip_beat + run_stop - run_start)
        else:
            # An unwarped clip's loop positions are already seconds.
            source_start = clip_beat
            source_stop = clip_beat + (run_stop - run_start) * seconds_per_beat
        if stretched:
            run_stop = run_start + (source_stop - source_start) / seconds_per_beat
        if duration and source_stop > duration:
            over = source_stop - duration
            source_stop = duration
            run_stop -= over / seconds_per_beat
        source_start = max(source_start, 0.0)
        if run_stop <= run_start or source_stop <= source_start:
            continue
        segments.append({'name': name, 'startBeat': run_start, 'stopBeat': run_stop,
                         'sourcePath': path, 'sourceStartSec': source_start,
                         'sourceStopSec': source_stop, 'gainDb': gain_db, 'muted': muted,
                         'fadeInBeats': 0.0, 'fadeOutBeats': 0.0})
    if segments and _true(clip, 'Fade'):
        if warped:
            segments[0]['fadeInBeats'] = _float(clip, 'Fades/FadeInLength')
            segments[-1]['fadeOutBeats'] = _float(clip, 'Fades/FadeOutLength')
        else:
            segments[0]['fadeInBeats'] = _float(clip, 'Fades/FadeInLength') / seconds_per_beat
            segments[-1]['fadeOutBeats'] = _float(clip, 'Fades/FadeOutLength') / seconds_per_beat
    return segments, audio_channels(path)


def _midi_notes(clip):
    start = float(clip.get('Time', _value(clip, 'CurrentStart') or 0))
    end = _float(clip, 'CurrentEnd')
    loop_start = _float(clip, 'Loop/LoopStart')
    loop_end = _float(clip, 'Loop/LoopEnd')
    source = []
    for key_track in clip.findall('Notes/KeyTracks/KeyTrack'):
        pitch = int(_value(key_track, 'MidiKey', '0'))
        for note in key_track.findall('Notes/MidiNoteEvent'):
            if note.get('IsEnabled', 'true') == 'true':
                source.append((pitch, float(note.get('Time')), float(note.get('Duration')),
                               float(note.get('Velocity', 100))))
    notes = []
    for run_start, run_stop, clip_beat in expand_loop(start, end, _true(clip, 'Loop/LoopOn'),
                                                      loop_start, loop_end,
                                                      _float(clip, 'Loop/StartRelative')):
        run_end = clip_beat + run_stop - run_start
        for pitch, time, length, velocity in source:
            if clip_beat <= time < run_end:
                note_start = run_start + time - clip_beat
                notes.append({'pitch': pitch, 'startBeat': note_start,
                              'stopBeat': min(note_start + length, run_stop),
                              'velocity': max(1, min(127, round(velocity)))})
    return notes


def _sample_paths_in_devices(track, als_dir):
    paths = []
    for file_ref in track.findall('DeviceChain/DeviceChain/Devices//SampleRef/FileRef'):
        path, _ = resolve_sample(file_ref, als_dir)
        if path and path not in paths:
            paths.append(path)
    return paths


def _read_track(track, tempo, als_dir, warnings, missing):
    name = _value(track, 'Name/EffectiveName') or 'Untitled'
    mixer = track.find('DeviceChain/Mixer')
    is_midi = track.tag == 'MidiTrack'
    clips = []
    channels = set()
    for clip in track.findall('.//MainSequencer/Sample/ArrangerAutomation/Events/AudioClip'):
        segments, count = _audio_clip(clip, name, tempo, als_dir, warnings, missing)
        clips.extend(segments)
        if count:
            channels.add(count)
    notes = []
    for clip in track.findall('.//MainSequencer/ClipTimeable/ArrangerAutomation/Events/MidiClip'):
        notes.extend(_midi_notes(clip))
    clips.sort(key=lambda c: c['startBeat'])
    notes.sort(key=lambda n: n['startBeat'])

    if any(count > 2 for count in channels):
        warnings.append(f'{name}: has audio with more than two channels, which may not import.')
    sample = next(iter(_sample_paths_in_devices(track, als_dir)), None) if is_midi else None
    if len(track.findall('.//MainSequencer/ClipSlotList//ClipSlot/ClipSlot/Value/*')) > 0:
        warnings.append(f'{name}: Session View clips are not on the timeline and were skipped.')
    pan = _float(mixer, 'Pan/Manual')
    if pan != 0:
        warnings.append(f'{name}: pan is {pan:+.2f} in Live and was left centred.')
    if any(_float(send, 'Send/Manual') > SILENT_SEND for send in mixer.findall('Sends/TrackSendHolder')):
        warnings.append(f'{name}: send levels not carried over.')
    if _true(mixer, 'SoloSink'):
        warnings.append(f'{name}: soloed in Live; the solo was not carried over.')

    stereo = bool(channels) and max(channels) >= 2 or is_midi or not channels
    return {'name': name, 'kind': 'instrument' if is_midi else 'audio',
            'stemFormat': 'stereo' if stereo else 'mono',
            'gainDb': _db(_float(mixer, 'Volume/Manual', 1.0)),
            'muted': not _true(mixer, 'Speaker/Manual'), 'clips': clips, 'notes': notes, 'samplePath': sample}


def read_set(path):
    """Read a .als file and answer its import plan. Never writes anything."""
    if not path.lower().endswith('.als') or not os.path.isfile(path):
        raise AlsError('That is not an Ableton Live set (.als).')
    try:
        root = _load_xml(path)
    except (OSError, EOFError, ET.ParseError) as error:
        raise AlsError(f'Could not read this set: {error}')
    live_set = root.find('LiveSet')
    if root.tag != 'Ableton' or live_set is None:
        raise AlsError('This file is not a Live set Ableton 9 or newer wrote.')

    main = live_set.find('MainTrack')
    if main is None:
        main = live_set.find('MasterTrack')
    tempo = _float(main, 'DeviceChain/Mixer/Tempo/Manual', 120.0)
    meter = decode_time_signature(_value(main, 'DeviceChain/Mixer/TimeSignature/Manual', '201'))
    warnings = []
    missing = []

    tempo_target = main.find('DeviceChain/Mixer/Tempo/AutomationTarget')
    for envelope in main.findall('AutomationEnvelopes/Envelopes/AutomationEnvelope'):
        if tempo_target is not None and _value(envelope, 'EnvelopeTarget/PointeeId') == tempo_target.get('Id'):
            if len(envelope.findall('Automation/Events/FloatEvent')) > 1:
                warnings.append(f'The set has tempo changes. Only its {tempo:g} BPM base tempo came in.')

    tracks = []
    als_dir = os.path.dirname(os.path.abspath(path))
    for track in live_set.find('Tracks'):
        if track.tag == 'ReturnTrack':
            continue
        elif track.tag == 'GroupTrack':
            name = _value(track, 'Name/EffectiveName')
            warnings.append(f'Group "{name}" was flattened: its tracks came in without the group.')
        elif track.tag in ('AudioTrack', 'MidiTrack'):
            # One track Live wrote in a way this doesn't expect is left out, not the whole set.
            try:
                tracks.append(_read_track(track, tempo, als_dir, warnings, missing))
            except Exception as error:  # noqa: BLE001
                traceback.print_exc()
                name = _value(track, 'Name/EffectiveName') or 'A track'
                warnings.append(f'"{name}" could not be read, so it was left out ({_describe(error)}).')

    locators = []
    for locator in live_set.findall('Locators/Locators/Locator'):
        name = _value(locator, 'Name') or 'Marker'
        try:
            locators.append({'name': name, 'beat': float(_value(locator, 'Time', '0'))})
        except (TypeError, ValueError) as error:
            warnings.append(f'Marker "{name}" could not be read, so it was left out ({_describe(error)}).')
    sources = sorted({clip['sourcePath'] for track in tracks for clip in track['clips']})
    try:
        suggestions, notes, unmatched = build(live_set, tempo, tracks)
    except Exception as error:  # noqa: BLE001
        traceback.print_exc()
        suggestions, notes = [], []
        unmatched = [f'No suggestions: part of this set could not be read ({_describe(error)}).']
    return {'source': {'path': path, 'name': os.path.basename(path), 'creator': root.get('Creator')},
            'tempo': tempo, 'timeSignature': meter, 'locators': locators, 'tracks': tracks,
            'sources': sources, 'warnings': warnings + unmatched, 'missing': missing,
            'suggestions': suggestions, 'notes': notes}
