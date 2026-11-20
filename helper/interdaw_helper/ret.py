"""Build the folder that goes back to someone using Live: their set with your work added, all the
audio it plays, stems for any other DAW, and a note saying what changed.

New tracks and clips are cloned from the set itself, so they match whichever Live wrote it. Nothing
in the folder depends on LUNA, UAD plugins or InterDAW, and the original set is never touched.
"""

import copy
import gzip
import os
import re
import shutil
import struct
import xml.etree.ElementTree as ET

from .als import AlsError, resolve_sample

PRINT_PREFIX = '[LUNA] '
ORIGINAL_PREFIX = '[Original] '
SILENT_DB = -70.0
# Live's send at minimum.
MIN_SEND = 0.0003162277571


def safe_name(name):
    cleaned = re.sub(r'[\\/:*?"<>|\x00-\x1f]', '-', name).strip(' .')
    return cleaned or 'Untitled'


FORMATS = ('both', 'live', 'stems')


def prepare(set_path, parent, output='both'):
    """Make a new empty return folder, never reusing one, so nothing is overwritten."""
    if output not in FORMATS:
        raise AlsError(f'Unknown export format {output}.')
    if not os.path.isfile(set_path) or not set_path.lower().endswith('.als'):
        raise AlsError('That is not an Ableton Live set (.als).')
    if not os.path.isdir(parent):
        raise AlsError('That folder is not available.')
    base = safe_name(os.path.splitext(os.path.basename(set_path))[0])
    label = 'LUNA Stems' if output == 'stems' else 'LUNA Return'
    folder = os.path.join(parent, f'{base} - {label}')
    count = 1
    while os.path.exists(folder):
        count += 1
        folder = os.path.join(parent, f'{base} - {label} {count}')
    os.makedirs(os.path.join(folder, 'Stems'))
    if output != 'stems':
        os.makedirs(os.path.join(folder, 'Audio'))
    return folder


def wav_info(path):
    """(frames, sample rate) from a WAV header, including the extensible kind LUNA renders."""
    with open(path, 'rb') as f:
        head = f.read(12)
        if head[:4] != b'RIFF' or head[8:12] != b'WAVE':
            raise AlsError(f'{os.path.basename(path)} is not a WAV file.')
        rate = block = None
        while True:
            chunk = f.read(8)
            if len(chunk) < 8:
                raise AlsError(f'{os.path.basename(path)} has no audio in it.')
            size = struct.unpack('<I', chunk[4:])[0]
            if chunk[:4] == b'fmt ':
                body = f.read(16)
                rate = struct.unpack('<I', body[4:8])[0]
                block = struct.unpack('<H', body[12:14])[0]
                f.seek(size - 16 + (size & 1), 1)
            elif chunk[:4] == b'data':
                if not block:
                    raise AlsError(f'{os.path.basename(path)} is damaged.')
                return size // block, rate
            else:
                f.seek(size + (size & 1), 1)


def _set(element, path, value):
    found = element.find(path)
    if found is not None:
        found.set('Value', str(value))


def _get(element, path, default=None):
    found = element.find(path)
    return default if found is None else found.get('Value', default)


def _clear(element, path):
    found = element.find(path)
    if found is not None:
        for child in list(found):
            found.remove(child)


def copy_audio(root, als_dir, folder):
    """Copy every sample the set plays into Audio/ and point the set at the copies. Answers the
    samples that couldn't be found, which stay as they were."""
    audio = os.path.join(folder, 'Audio')
    copied = {}
    missing = []
    for sample_ref in root.iter('SampleRef'):
        file_ref = sample_ref.find('FileRef')
        if file_ref is None:
            continue
        source, unresolved = resolve_sample(file_ref, als_dir)
        if source is None:
            if unresolved not in missing:
                missing.append(unresolved)
            continue
        if source not in copied:
            name = safe_name(os.path.basename(source))
            stem, extension = os.path.splitext(name)
            count = 1
            while os.path.exists(os.path.join(audio, name)):
                count += 1
                name = f'{stem} {count}{extension}'
            shutil.copy2(source, os.path.join(audio, name))
            if os.path.isfile(f'{source}.asd'):
                shutil.copy2(f'{source}.asd', os.path.join(audio, f'{name}.asd'))
            copied[source] = name
        _set(file_ref, 'RelativePathType', 1)
        _set(file_ref, 'RelativePath', f'Audio/{copied[source]}')
        _set(file_ref, 'Path', os.path.join(audio, copied[source]))
    return len(copied), missing


