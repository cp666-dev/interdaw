"""Run from inter-daw-helper/: python3 -m unittest discover -s tests -t ."""

import gzip
import os
import struct
import tempfile
import unittest
import xml.etree.ElementTree as ET

from interdaw_helper.als import AlsError, read_set
from interdaw_helper.ret import build, prepare, safe_name, wav_info
from tests.test_als import clip_xml, track_xml, wav, write_set


def rendered_wav(path, seconds=8, rate=48000):
    """A float WAV in the extensible form LUNA renders."""
    frames = seconds * rate
    with open(path, 'wb') as f:
        fmt = struct.pack('<HHIIHHHIH', 65534, 2, rate, rate * 8, 8, 32, 22, 3, 3) + b'\0' * 14
        f.write(b'RIFF' + struct.pack('<I', 0) + b'WAVE' + b'fmt ' + struct.pack('<I', len(fmt)) + fmt)
        f.write(b'data' + struct.pack('<I', frames * 8))
        f.truncate(f.tell() + frames * 8)
    return frames


class Fixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.src = os.path.join(self.tmp.name, 'src')
        os.makedirs(self.src)
        wav(os.path.join(self.src, 'a.wav'))
        tracks = track_xml('AudioTrack', 'Bass', clip_xml(0, 8, False, 0, 8)) + track_xml('AudioTrack', 'Keys', clip_xml(8, 16, False, 0, 8)).replace('Id="1"', 'Id="2"', 1)
        self.set_path = write_set(self.src, tracks + '<ReturnTrack Id="9"><Name><EffectiveName Value="A-Reverb"/></Name></ReturnTrack>', extra='<NextPointeeId Value="500"/>')
        with open(self.set_path, 'rb') as f:
            self.before = f.read()
        self.folder = prepare(self.set_path, self.tmp.name)

    def stem(self, file):
        return rendered_wav(os.path.join(self.folder, 'Stems', file))

    def built(self, **kw):
        args = {'prints': [], 'moves': [], 'removed': []}
        args.update(kw)
        result = build(self.folder, self.set_path, args['prints'], args['moves'], args['removed'], args.get('warnings', ()))
        with gzip.open(os.path.join(self.folder, result['set'])) as f:
            return result, ET.fromstring(f.read())


def names(root):
    return [t.find('Name/EffectiveName').get('Value') for t in root.find('LiveSet/Tracks')]


class Prepare(Fixture):
    def test_never_reuses_a_folder(self):
        self.assertTrue(self.folder.endswith('Song - LUNA Return'))
        self.assertTrue(prepare(self.set_path, self.tmp.name).endswith('Song - LUNA Return 2'))

    def test_refuses_what_is_not_a_set(self):
        with self.assertRaises(AlsError):
            prepare(os.path.join(self.src, 'a.wav'), self.tmp.name)

    def test_safe_names(self):
        self.assertEqual(safe_name('Voc Enjoy/Yourself: Rec'), 'Voc Enjoy-Yourself- Rec')
        self.assertEqual(safe_name('..'), 'Untitled')


class Formats(Fixture):
    def test_stems_only_has_no_set_or_copied_audio(self):
        folder = prepare(self.set_path, self.tmp.name, 'stems')
        self.assertTrue(folder.endswith('Song - LUNA Stems'))
        rendered_wav(os.path.join(folder, 'Stems', '01 Bass.wav'))
        result = build(folder, self.set_path, [], [], [], (), 'stems', 98.0)
        self.assertIsNone(result['set'])
        self.assertEqual(result['stems'], 1)
        self.assertEqual(sorted(os.listdir(folder)), ['Read Me.txt', 'Stems'])
        with open(os.path.join(folder, 'Read Me.txt')) as f:
            note = f.read()
        self.assertIn('Tempo: 98 BPM', note)
        self.assertIn('01 Bass.wav', note)
        self.assertNotIn('.als in this folder', note)

    def test_live_set_only_says_nothing_about_importing_stems(self):
        self.stem('01 Bass.wav')
        result = build(self.folder, self.set_path, [{'file': '01 Bass.wav', 'name': 'Bass', 'original': 'Bass'}], [], [], (), 'live')
        self.assertEqual(result['set'], 'Song LUNA.als')
        with open(os.path.join(self.folder, 'Read Me.txt')) as f:
            note = f.read()
        self.assertIn('.als in this folder', note)
        self.assertNotIn('import everything in Stems', note)

    def test_unknown_format_is_refused(self):
        with self.assertRaises(AlsError):
            prepare(self.set_path, self.tmp.name, 'aaf')


class Wav(Fixture):
    def test_reads_luna_float_renders(self):
        path = os.path.join(self.tmp.name, 'x.wav')
        frames = rendered_wav(path)
        self.assertEqual(wav_info(path), (frames, 48000))


