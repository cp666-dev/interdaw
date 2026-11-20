"""Run from inter-daw-helper/: python3 -m unittest discover -s tests -t ."""

import math
import unittest
import xml.etree.ElementTree as ET

from interdaw_helper.suggest import (API, BELL, HIGHPASS, LOWPASS, SSL, Builder, _share_with_filter_copy, build, eq8_bands, fit_eq, fourcc, simpler_slices)


def band(index, on='true', mode=3, freq=1000, gain=0, q=0.7071):
    return f'<Bands.{index}><ParameterA><IsOn><Manual Value="{on}"/></IsOn><Mode><Manual Value="{mode}"/></Mode><Freq><Manual Value="{freq}"/></Freq><Gain><Manual Value="{gain}"/></Gain><Q><Manual Value="{q}"/></Q></ParameterA></Bands.{index}>'


def eq8(*bands, on='true', global_gain=0):
    return f'<Eq8><On><Manual Value="{on}"/></On><GlobalGain><Manual Value="{global_gain}"/></GlobalGain>{"".join(bands)}</Eq8>'


def run_device(xml, envelopes=()):
    builder = Builder(136.0)
    took_slot = builder.device(ET.fromstring(xml), 0, 'T', 'T', list(envelopes), True)
    return builder, took_slot


def param(insert, identifier):
    return next(p for p in insert['params'] if p['id'] == str(identifier))


def envelope(target, events):
    body = ''.join(f'<FloatEvent Time="{t}" Value="{v}"/>' for t, v in events)
    return ET.fromstring(f'<AutomationEnvelope><EnvelopeTarget><PointeeId Value="{target}"/></EnvelopeTarget><Automation><Events>{body}</Events></Automation></AutomationEnvelope>')


class Eq(unittest.TestCase):
    def test_48_db_cut_reads_with_its_slope(self):
        bands = eq8_bands(ET.fromstring(eq8(band(0, mode=0, freq=170))))
        self.assertEqual(bands, [(HIGHPASS, 170, 0, 0.7071, 48)])

    def test_flat_and_disabled_bands_do_nothing(self):
        self.assertEqual(eq8_bands(ET.fromstring(eq8(band(0, gain=0), band(1, on='false', gain=6)))), [])

    def test_flat_eq_is_a_note_not_a_suggestion(self):
        builder, slots = run_device(eq8(band(0, gain=0)))
        self.assertEqual(slots, 0)
        self.assertEqual(builder.suggestions, [])
        self.assertIn('changes nothing', builder.notes[0])

    def test_bands_land_on_ssl_controls_with_the_slope_difference_said(self):
        inserts, limits = fit_eq([(HIGHPASS, 170, 0, 0.7, 48), (BELL, 255, -1.4, 0.7, 0), (BELL, 711, 2.6, 0.33, 0)])
        ssl = inserts[0]
        self.assertEqual(ssl['pluginId'], SSL)
        self.assertEqual(param(ssl, 'hp_freq')['real'], 170)
        self.assertEqual((param(ssl, 'lf_freq')['real'], param(ssl, 'lf_gain')['real'], param(ssl, 'lf_bell')['display']), (255, -1.4, 'Bell'))
        self.assertEqual((param(ssl, 'lmf_freq')['real'], param(ssl, 'lmf_gain')['real'], param(ssl, 'lmf_q')['real']), (711, 2.6, 0.5))
        self.assertEqual(param(ssl, 'transformer')['display'], 'Off')
        self.assertTrue(any('18 dB/oct, not 48' in text for text in limits))
        self.assertTrue(any('Q stops short' in text for text in limits))

    def test_low_pass_below_the_ssl_uses_the_api_and_says_how_far_it_reaches(self):
        inserts, limits = fit_eq([(LOWPASS, 211, 0, 0.7, 12)])
        self.assertEqual([i['pluginId'] for i in inserts], [API])
        self.assertEqual(param(inserts[0], '215_lp_filter')['real'], 643)
        self.assertTrue(any('643 Hz, not 211' in text for text in limits))

    def test_too_many_bands_are_said_not_hidden(self):
        _, limits = fit_eq([(BELL, 300, 1, 1, 0), (BELL, 310, 1, 1, 0), (BELL, 320, 1, 1, 0)])
        self.assertTrue(any('no free band' in text for text in limits))

    def test_switched_off_device_is_only_a_note(self):
        builder, slots = run_device('<Overdrive><On><Manual Value="false"/></On></Overdrive>')
        self.assertEqual(slots, 0)
        self.assertEqual(builder.suggestions, [])
        self.assertIn('switched off', builder.notes[0])


