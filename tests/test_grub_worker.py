"""Backend failure isolation and stale-job checks, without real D-Bus writes."""
import os
os.environ['GSETTINGS_BACKEND'] = 'memory'
import json
import unittest
from unittest.mock import Mock, patch
from wallpaper_sync.worker import Worker, synchronize
from gi.repository import GLib


class WorkerBackends(unittest.TestCase):
    def setUp(self):
        self.source = {'uri': 'file:///image.webp', 'blur': 12, 'convert': True, 'grub_enabled': True}
        self.metadata = {'sha256': 'prepared'}
        self.grub_profile = {'enabled': True, 'available': True, 'output_width': 1920, 'output_height': 1080}
        self.calls = []

    def helper(self, method, *args):
        self.calls.append(method)
        return {'grub': dict(self.grub_profile)} if method == 'GetStatus' else {'installed_sha256': 'prepared', 'changed': True}

    def test_gdm_failure_does_not_block_grub(self):
        with patch('wallpaper_sync.worker.prepare', side_effect=ValueError('GDM failed')), \
             patch('wallpaper_sync.worker.prepare_grub', return_value=(b'jpeg', self.metadata)), \
             patch('wallpaper_sync.worker.helper_call', side_effect=self.helper):
            result = synchronize(self.source, lambda: None)
        self.assertIn('gdm', result['errors'])
        self.assertIn('grub', result)
        self.assertIn('ApplyGrub', self.calls)

    def test_grub_failure_does_not_undo_gdm(self):
        with patch('wallpaper_sync.worker.prepare', return_value=(b'png', self.metadata)), \
             patch('wallpaper_sync.worker.prepare_grub', side_effect=ValueError('bad JPEG')), \
             patch('wallpaper_sync.worker.helper_call', side_effect=self.helper):
            result = synchronize(self.source, lambda: None)
        self.assertIn('grub', result['errors'])
        self.assertIn('gdm', result)
        self.assertEqual(self.calls, ['GetStatus', 'Apply', 'GetStatus'])

    def test_stale_generation_never_reaches_helper(self):
        def stale():
            raise RuntimeError('Superseded')
        with patch('wallpaper_sync.worker.helper_call', side_effect=self.helper):
            result = synchronize(self.source, stale)
        self.assertFalse(result['success'])
        self.assertEqual(self.calls, [])

    def test_settings_change_during_conversion_prevents_install(self):
        checks = 0
        def check():
            nonlocal checks
            checks += 1
            if checks > 1:
                raise RuntimeError('Superseded')
        with patch('wallpaper_sync.worker.prepare', return_value=(b'png', self.metadata)), \
             patch('wallpaper_sync.worker.helper_call', side_effect=self.helper):
            result = synchronize(self.source, check)
        self.assertFalse(result['success'])
        self.assertEqual(self.calls, [])

    def test_disabled_grub_never_converts_or_calls_install(self):
        self.source['grub_enabled'] = False
        with patch('wallpaper_sync.worker.prepare', return_value=(b'png', self.metadata)), \
             patch('wallpaper_sync.worker.prepare_grub') as grub, \
             patch('wallpaper_sync.worker.helper_call', side_effect=self.helper):
            result = synchronize(self.source, lambda: None)
        grub.assert_not_called()
        self.assertTrue(result['success'])
        self.assertTrue(result['grub']['disabled'])

    def test_runtime_unconfigured_grub_keeps_completed_gdm_and_never_converts(self):
        self.grub_profile = {'enabled': False, 'available': False, 'configuration_required': True}
        with patch('wallpaper_sync.worker.prepare', return_value=(b'png', self.metadata)), \
             patch('wallpaper_sync.worker.prepare_grub') as grub, \
             patch('wallpaper_sync.worker.helper_call', side_effect=self.helper):
            result = synchronize(self.source, lambda: None)
        grub.assert_not_called()
        self.assertTrue(result['gdm']['changed'])
        self.assertFalse(result['success'])
        self.assertEqual(set(result['errors']), {'grub'})
        self.assertIn('not configured', result['errors']['grub'])
        self.assertIn('GDM wallpaper synchronized.', result['message'])
        self.assertEqual(self.calls, ['GetStatus', 'Apply', 'GetStatus'])

    def test_configured_output_size_is_passed_to_conversion(self):
        self.grub_profile.update(output_width=2560, output_height=1440, width=80, height=50)
        with patch('wallpaper_sync.worker.prepare', return_value=(b'png', self.metadata)), \
             patch('wallpaper_sync.worker.prepare_grub', return_value=(b'jpeg', self.metadata)) as grub, \
             patch('wallpaper_sync.worker.helper_call', side_effect=self.helper):
            result = synchronize(self.source, lambda: None)
        grub.assert_called_once_with(self.source['uri'], size=(2560, 1440))
        self.assertTrue(result['success'])
        self.assertEqual(self.calls, ['GetStatus', 'Apply', 'GetStatus', 'ApplyGrub'])

    def test_grub_status_error_is_preserved_without_conversion(self):
        error = 'Installed theme must reference the existing background.jpg.'
        self.grub_profile = {'enabled': True, 'error': error}
        with patch('wallpaper_sync.worker.prepare', return_value=(b'png', self.metadata)), \
             patch('wallpaper_sync.worker.prepare_grub') as grub, \
             patch('wallpaper_sync.worker.helper_call', side_effect=self.helper):
            result = synchronize(self.source, lambda: None)
        grub.assert_not_called()
        self.assertTrue(result['gdm']['changed'])
        self.assertEqual(result['errors'], {'grub': error})
        self.assertIn(error, result['message'])
        self.assertEqual(self.calls, ['GetStatus', 'Apply', 'GetStatus'])

    def test_grub_status_transport_error_is_preserved_without_conversion(self):
        error = 'Helper service disconnected during GRUB status.'
        def helper(method, *args):
            if method == 'GetStatus' and 'Apply' in self.calls:
                self.calls.append(method)
                raise RuntimeError(error)
            return self.helper(method, *args)
        with patch('wallpaper_sync.worker.prepare', return_value=(b'png', self.metadata)), \
             patch('wallpaper_sync.worker.prepare_grub') as grub, \
             patch('wallpaper_sync.worker.helper_call', side_effect=helper):
            result = synchronize(self.source, lambda: None)
        grub.assert_not_called()
        self.assertTrue(result['gdm']['changed'])
        self.assertEqual(result['errors'], {'grub': error})
        self.assertIn(error, result['message'])

    def test_identical_backends_are_skipped(self):
        current = {'matches_last_operation': True, 'image_sha256': 'prepared',
                   'source_uri': self.source['uri'], 'blur': 12}
        current['grub'] = {**current, **self.grub_profile}
        with patch('wallpaper_sync.worker.prepare', return_value=(b'png', self.metadata)), \
             patch('wallpaper_sync.worker.prepare_grub', return_value=(b'jpeg', self.metadata)), \
             patch('wallpaper_sync.worker.helper_call', return_value=current) as helper:
            result = synchronize(self.source, lambda: None)
        self.assertTrue(result['gdm']['skipped'])
        self.assertTrue(result['grub']['skipped'])
        self.assertEqual([call.args[0] for call in helper.call_args_list], ['GetStatus', 'GetStatus'])