class Ids:
    """Hands out ids no element in the set uses. Live needs these unique and below NextPointeeId."""

    def __init__(self, live_set):
        self.next = int(_get(live_set, 'NextPointeeId', '1'))
        self.live_set = live_set
        self.track = max(int(t.get('Id', '0')) for t in live_set.find('Tracks')) + 1

    def pointee(self):
        self.next += 1
        return self.next - 1

    def new_track(self):
        self.track += 1
        return self.track - 1

    def finish(self):
        _set(self.live_set, 'NextPointeeId', self.next)


def _new_track(template, ids, name):
    """An empty audio track cloned from one of the set's own."""
    track = copy.deepcopy(template)
    track.set('Id', str(ids.new_track()))
    for element in track.iter():
        if element.get('Id') is not None and (element.tag.endswith('Target') or element.tag == 'Pointee'):
            element.set('Id', str(ids.pointee()))
    _clear(track, 'AutomationEnvelopes/Envelopes')
    _clear(track, 'DeviceChain/DeviceChain/Devices')
    _clear(track, 'DeviceChain/MainSequencer/Sample/ArrangerAutomation/Events')
    _clear(track, 'TakeLanes/TakeLanes')
    for slot in track.findall('.//ClipSlotList/ClipSlot/ClipSlot/Value'):
        for child in list(slot):
            slot.remove(child)
    _set(track, 'Name/EffectiveName', name)
    _set(track, 'Name/UserName', name)
    _set(track, 'Name/MemorizedFirstClipName', '')
    _set(track, 'TrackGroupId', -1)
    _set(track, 'LinkedTrackGroupId', -1)
    _set(track, 'Freeze', 'false')
    mixer = track.find('DeviceChain/Mixer')
    _set(mixer, 'Volume/Manual', 1)
    _set(mixer, 'Pan/Manual', 0)
    _set(mixer, 'Speaker/Manual', 'true')
    _set(mixer, 'SoloSink', 'false')
    for send in mixer.findall('Sends/TrackSendHolder'):
        _set(send, 'Send/Manual', MIN_SEND)
    return track


def _new_clip(template, name, path, relative, tempo):
    """A clip playing a whole file from bar 1 at its recorded speed. Warp stays on at exactly the
    set's tempo, as Live's own stems do, so it follows if they change the tempo later."""
    frames, rate = wav_info(path)
    seconds_per_beat = 60.0 / tempo
    beats = frames / rate / seconds_per_beat
    clip = copy.deepcopy(template)
    clip.set('Id', '0')
    clip.set('Time', '0')
    for tag, value in (('CurrentStart', 0), ('CurrentEnd', beats), ('Name', name), ('Disabled', 'false'),
                       ('IsWarped', 'true'), ('WarpMode', 0), ('PitchCoarse', 0), ('PitchFine', 0),
                       ('SampleVolume', 1), ('MarkersGenerated', 'true'), ('IsSongTempoLeader', 'false'),
                       ('Fades/FadeInLength', 0), ('Fades/FadeOutLength', 0),
                       ('Loop/LoopStart', 0), ('Loop/LoopEnd', beats), ('Loop/StartRelative', 0),
                       ('Loop/LoopOn', 'false'), ('Loop/OutMarker', beats), ('Loop/HiddenLoopStart', 0),
                       ('Loop/HiddenLoopEnd', beats), ('Onsets/HasUserOnsets', 'false')):
        _set(clip, tag, value)
    _clear(clip, 'Envelopes/Envelopes')
    _clear(clip, 'Onsets/UserOnsets')
    _clear(clip, 'SavedWarpMarkersForStretched')
    _clear(clip, 'SampleRef/SourceContext')
    markers = clip.find('WarpMarkers')
    for child in list(markers):
        markers.remove(child)
    for index, (seconds, beat) in enumerate(((0, 0), (0.03125 * seconds_per_beat, 0.03125))):
        ET.SubElement(markers, 'WarpMarker', {'Id': str(index), 'SecTime': repr(float(seconds)), 'BeatTime': repr(float(beat))})
    file_ref = clip.find('SampleRef/FileRef')
    _set(file_ref, 'RelativePathType', 1)
    _set(file_ref, 'RelativePath', relative)
    _set(file_ref, 'Path', path)
    _set(file_ref, 'Type', 2)
    _set(file_ref, 'OriginalFileSize', os.path.getsize(path))
    _set(file_ref, 'OriginalCrc', 0)
    _set(clip, 'SampleRef/LastModDate', int(os.path.getmtime(path)))
    _set(clip, 'SampleRef/DefaultDuration', frames)
    _set(clip, 'SampleRef/DefaultSampleRate', rate)
    return clip