class Filters(unittest.TestCase):
    AUTO = '<AutoFilter2><On><Manual Value="true"/></On><Filter_Type><Manual Value="0"/></Filter_Type><Filter_Slope><Manual Value="1"/></Filter_Slope><Filter_Frequency><Manual Value="431"/></Filter_Frequency><Lfo_Amount><Manual Value="0"/></Lfo_Amount><DryWet><AutomationTarget Id="77"/></DryWet></AutoFilter2>'

    def test_automated_dry_wet_becomes_a_crossfade_with_a_filtered_copy(self):
        builder, slots = run_device(self.AUTO, [envelope(77, [(-63072000, 1), (80, 1), (96, 0), (384, 0)])])
        self.assertEqual(slots, 0)
        suggestion = builder.suggestions[0]
        self.assertEqual(suggestion['match'], 'check')
        action = suggestion['action']
        self.assertEqual(action['kind'], 'crossfade')
        self.assertEqual(action['wetName'], 'T (filter)')
        self.assertEqual(action['insert']['pluginId'], API)
        self.assertEqual(param(action['insert'], '215_lp_filter')['real'], 643)
        self.assertEqual((action['wetLane'][0]['beat'], action['wetLane'][0]['real']), (0, 0))
        self.assertEqual(action['dryLane'][0]['real'], -144)
        at = lambda lane, beat: next(p for p in lane if p['beat'] == beat)['real']
        self.assertEqual((at(action['wetLane'], 96), at(action['dryLane'], 96)), (-144, 0))
        self.assertAlmostEqual(at(action['wetLane'], 88), 20 * math.log10(0.5))
        self.assertAlmostEqual(at(action['dryLane'], 88), 20 * math.log10(0.5))
        self.assertIn('around bar 21', suggestion['why'])
        self.assertIn('643 Hz, not 431', suggestion['why'])

    def test_a_filter_with_no_automation_is_a_plain_insert(self):
        builder, slots = run_device(self.AUTO)
        self.assertEqual(slots, 1)
        self.assertEqual(builder.suggestions[0]['action']['kind'], 'insert')

    def test_eq_three_kill_switches_with_its_on_lane(self):
        xml = '<FilterEQ3><On><Manual Value="false"/><AutomationTarget Id="9"/></On><GainLo><Manual Value="0.0003"/></GainLo><GainMid><Manual Value="1"/></GainMid><GainHi><Manual Value="1"/></GainHi><FreqLo><Manual Value="250"/></FreqLo><FreqHi><Manual Value="2500"/></FreqHi><Slope><Manual Value="1"/></Slope></FilterEQ3>'
        builder, _ = run_device(xml, [envelope(9, [(-63072000, 0), (80, 1), (96, 0)])])
        suggestion = builder.suggestions[0]
        insert = suggestion['action']['inserts'][0]
        self.assertEqual(insert['pluginId'], SSL)
        self.assertEqual(param(insert, 'hp_freq'), {'id': 'hp_freq', 'off': True})  # out until the lane switches it on
        points = insert['automation'][0]['points']
        self.assertEqual([p.get('real', 'off') if 'off' not in p else 'off' for p in points], ['off', 'off', 250, 250, 'off'])
        self.assertEqual((points[0]['beat'], points[2]['beat'], points[4]['beat']), (0, 80, 96))
        self.assertIn('bar 21', suggestion['why'])

    def test_a_limiter_with_no_stand_in_is_said_plainly(self):
        builder, slots = run_device('<Limiter><On><Manual Value="true"/></On><Gain><Manual Value="0"/></Gain><Ceiling><Manual Value="0"/></Ceiling></Limiter>')
        self.assertEqual(slots, 0)
        self.assertIn('only catches peaks above 0 dB', builder.unmatched[0])


class Plugins(unittest.TestCase):
    def test_audio_unit_id_comes_from_its_codes(self):
        self.assertEqual(fourcc(1802721110), 'ksWV')
        xml = '<AuPluginDevice><On><Manual Value="true"/></On><PluginDesc><AuPluginInfo><Name Value="OneKnob"/><ComponentManufacturer Value="1802721110"/><ComponentType Value="1635083896"/><ComponentSubType Value="1330332243"/></AuPluginInfo></PluginDesc><ParameterList><PluginFloatParameter><ParameterId Value="0"/><ParameterValue><Manual Value="0.28"/></ParameterValue></PluginFloatParameter><PluginEnumParameter><ParameterId Value="-1"/><ParameterValue><Manual Value="0"/></ParameterValue></PluginEnumParameter></ParameterList></AuPluginDevice>'
        insert = run_device(xml)[0].suggestions[0]['action']['inserts'][0]
        self.assertEqual(insert['pluginId'], 'com.ksWV.au.aufx_OKBS')
        self.assertEqual(insert['params'], [{'id': '0', 'real': 0.28}])
        self.assertEqual(insert['slot'], 'effect_0')

    def test_unknown_device_says_so(self):
        builder, slots = run_device('<Saturator><On><Manual Value="true"/></On></Saturator>')
        self.assertEqual(slots, 0)
        self.assertIn('Saturator', builder.unmatched[0])