class WorkerScheduling(unittest.TestCase):
    def setUp(self):
        self.connection = Mock()
        self.connection.register_object.return_value = 1
        with patch('wallpaper_sync.worker.load_json', return_value={}):
            self.worker = Worker(self.connection)
        self.original = {key: self.worker.config.get_value(key)
                         for key in ['synchronize', 'follow-current', 'grub-enabled']}
        for key in self.original:
            self.worker.config.set_boolean(key, True)
        timeout_patch = patch('wallpaper_sync.worker.GLib.timeout_add', return_value=71)
        remove_patch = patch('wallpaper_sync.worker.GLib.source_remove')
        self.timeout, self.remove = timeout_patch.start(), remove_patch.start()
        self.addCleanup(timeout_patch.stop)
        self.addCleanup(remove_patch.stop)

    def tearDown(self):
        for key, value in self.original.items():
            self.worker.config.set_value(key, value)

    def request(self, method, force=False):
        invocation = Mock()
        arguments = GLib.Variant('(b)', (force,)) if method == 'Sync' else None
        self.worker.call(None, None, None, None, method, arguments, invocation)
        invocation.return_dbus_error.assert_not_called()
        return invocation

    def finish(self):
        self.worker.active = {'id': 'active-sync', 'method': 'Sync'}
        with patch('wallpaper_sync.worker.atomic_json'), patch('wallpaper_sync.worker.log'), \
             patch('wallpaper_sync.worker.time.monotonic', return_value=100.0):
            self.worker.finish({'job_id': 'active-sync', 'success': True, 'message': 'Done'})

    def test_automatic_request_does_not_replace_queued_explicit_grub_restore(self):
        # The grub-enabled change generates an automatic extension request while
        # an earlier conversion can still be active and holding up the restore.
        self.worker.active = {'id': 'long-conversion', 'method': 'Sync'}
        self.request('RestoreGrub')
        restore_id = self.worker.pending['id']
        timer = self.worker.timer
        automatic = self.request('Sync')
        self.assertEqual(self.worker.pending['id'], restore_id)
        self.assertEqual(self.worker.pending['method'], 'RestoreGrub')
        self.assertNotIn(restore_id, self.worker.completed)
        self.assertEqual(self.worker.timer, timer)
        self.assertFalse(self.worker.config.get_boolean('grub-enabled'))
        self.assertIn('rollback', automatic.return_value.call_args.args[0].unpack()[0])

    def test_finish_reschedules_pending_debounce_after_backend_cooldown(self):
        self.worker.pending = {'id': 'new-wallpaper', 'method': 'Sync'}
        self.worker.timer = 19
        self.finish()
        self.remove.assert_called_once_with(19)
        self.timeout.assert_called_once_with(2200, self.worker.start)
        self.assertAlmostEqual(self.worker.next_start, 102.2)
        self.assertIsNone(self.worker.active)
        self.assertEqual(self.worker.pending['id'], 'new-wallpaper')

    def test_later_event_cannot_shorten_backend_cooldown(self):
        self.finish()
        with patch('wallpaper_sync.worker.time.monotonic', return_value=100.1):
            self.request('Sync', force=True)
        self.timeout.assert_called_with(750, self.worker.start)
        pending = dict(self.worker.pending)
        with patch('wallpaper_sync.worker.time.monotonic', return_value=100.85), \
             patch('wallpaper_sync.worker.threading.Thread') as thread:
            self.worker.start()
        thread.assert_not_called()
        self.assertIsNone(self.worker.active)
        self.assertEqual(self.worker.pending, pending)
        wait, callback = self.timeout.call_args.args
        self.assertGreaterEqual(wait, 1350)
        self.assertLessEqual(wait, 1351)
        self.assertEqual(callback, self.worker.start)

    def test_duplicate_automatic_request_joins_pending_manual_sync(self):
        source = {'uri': 'file:///same-wallpaper.png', 'grub_enabled': True}
        with patch('wallpaper_sync.worker.desktop_source', return_value=source):
            self.request('Sync', force=True)
            job_id, generation = self.worker.pending['id'], self.worker.generation
            self.timeout.reset_mock()
            automatic = self.request('Sync')
        reply = json.loads(automatic.return_value.call_args.args[0].unpack()[0])
        self.assertEqual(reply, {'job_id': job_id})
        self.assertEqual(self.worker.pending['id'], job_id)
        self.assertEqual(self.worker.generation, generation)
        self.assertNotIn(job_id, self.worker.completed)
        self.timeout.assert_not_called()

    def test_duplicate_automatic_request_joins_active_manual_sync(self):
        source = {'uri': 'file:///same-wallpaper.png', 'grub_enabled': True}
        with patch('wallpaper_sync.worker.desktop_source', return_value=source), \
             patch('wallpaper_sync.worker.threading.Thread'), patch('wallpaper_sync.worker.log'):
            self.request('Sync', force=True)
            self.worker.start()
            job_id, generation = self.worker.active['id'], self.worker.generation
            self.timeout.reset_mock()
            automatic = self.request('Sync')
        reply = json.loads(automatic.return_value.call_args.args[0].unpack()[0])
        self.assertEqual(reply, {'job_id': job_id})
        self.assertEqual(self.worker.active['id'], job_id)
        self.assertIsNone(self.worker.pending)
        self.assertEqual(self.worker.generation, generation)
        self.assertNotIn(job_id, self.worker.completed)
        self.timeout.assert_not_called()

    def test_different_wallpaper_still_supersedes_pending_or_active_sync(self):
        first = {'uri': 'file:///first.png', 'grub_enabled': True}
        latest = {'uri': 'file:///latest.png', 'grub_enabled': True}
        for active in [False, True]:
            with self.subTest(active=active):
                self.worker.active = self.worker.pending = None
                self.worker.timer = 0
                with patch('wallpaper_sync.worker.desktop_source', return_value=first), \
                     patch('wallpaper_sync.worker.threading.Thread'), patch('wallpaper_sync.worker.log'):
                    self.request('Sync', force=True)
                    old_id = self.worker.pending['id']
                    if active:
                        self.worker.start()
                    generation = self.worker.generation
                with patch('wallpaper_sync.worker.desktop_source', return_value=latest):
                    automatic = self.request('Sync')
                reply = json.loads(automatic.return_value.call_args.args[0].unpack()[0])
                self.assertNotEqual(reply['job_id'], old_id)
                self.assertEqual(self.worker.pending['source'], latest)
                self.assertGreater(self.worker.generation, generation)
                if active:
                    self.assertEqual(self.worker.active['id'], old_id)
                    self.assertLess(self.worker.active['generation'], self.worker.generation)
                else:
                    self.assertFalse(self.worker.completed[old_id]['success'])


if __name__ == '__main__':
    unittest.main()
