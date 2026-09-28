"""Public installer tests: every simulated write is inside a temporary directory."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location('wpsync_installer', ROOT / 'tools/install.py')
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)
PROFILE = {'enabled': True, 'theme': 'test-theme', 'width': 1920, 'height': 1080}


class InstallerOptions(unittest.TestCase):
    def request(self, **kwargs):
        values = {'without_grub': False, 'grub_theme': None, 'grub_size': None, **kwargs}
        return installer.grub_request(argparse.Namespace(**values))

    def test_optional_grub_requires_explicit_complete_profile(self):
        self.assertIsNone(self.request())
        self.assertEqual(self.request(without_grub=True), {'enabled': False})
        self.assertEqual(self.request(grub_theme='test-theme', grub_size='1920x1080'), PROFILE)
        for values in [{'grub_theme':'test-theme'}, {'grub_size':'1920x1080'},
                       {'without_grub':True,'grub_theme':'test-theme'},
                       {'grub_theme':'../other','grub_size':'1920x1080'},
                       {'grub_theme':'safe','grub_size':'1920x1080x1'},
                       {'grub_theme':'safe','grub_size':'999999x999999'}]:
            with self.subTest(values=values), self.assertRaises(ValueError):
                self.request(**values)

    def test_roundtrip_root_arguments(self):
        self.assertEqual(installer.grub_arguments(PROFILE),
                         ['--grub-theme','test-theme','--grub-size','1920x1080'])
        self.assertEqual(installer.grub_arguments({'enabled':False}), ['--without-grub'])

    def test_unit_mount_allowlist_is_specific_to_configured_theme(self):
        unit = Path(installer.FIXED_FILES['systemd/wallpaper-sync-helper.service'])
        disabled = installer.system_files('testuser')[unit][0].decode()
        enabled = installer.system_files('testuser', PROFILE)[unit][0].decode()
        self.assertNotIn('/boot', disabled)
        line = next(line for line in enabled.splitlines() if line.startswith('ReadWritePaths='))
        self.assertTrue(line.endswith(' -/boot/grub2/themes/test-theme'))
        self.assertNotIn('/boot/grub2/themes/tela', enabled)
        self.assertIn('AmbientCapabilities=CAP_SETUID', enabled)
        with self.assertRaises(ValueError):
            installer.system_files('testuser', {**PROFILE, 'theme':'safe\nReadWritePaths=/etc'})

    def test_polkit_is_generated_for_selected_account_only(self):
        policy = installer.system_files('example-user')[installer.RULE][0].decode()
        self.assertIn('subject.user === "example-user"', policy)
        self.assertNotIn('@USERNAME@', policy)
        self.assertNotIn('subject.isInGroup', policy)

    def test_version_is_installed_with_runtime(self):
        self.assertEqual(installer.system_files('testuser')[installer.LIB/'VERSION'][0],
                         (ROOT/'VERSION').read_bytes())

    def test_root_phase_cannot_run_as_normal_user(self):
        with patch.object(installer.os, 'geteuid', return_value=1000), self.assertRaises(RuntimeError):
            installer.install_system(1000)


class InstallerMigration(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.owner = self.root/'owner.json'
        self.lib = self.root/'lib'
        self.unit = self.root/'helper.service'
        for name,value in [('OWNER',self.owner),('LIB',self.lib)]:
            patched = patch.object(installer,name,value);patched.start();self.addCleanup(patched.stop)
        patched = patch.dict(installer.FIXED_FILES, {'systemd/wallpaper-sync-helper.service':str(self.unit)})
        patched.start();self.addCleanup(patched.stop)

    def legacy_files(self):
        (self.lib/'wallpaper_sync').mkdir(parents=True)
        (self.lib/'wallpaper_sync/grub.py').write_text('legacy GRUB module')
        self.unit.write_text('ReadWritePaths=/run/wallpaper-sync -/boot/grub2/themes/tela\n')

    def test_fresh_install_defaults_to_gdm_only_even_with_orphan_files(self):
        self.legacy_files()
        self.assertEqual(installer.selected_grub_config(), {'enabled':False})

    def test_old_gdm_only_install_does_not_gain_grub(self):
        self.owner.write_text(json.dumps({'uid':1000}))
        self.assertEqual(installer.selected_grub_config(), {'enabled':False})

    def test_legacy_grub_profile_is_migrated_explicitly(self):
        self.legacy_files(); self.owner.write_text(json.dumps({'uid':1000}))
        self.assertEqual(installer.selected_grub_config(),
                         {'enabled':True,'theme':'tela','width':2880,'height':1800})
        self.assertEqual(installer.selected_grub_config({'enabled':False}), {'enabled':False})

    def test_public_profile_is_preserved_and_explicit_profile_wins(self):
        self.owner.write_text(json.dumps({'uid':1000,'grub':PROFILE}))
        self.assertEqual(installer.selected_grub_config(), PROFILE)
        other = {**PROFILE,'theme':'another'}
        self.assertEqual(installer.selected_grub_config(other), other)


class TemporaryInstallation(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.state = self.root/'var/lib/wallpaper-sync'
        changes = {'LIB':self.root/'usr/local/lib/wallpaper-sync', 'STATE':self.state,
                   'MANIFEST':self.state/'install-manifest.json', 'OWNER':self.root/'etc/wallpaper-sync/owner.json',
                   'THEME':self.root/'usr/share/gnome-shell/theme.gresource',
                   'RULE':self.root/'etc/polkit-1/rules.d/49-wallpaper-sync.rules'}
        for key,value in changes.items():
            mocked=patch.object(installer,key,value);mocked.start();self.addCleanup(mocked.stop)
        files = {source:str(self.root/Path(target).relative_to('/'))
                 for source,target in installer.FIXED_FILES.items()}
        mocked=patch.object(installer,'FIXED_FILES',files);mocked.start();self.addCleanup(mocked.stop)
        installer.THEME.parent.mkdir(parents=True)
        installer.THEME.write_bytes(b'temporary Fedora theme')
        # Only emulate privilege/ownership checks; real writes stay in the temp root.
        mocks = [patch.object(installer.os,'geteuid',return_value=0),
                 patch.object(installer,'ensure_root_path'),
                 patch.object(installer.pwd,'getpwuid',return_value=SimpleNamespace(pw_name='testuser'))]
        for mocked in mocks:mocked.start();self.addCleanup(mocked.stop)
        mocked=patch.object(installer,'command');self.command=mocked.start();self.addCleanup(mocked.stop)

    def test_fresh_install_records_disabled_grub_and_version(self):
        installer.install_system(1000)
        owner=json.loads(installer.OWNER.read_text())
        self.assertEqual(owner['grub'],{'enabled':False})
        manifest=json.loads(installer.MANIFEST.read_text())
        self.assertEqual(manifest['version'],2)
        self.assertEqual(manifest['release'],(ROOT/'VERSION').read_text().strip())
        self.assertTrue(all(Path(path).is_relative_to(self.root) for path in manifest['files']))
        self.assertEqual(installer.OWNER.stat().st_mode & 0o777,0o644)
        self.assertEqual(installer.MANIFEST.stat().st_mode & 0o777,0o600)

    def test_theme_validation_failure_changes_no_installed_files(self):
        installer.install_system(1000)
        original={p:p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        with patch('wallpaper_sync.grub.GrubBackend.status',side_effect=RuntimeError('invalid theme')):
            with self.assertRaisesRegex(RuntimeError,'invalid theme'):
                installer.install_system(1000,PROFILE)
        self.assertEqual(original,{p:p.read_bytes() for p in self.root.rglob('*') if p.is_file()})

    def test_failed_upgrade_restores_original_configuration_and_files(self):
        installer.install_system(1000)
        original={p:p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        def fail_label(command,**kwargs):
            if command[0]=='/usr/bin/restorecon':raise OSError('simulated install failure')
        self.command.side_effect=fail_label
        with patch('wallpaper_sync.grub.GrubBackend.status',return_value={'available':True}):
            with self.assertRaisesRegex(OSError,'simulated'):
                installer.install_system(1000,PROFILE)
        self.assertEqual(original,{p:p.read_bytes() for p in self.root.rglob('*') if p.is_file()})

    def test_dbus_reload_failure_reloads_restored_service_configuration(self):
        installer.install_system(1000)
        self.command.reset_mock()
        original=installer.OWNER.read_bytes()
        failures=0
        def fail_first_bus_reload(command,**kwargs):
            nonlocal failures
            if command[0]=='/usr/bin/busctl':
                failures+=1
                if failures==1:raise OSError('simulated D-Bus reload failure')
        self.command.side_effect=fail_first_bus_reload
        with patch('wallpaper_sync.grub.GrubBackend.status',return_value={'available':True}):
            with self.assertRaisesRegex(OSError,'D-Bus reload'):
                installer.install_system(1000,PROFILE)
        self.assertEqual(installer.OWNER.read_bytes(),original)
        reloads=[call for call in self.command.call_args_list
                 if call.args[0]==['/usr/bin/systemctl','daemon-reload']]
        self.assertEqual(len(reloads),2)


class MainFlow(unittest.TestCase):
    def test_fresh_install_does_not_stop_absent_user_service(self):
        with tempfile.TemporaryDirectory() as temporary:
            commands=[]
            def command(arguments,**kwargs):
                commands.append(arguments)
                return SimpleNamespace(stdout='GNOME Shell 50.0\n')
            original_read=Path.read_text
            def read(path,*args,**kwargs):
                if str(path)=='/etc/os-release':return 'ID=fedora\nVERSION_ID=44\n'
                return original_read(path,*args,**kwargs)
            with patch.object(installer.sys,'argv',['install.py','--yes']), \
                 patch.object(installer.os,'geteuid',return_value=1000), \
                 patch.object(installer,'selected_grub_config',return_value={'enabled':False}), \
                 patch.object(installer,'plan'),patch.object(installer,'preflight_user'), \
                 patch.object(installer,'user_locations',return_value={'manifest':Path(temporary)/'missing.json'}), \
                 patch.object(installer,'command',side_effect=command), \
                 patch.object(installer.subprocess,'run',return_value=SimpleNamespace(returncode=0)), \
                 patch.object(installer,'install_user') as install_user, \
                 patch.object(Path,'read_text',read):
                installer.main()
            self.assertNotIn(['systemctl','--user','stop','wallpaper-sync.service'],commands)
            self.assertTrue(any('--system-install' in command for command in commands))
            install_user.assert_called_once_with({'enabled':False},set_grub_preference=True,legacy_grub_default=False)

    def test_legacy_default_migrates_but_explicit_false_is_preserved(self):
        from unittest.mock import Mock
        for saved in [None,False,True]:
            config=Mock();config.get_user_value.return_value=saved
            installer.configure_user_grub(config,PROFILE,legacy_default=True)
            if saved is None:config.set_boolean.assert_called_once_with('grub-enabled',True)
            else:config.set_boolean.assert_not_called()

    def test_explicit_disabled_profile_overrides_user_preference(self):
        from unittest.mock import Mock
        config=Mock()
        installer.configure_user_grub(config,{'enabled':False},explicit=True)
        config.set_boolean.assert_called_once_with('grub-enabled',False)

if __name__=='__main__':unittest.main()