class Build(Fixture):
    def test_original_set_is_untouched(self):
        self.built()
        with open(self.set_path, 'rb') as f:
            self.assertEqual(f.read(), self.before)

    def test_nothing_changed_keeps_the_same_tracks_with_audio_copied(self):
        result, root = self.built()
        self.assertEqual(names(root), ['Bass', 'Keys', 'A-Reverb'])
        self.assertEqual(result['copied'], 1)
        self.assertTrue(os.path.isfile(os.path.join(self.folder, 'Audio', 'a.wav')))
        for ref in root.iter('FileRef'):
            self.assertEqual(ref.find('RelativePath').get('Value'), 'Audio/a.wav')

    def test_the_folder_opens_after_moving_to_another_computer(self):
        result, _ = self.built()
        moved = os.path.join(self.tmp.name, 'moved')
        os.rename(self.folder, moved)
        os.rename(self.src, self.src + '-gone')
        plan = read_set(os.path.join(moved, result['set']))
        self.assertEqual(plan['missing'], [])
        self.assertEqual(os.path.dirname(plan['sources'][0]), os.path.join(moved, 'Audio'))

    def test_a_print_sits_above_its_muted_original(self):
        self.stem('01 Bass.wav')
        _, root = self.built(prints=[{'file': '01 Bass.wav', 'name': 'Bass', 'original': 'Bass'}])
        self.assertEqual(names(root), ['[LUNA] Bass', '[Original] Bass', 'Keys', 'A-Reverb'])
        tracks = list(root.find('LiveSet/Tracks'))
        self.assertEqual(tracks[1].find('DeviceChain/Mixer/Speaker/Manual').get('Value'), 'false')
        self.assertEqual(tracks[0].find('DeviceChain/Mixer/Speaker/Manual').get('Value'), 'true')
        self.assertEqual(tracks[2].find('DeviceChain/Mixer/Speaker/Manual').get('Value'), 'true')

    def test_a_print_plays_from_bar_one_at_recorded_speed(self):
        self.stem('01 Bass.wav')
        result, _ = self.built(prints=[{'file': '01 Bass.wav', 'name': 'Bass', 'original': 'Bass'}])
        plan = read_set(os.path.join(self.folder, result['set']))
        print_track = plan['tracks'][0]
        self.assertEqual(print_track['name'], '[LUNA] Bass')
        clip = print_track['clips'][0]
        self.assertEqual(clip['startBeat'], 0)
        self.assertAlmostEqual(clip['sourceStopSec'], 8)
        self.assertAlmostEqual(clip['stopBeat'], 8 / (60 / 136))
        self.assertEqual(plan['warnings'], [])
        self.assertEqual(clip['gainDb'], 0)

    def test_new_tracks_go_before_the_returns(self):
        self.stem('03 Vox.wav')
        _, root = self.built(prints=[{'file': '03 Vox.wav', 'name': 'Vox', 'original': None}])
        self.assertEqual(names(root), ['Bass', 'Keys', '[LUNA] Vox', 'A-Reverb'])

    def test_ids_stay_unique_and_under_the_counter(self):
        self.stem('01 Bass.wav')
        self.stem('02 Vox.wav')
        _, root = self.built(prints=[{'file': '01 Bass.wav', 'name': 'Bass', 'original': 'Bass'}, {'file': '02 Vox.wav', 'name': 'Vox', 'original': None}])
        track_ids = [t.get('Id') for t in root.find('LiveSet/Tracks')]
        self.assertEqual(len(track_ids), len(set(track_ids)))
        targets = [int(e.get('Id')) for e in root.iter() if e.get('Id') and (e.tag.endswith('Target') or e.tag == 'Pointee')]
        self.assertEqual(len(targets), len(set(targets)))
        self.assertLess(max(targets or [0]), int(root.find('LiveSet/NextPointeeId').get('Value')))

    def test_mix_moves_are_written_into_the_original_track(self):
        result, root = self.built(moves=[{'track': 'Keys', 'gainDb': -6, 'muted': True}])
        keys = list(root.find('LiveSet/Tracks'))[1]
        self.assertAlmostEqual(float(keys.find('DeviceChain/Mixer/Volume/Manual').get('Value')), 0.501, places=3)
        self.assertEqual(keys.find('DeviceChain/Mixer/Speaker/Manual').get('Value'), 'false')
        self.assertEqual(result['moves'], 1)

    def test_a_removed_track_is_muted_and_said_so(self):
        _, root = self.built(removed=['Keys'])
        self.assertEqual(list(root.find('LiveSet/Tracks'))[1].find('DeviceChain/Mixer/Speaker/Manual').get('Value'), 'false')
        with open(os.path.join(self.folder, 'Read Me.txt')) as f:
            note = f.read()
        self.assertIn('Removed in LUNA, muted here:\n  Keys', note)

    def test_read_me_says_what_happened(self):
        self.stem('01 Bass.wav')
        self.built(prints=[{'file': '01 Bass.wav', 'name': 'Bass', 'original': 'Bass'}], warnings=['Tempo changed in LUNA.'])
        with open(os.path.join(self.folder, 'Read Me.txt')) as f:
            note = f.read()
        for text in ('136 BPM', '48 kHz', '  Bass', 'Tempo changed in LUNA.', '01 Bass.wav'):
            self.assertIn(text, note)

    def test_a_sample_rate_mismatch_is_flagged(self):
        self.stem('01 Bass.wav')
        self.built(prints=[{'file': '01 Bass.wav', 'name': 'Bass', 'original': 'Bass'}])
        with open(os.path.join(self.folder, 'Read Me.txt')) as f:
            self.assertIn("The stems are 48 kHz but the set's own audio is 44.1 kHz", f.read())

    def test_a_render_that_never_arrived_is_an_error(self):
        with self.assertRaises(AlsError):
            self.built(prints=[{'file': 'nope.wav', 'name': 'Nope', 'original': None}])


if __name__ == '__main__':
    unittest.main()