class Slices(unittest.TestCase):
    def test_notes_become_the_slices_they_trigger(self):
        simpler = ET.fromstring('<OriginalSimpler><Globals><PlaybackMode Value="2"/></Globals><SimplerSlicing><Style Value="0"/></SimplerSlicing><MultiSamplePart><SampleEnd Value="88200"/><SampleRef><DefaultSampleRate Value="44100"/></SampleRef><SlicePoints><SlicePoint TimeInSeconds="0"/><SlicePoint TimeInSeconds="0.5"/><SlicePoint TimeInSeconds="1"/></SlicePoints></MultiSamplePart></OriginalSimpler>')
        clips = simpler_slices(simpler, [{'pitch': 37, 'startBeat': 8}, {'pitch': 38, 'startBeat': 12}, {'pitch': 90, 'startBeat': 16}], 0.5)
        self.assertEqual(clips, [{'startBeat': 8, 'stopBeat': 9, 'sourceStartSec': 0.5, 'sourceStopSec': 1},
                                 {'startBeat': 12, 'stopBeat': 14, 'sourceStartSec': 1, 'sourceStopSec': 2}])


class WholeSet(unittest.TestCase):
    def test_plugins_after_a_crossfaded_filter_go_on_the_copy_too(self):
        builder = Builder(136.0)
        dry_wet = envelope(77, [(-63072000, 1), (80, 1), (96, 0)])
        builder.device(ET.fromstring(Filters.AUTO), 0, 'T', 'T', [dry_wet], True)
        builder.device(ET.fromstring(eq8(band(1, freq=700, gain=2))), 0, 'T', 'T', [], True)
        _share_with_filter_copy(builder.suggestions, 'T')
        crossfade, eq = builder.suggestions
        self.assertEqual(eq['action']['alsoOn'], ['T (filter)'])
        self.assertEqual(eq['action']['inserts'][0]['slot'], 'effect_0')
        self.assertEqual(crossfade['action']['also'][0]['slot'], 'effect_1')
        self.assertEqual(crossfade['action']['also'][0]['pluginId'], SSL)

    def test_returns_and_colours(self):
        live_set = ET.fromstring('''<LiveSet><Tracks>
          <AudioTrack><Name><EffectiveName Value="A"/></Name><Color Value="0"/><DeviceChain><Mixer><Sends><TrackSendHolder><Send><Manual Value="0.0003162277571"/></Send></TrackSendHolder></Sends></Mixer><DeviceChain><Devices/></DeviceChain></DeviceChain></AudioTrack>
          <ReturnTrack><Name><EffectiveName Value="A-Reverb"/></Name><DeviceChain><DeviceChain><Devices><Reverb><On><Manual Value="true"/></On><PreDelay><Manual Value="2.5"/></PreDelay><DecayTime><Manual Value="2500"/></DecayTime></Reverb></Devices></DeviceChain></DeviceChain></ReturnTrack>
          </Tracks><MainTrack><AutomationEnvelopes><Envelopes/></AutomationEnvelopes><DeviceChain><DeviceChain><Devices/></DeviceChain></DeviceChain></MainTrack></LiveSet>''')
        suggestions, _, _ = build(live_set, 136.0, [{'name': 'A', 'muted': False, 'notes': []}])
        ret = next(s for s in suggestions if s['id'] == 'return:0')
        self.assertFalse(ret['audible'])
        self.assertEqual(ret['action']['send'], 'send_0')
        self.assertAlmostEqual(ret['action']['levels'][0]['gainDb'], -70, places=1)
        self.assertEqual(ret['action']['inserts'][0]['pluginId'], 'uaudio_pure_plate')
        self.assertEqual(next(p for p in ret['action']['inserts'][0]['params'] if p['id'] == 'decay')['displayNumber'], 2.5)
        colours = next(s for s in suggestions if s['id'] == 'colours')
        self.assertEqual(colours['action']['colours'], {'A': 0xFFFF94A6})


if __name__ == '__main__':
    unittest.main()
