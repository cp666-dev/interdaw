"""Run from inter-daw-helper/: python3 -m unittest discover -s tests -t .

Set INTERDAW_TEST_ALS to a real set's path to check it reads too. Client files stay out of git.
"""

import gzip
import json
import os
import struct
import tempfile
import threading
import unittest
import unittest.mock
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

from interdaw_helper.als import AlsError, decode_time_signature, expand_loop, read_set
from interdaw_helper.server import Handler

BEAT = 60 / 136


def wav(path, channels=2, seconds=20):
    rate = 44100
    frames = int(rate * seconds)
    with open(path, 'wb') as f:
        f.write(b'RIFF' + struct.pack('<I', 36) + b'WAVEfmt ' + struct.pack('<IHHIIHH', 16, 1, channels, rate, rate * channels * 2, channels * 2, 16))
        f.write(b'data' + struct.pack('<I', 0))
    return frames


def clip_xml(start, end, loop_on, loop_start, loop_end, relative='a.wav', start_relative=0, frames=882000, gain=1, fade_out=0, warp_beats=BEAT):
    return f'''<AudioClip Time="{start}"><CurrentStart Value="{start}"/><CurrentEnd Value="{end}"/>
      <Loop><LoopStart Value="{loop_start}"/><LoopEnd Value="{loop_end}"/><StartRelative Value="{start_relative}"/><LoopOn Value="{str(loop_on).lower()}"/></Loop>
      <Name Value="Clip"/><Disabled Value="false"/><IsWarped Value="true"/>
      <SampleRef><FileRef><RelativePathType Value="1"/><RelativePath Value="{relative}"/><Path Value="/stale/{relative}"/></FileRef><DefaultDuration Value="{frames}"/><DefaultSampleRate Value="44100"/></SampleRef>
      <Fade Value="true"/><Fades><FadeInLength Value="0"/><FadeOutLength Value="{fade_out}"/></Fades><PitchCoarse Value="0"/><PitchFine Value="0"/><SampleVolume Value="{gain}"/>
      <WarpMarkers><WarpMarker SecTime="0" BeatTime="0"/><WarpMarker SecTime="{warp_beats}" BeatTime="1"/></WarpMarkers></AudioClip>'''


def track_xml(tag, name, clips='', midi='', volume=1, speaker='true', devices=''):
    sequencer = f'''<MainSequencer><ClipSlotList/><Sample><ArrangerAutomation><Events>{clips}</Events></ArrangerAutomation></Sample>
      <ClipTimeable><ArrangerAutomation><Events>{midi}</Events></ArrangerAutomation></ClipTimeable></MainSequencer>'''
    return f'''<{tag} Id="1"><Name><EffectiveName Value="{name}"/></Name><AutomationEnvelopes><Envelopes/></AutomationEnvelopes>
      <DeviceChain>{sequencer}<Mixer><Pan><Manual Value="0"/></Pan><Volume><Manual Value="{volume}"/></Volume><Speaker><Manual Value="{speaker}"/></Speaker><SoloSink Value="false"/>
      <Sends><TrackSendHolder><Send><Manual Value="0.0003162277571"/></Send></TrackSendHolder></Sends></Mixer>
      <DeviceChain><Devices>{devices}</Devices></DeviceChain></DeviceChain></{tag}>'''


def write_set(folder, tracks, extra=''):
    path = os.path.join(folder, 'Song.als')
    xml = f'''<?xml version="1.0"?><Ableton Creator="Ableton Live 12.3.6"><LiveSet><Tracks>{tracks}</Tracks>
      <MainTrack><AutomationEnvelopes><Envelopes/></AutomationEnvelopes><DeviceChain><Mixer><Tempo><Manual Value="136"/><AutomationTarget Id="8"/></Tempo><TimeSignature><Manual Value="201"/></TimeSignature></Mixer><DeviceChain><Devices/></DeviceChain></DeviceChain></MainTrack>
      <Locators><Locators><Locator Id="0"><Time Value="80"/><Name Value="BREAK"/></Locator></Locators></Locators>{extra}</LiveSet></Ableton>'''
    with gzip.open(path, 'wb') as f:
        f.write(xml.encode())
    return path