def _apply_move(track, move):
    mixer = track.find('DeviceChain/Mixer')
    if 'gainDb' in move:
        _set(mixer, 'Volume/Manual', 10 ** (move['gainDb'] / 20))
    if 'muted' in move:
        _set(mixer, 'Speaker/Manual', 'false' if move['muted'] else 'true')
    holders = mixer.findall('Sends/TrackSendHolder')
    for send in move.get('sends', []):
        if send['index'] < len(holders):
            level = MIN_SEND if send['gainDb'] <= SILENT_DB else 10 ** (send['gainDb'] / 20)
            _set(holders[send['index']], 'Send/Manual', level)


def _write_set(root, path):
    data = b'<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding='utf-8')
    with gzip.open(path, 'wb') as f:
        f.write(data)


def _read_me(set_name, tempo, meter, rates, prints, new, moves, removed, missing, warnings, stems, output='both'):
    lines = [f'{set_name}: back from LUNA', '']
    if output != 'stems':
        lines.append('In Ableton Live: open the .als in this folder. Your set is unchanged except as listed here.')
    if output != 'live':
        lines.append('In any DAW: import everything in Stems at bar 1. They all start there and run the same length.')
    lines.extend(['', f'Tempo: {tempo:g} BPM. Meter: {meter}.'])
    if rates:
        lines.append(f'{"Stems" if output == "stems" else "Stems and new tracks"} are {", ".join(f"{r / 1000:g} kHz" for r in sorted(rates))}, 32-bit float.')
    lines.append('Mix Reference.wav is how this sounded in LUNA, including anything on the LUNA master.')
    def section(title, items):
        if items:
            lines.extend(['', title, *[f'  {item}' for item in items]])
    section('Printed from LUNA (your original is kept beside each, muted):', prints)
    section('New tracks:', new)
    section('Fader, mute and send changes written into your tracks:', moves)
    section('Removed in LUNA, muted here:', removed)
    section('Audio that was missing when this was made:', missing)
    section('Please check:', warnings)
    if output != 'live':
        section('Stems:', stems)
    lines.append('')
    return '\n'.join(lines)