class ExpandLoop(unittest.TestCase):
    def test_unlooped_stays_whole_and_keeps_its_offset(self):
        self.assertEqual(expand_loop(264, 480, False, 40, 256, 0), [(264, 480, 40)])

    def test_repeats_to_the_clip_end_and_cuts_the_last_pass(self):
        self.assertEqual(expand_loop(0, 10, True, 0, 4, 0), [(0, 4, 0), (4, 8, 0), (8, 10, 0)])

    def test_start_offset_only_shortens_the_first_pass(self):
        self.assertEqual(expand_loop(80, 96, True, 0, 8, 2), [(80, 86, 2), (86, 94, 0), (94, 96, 0)])


class ReadSet(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        wav(os.path.join(self.dir.name, 'a.wav'))

    def test_meter_decoding(self):
        self.assertEqual(decode_time_signature(201), {'numerator': 4, 'denominator': 4})
        self.assertEqual(decode_time_signature(101), {'numerator': 3, 'denominator': 2})
        self.assertEqual(decode_time_signature(299), {'numerator': 3, 'denominator': 8})

    def test_tempo_meter_and_markers(self):
        plan = read_set(write_set(self.dir.name, ''))
        self.assertEqual(plan['tempo'], 136)
        self.assertEqual(plan['timeSignature'], {'numerator': 4, 'denominator': 4})
        self.assertEqual(plan['locators'], [{'name': 'BREAK', 'beat': 80}])

    def test_looped_clip_maps_every_pass_to_the_file(self):
        clips = clip_xml(0, 10, True, 0, 4, fade_out=0.25)
        plan = read_set(write_set(self.dir.name, track_xml('AudioTrack', 'Kicks', clips)))
        segments = plan['tracks'][0]['clips']
        self.assertEqual([(s['startBeat'], s['stopBeat']) for s in segments], [(0, 4), (4, 8), (8, 10)])
        self.assertAlmostEqual(segments[0]['sourceStopSec'], 4 * BEAT)
        self.assertAlmostEqual(segments[2]['sourceStopSec'], 2 * BEAT)
        self.assertEqual([s['fadeOutBeats'] for s in segments], [0, 0, 0.25])
        self.assertTrue(plan['sources'][0].endswith('a.wav'))
        self.assertEqual(plan['tracks'][0]['stemFormat'], 'stereo')

    def test_clip_is_cut_where_its_file_ends(self):
        clips = clip_xml(0, 100, False, 0, 100, frames=441000)
        segment = read_set(write_set(self.dir.name, track_xml('AudioTrack', 'A', clips)))['tracks'][0]['clips'][0]
        self.assertAlmostEqual(segment['sourceStopSec'], 10)
        self.assertAlmostEqual(segment['stopBeat'], 10 / BEAT)

    def test_mono_file_makes_a_mono_track(self):
        wav(os.path.join(self.dir.name, 'm.wav'), channels=1)
        clips = clip_xml(0, 4, False, 0, 4, relative='m.wav')
        self.assertEqual(read_set(write_set(self.dir.name, track_xml('AudioTrack', 'M', clips)))['tracks'][0]['stemFormat'], 'mono')

    def test_missing_audio_is_listed_not_fatal(self):
        clips = clip_xml(0, 4, False, 0, 4, relative='gone.wav')
        plan = read_set(write_set(self.dir.name, track_xml('AudioTrack', 'A', clips)))
        self.assertEqual(plan['tracks'][0]['clips'], [])
        self.assertEqual(plan['missing'][0]['path'], '/stale/gone.wav')

    def test_stretched_clip_is_warned_about(self):
        clips = clip_xml(0, 4, False, 0, 4, warp_beats=BEAT * 1.5)
        plan = read_set(write_set(self.dir.name, track_xml('AudioTrack', 'A', clips)))
        self.assertIn('warped away', plan['warnings'][0])
        self.assertAlmostEqual(plan['tracks'][0]['clips'][0]['stopBeat'], 6)

    def test_mix_settings(self):
        plan = read_set(write_set(self.dir.name, track_xml('AudioTrack', 'A', volume=0.5, speaker='false')))
        track = plan['tracks'][0]
        self.assertAlmostEqual(track['gainDb'], -6.0206, places=3)
        self.assertTrue(track['muted'])

    def test_midi_notes_repeat_with_the_loop(self):
        midi = '''<MidiClip Time="288"><CurrentEnd Value="304"/><Loop><LoopStart Value="0"/><LoopEnd Value="8"/><StartRelative Value="0"/><LoopOn Value="true"/></Loop>
          <Notes><KeyTracks><KeyTrack><MidiKey Value="37"/><Notes><MidiNoteEvent Time="1" Duration="1" Velocity="90" IsEnabled="true"/><MidiNoteEvent Time="2" Duration="1" Velocity="90" IsEnabled="false"/></Notes></KeyTrack></KeyTracks></Notes></MidiClip>'''
        track = read_set(write_set(self.dir.name, track_xml('MidiTrack', 'Keys', midi=midi)))['tracks'][0]
        self.assertEqual(track['kind'], 'instrument')
        self.assertEqual([n['startBeat'] for n in track['notes']], [289, 297])

    def test_rejects_other_files(self):
        with self.assertRaises(AlsError):
            read_set(os.path.join(self.dir.name, 'a.wav'))
        bad = os.path.join(self.dir.name, 'bad.als')
        with open(bad, 'wb') as f:
            f.write(b'not gzip')
        with self.assertRaises(AlsError):
            read_set(bad)

    @unittest.skipUnless(os.environ.get('INTERDAW_TEST_ALS'), 'INTERDAW_TEST_ALS not set')
    def test_real_set(self):
        plan = read_set(os.environ['INTERDAW_TEST_ALS'])
        self.assertTrue(plan['tracks'])
        for track in plan['tracks']:
            for clip in track['clips']:
                self.assertTrue(os.path.isfile(clip['sourcePath']))
                self.assertLess(clip['startBeat'], clip['stopBeat'])
                self.assertAlmostEqual(clip['stopBeat'] - clip['startBeat'], (clip['sourceStopSec'] - clip['sourceStartSec']) / (60 / plan['tempo']), places=3)


class Server(unittest.TestCase):
    def setUp(self):
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def request(self, path, origin, body=None):
        headers = {'Origin': origin} if origin else {}
        data = None if body is None else json.dumps(body).encode()
        request = urllib.request.Request(f'http://127.0.0.1:{self.server.server_address[1]}{path}', data, headers)
        try:
            with urllib.request.urlopen(request) as answer:
                return answer.status, json.load(answer), answer.headers
        except urllib.error.HTTPError as error:
            return error.code, json.load(error), error.headers

    def test_the_panel_is_allowed(self):
        status, body, headers = self.request('/health', 'ua-content://com.cp.interdaw')
        self.assertEqual((status, body['ok']), (200, True))
        self.assertEqual(headers['Access-Control-Allow-Origin'], 'ua-content://com.cp.interdaw')

    def test_other_origins_and_no_origin_are_refused(self):
        for origin in ('https://example.com', None):
            status, _, headers = self.request('/als/read', origin, {'path': '/etc/hosts'})
            self.assertEqual(status, 403)
            self.assertIsNone(headers['Access-Control-Allow-Origin'])

    def test_the_dev_server_is_allowed_only_in_dev_mode(self):
        self.assertEqual(self.request('/health', 'http://localhost:8790')[0], 403)
        with unittest.mock.patch.dict(os.environ, {'INTERDAW_DEV': '1'}):
            self.assertEqual(self.request('/health', 'http://localhost:8790')[0], 200)

    def test_health_says_which_api_it_speaks(self):
        _, body, _ = self.request('/health', 'ua-content://com.cp.interdaw')
        self.assertEqual(body['api'], 1)

    def test_build_only_writes_to_a_folder_it_prepared(self):
        status, body, _ = self.request('/return/build', 'ua-content://com.cp.interdaw', {'folder': '/tmp', 'setPath': '/x.als', 'prints': [], 'moves': [], 'removed': []})
        self.assertEqual(status, 422)
        self.assertIn('not prepared', body['error'])

    def test_a_bad_file_is_a_readable_error(self):
        status, body, _ = self.request('/als/read', 'ua-content://com.cp.interdaw', {'path': '/etc/hosts'})
        self.assertEqual(status, 422)
        self.assertIn('not an Ableton', body['error'])


if __name__ == '__main__':
    unittest.main()