def build(folder, set_path, prints, moves, removed, warnings=(), output='both', session_tempo=None):
    """Assemble the folder. `prints` are stems already rendered into Stems/:
    { file, name, original: track name or null }. Stems-only output has no set or copied audio, and
    states the session's tempo, because that's what the stems were printed at. Answers what was made."""
    if output not in FORMATS:
        raise AlsError(f'Unknown export format {output}.')
    if not os.path.isfile(set_path):
        raise AlsError('The original set has moved.')
    with gzip.open(set_path, 'rb') as f:
        root = ET.fromstring(f.read())
    live_set = root.find('LiveSet')
    main = live_set.find('MainTrack')
    if main is None:
        main = live_set.find('MasterTrack')
    tempo = float(_get(main, 'DeviceChain/Mixer/Tempo/Manual', '120'))
    meter_value = int(_get(main, 'DeviceChain/Mixer/TimeSignature/Manual', '201'))
    index, numerator = divmod(meter_value, 99)
    meter = f'{numerator + 1}/{2 ** index}'

    # Templates first: copying audio rewrites the set's own references, and these must stay clean.
    tracks = live_set.find('Tracks')
    template_track = next((t for t in tracks if t.tag == 'AudioTrack'), None)
    template_clip = next(iter(root.iter('AudioClip')), None)
    set_rates = {int(float(_get(c, 'SampleRef/DefaultSampleRate', '0'))) for c in root.iter('AudioClip')} - {0}
    stems_dir = os.path.join(folder, 'Stems')
    als_name = f'{os.path.splitext(os.path.basename(set_path))[0]} LUNA.als'

    if output == 'stems':
        stems = sorted(f for f in os.listdir(stems_dir) if f.lower().endswith('.wav'))
        rates = {wav_info(os.path.join(stems_dir, stem))[1] for stem in stems}
        with open(os.path.join(folder, 'Read Me.txt'), 'w', encoding='utf-8') as f:
            f.write(_read_me(os.path.basename(set_path), session_tempo or tempo, meter, rates, [], [], [], [], [], list(warnings), stems, output))
        return {'folder': folder, 'set': None, 'copied': 0, 'missing': [], 'stems': len(stems), 'printed': 0, 'new': 0, 'moves': 0}

    copied, missing = copy_audio(root, os.path.dirname(os.path.abspath(set_path)), folder)
    rates = set()
    print_names, new_names, move_names = [], [], []
    wanted = list(warnings)
    for name in removed:
        moves = [*moves, {'track': name, 'muted': True}]

    by_name = {_get(t, 'Name/EffectiveName'): t for t in tracks}
    for move in moves:
        track = by_name.get(move['track'])
        if track is not None:
            _apply_move(track, move)
            if move['track'] not in removed:
                move_names.append(move['track'])

    made_set = False
    if prints and (template_track is None or template_clip is None):
        wanted.append('This set has no audio track to copy, so your new tracks are only in Stems.')
    elif template_track is not None:
        ids = Ids(live_set)
        for item in prints:
            path = os.path.join(stems_dir, item['file'])
            if not os.path.isfile(path):
                raise AlsError(f'LUNA did not write {item["file"]}.')
            _, rate = wav_info(path)
            rates.add(rate)
            track = _new_track(template_track, ids, f'{PRINT_PREFIX}{item["name"]}')
            track.find('DeviceChain/MainSequencer/Sample/ArrangerAutomation/Events').append(
                _new_clip(template_clip, item['name'], path, f'Stems/{item["file"]}', tempo))
            colour = _get(by_name.get(item.get('original')), 'Color') if item.get('original') in by_name else None
            if colour is not None:
                _set(track, 'Color', colour)
            original = by_name.get(item.get('original'))
            if original is not None:
                tracks.insert(list(tracks).index(original), track)
                _apply_move(original, {'muted': True})
                _set(original, 'Name/EffectiveName', f'{ORIGINAL_PREFIX}{item["original"]}')
                _set(original, 'Name/UserName', f'{ORIGINAL_PREFIX}{item["original"]}')
                print_names.append(item['name'])
            else:
                before_returns = next((i for i, t in enumerate(tracks) if t.tag == 'ReturnTrack'), len(tracks))
                tracks.insert(before_returns, track)
                new_names.append(item['name'])
        ids.finish()
        made_set = True
    if template_track is not None and not prints:
        made_set = True
    if made_set:
        _write_set(root, os.path.join(folder, als_name))

    stems = sorted(f for f in os.listdir(stems_dir) if f.lower().endswith('.wav'))
    for stem in stems:
        rates.add(wav_info(os.path.join(stems_dir, stem))[1])
    if set_rates - rates and rates:
        khz = lambda values: ' and '.join(f'{r / 1000:g} kHz' for r in sorted(values))
        wanted.append(f'The stems are {khz(rates)} but the set\'s own audio is {khz(set_rates)}. Live converts this as it plays; other DAWs may need the session set to match.')
    with open(os.path.join(folder, 'Read Me.txt'), 'w', encoding='utf-8') as f:
        f.write(_read_me(os.path.basename(set_path), tempo, meter, rates, print_names, new_names, move_names, list(removed), missing, wanted, stems, output))
    return {'folder': folder, 'set': als_name if made_set else None, 'copied': copied, 'missing': missing,
            'stems': len(stems), 'printed': len(print_names), 'new': len(new_names), 'moves': len(move_names)}
