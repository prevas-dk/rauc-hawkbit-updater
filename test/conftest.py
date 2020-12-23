# SPDX-License-Identifier: LGPL-2.1-only
# SPDX-FileCopyrightText: 2021 Enrico Jörns <e.joerns@pengutronix.de>, Pengutronix
# SPDX-FileCopyrightText: 2021 Bastian Krause <bst@pengutronix.de>, Pengutronix

import os
import sys
from configparser import ConfigParser

import pytest

from hawkbit_mgmt import HawkbitMgmtTestClient, HawkbitError
from helper import run_pexpect

def pytest_addoption(parser):
    """Register custom argparse-style options."""
    parser.addoption(
        '--hawkbit-instance',
        help='HOST:PORT of hawkBit instance to use (default: %(default)s)',
        default='localhost:8080')

@pytest.fixture(scope='session')
def hawkbit(pytestconfig):
    """Instance of HawkbitMgmtTestClient connecting to a hawkBit instance."""
    from uuid import uuid4

    host, port = pytestconfig.option.hawkbit_instance.split(':')
    client = HawkbitMgmtTestClient(host, int(port))

    client.set_config('pollingTime', '00:00:30')
    client.set_config('pollingOverdueTime', '00:03:00')
    client.set_config('authentication.targettoken.enabled', True)
    client.set_config('authentication.gatewaytoken.enabled', True)
    client.set_config('authentication.gatewaytoken.key', uuid4().hex)

    return client

@pytest.fixture
def hawkbit_target_added(hawkbit):
    """Creates a hawkBit target."""
    target = hawkbit.add_target()
    yield target

    hawkbit.delete_target(target)

@pytest.fixture
def config(tmp_path, hawkbit, hawkbit_target_added):
    """
    Creates a temporary rauc-hawkbit-updater configuration matching the hawkBit (target)
    configuration of the hawkbit and hawkbit_target_added fixtures.
    """
    target = hawkbit.get_target()
    target_token = target.get('securityToken')
    target_name = target.get('name')
    bundle_location = tmp_path / 'bundle.raucb'

    hawkbit_config = ConfigParser()
    hawkbit_config['client'] = {
        'hawkbit_server': f'{hawkbit.host}:{hawkbit.port}',
        'ssl': 'false',
        'ssl_verify': 'false',
        'tenant_id': 'DEFAULT',
        'target_name': target_name,
        'auth_token': target_token,
        'bundle_download_location': str(bundle_location),
        'retry_wait': '60',
        'connect_timeout': '20',
        'timeout': '60',
        'log_level': 'debug',
    }
    hawkbit_config['device'] = {
        'product': 'Terminator',
        'model': 'T-1000',
        'serialnumber': '8922673153',
        'hw_revision': '2',
        'mac_address': 'ff:ff:ff:ff:ff:ff',
    }

    tmp_config = tmp_path / 'rauc-hawkbit-updater.conf'
    with tmp_config.open('w') as f:
        hawkbit_config.write(f)
    return tmp_config

@pytest.fixture
def adjust_config(config):
    """
    Adjusts the rauc-hawkbit-updater configuration created by the config fixture by
    adding/overwriting or removing options.
    """
    config_files = []
    def _adjust_config(options={'client': {}}, remove={}):
        adjusted_config = ConfigParser()
        adjusted_config.read(config)

        # update
        for section, option in options.items():
            for key, value in option.items():
                adjusted_config.set(section, key, value)

        # remove
        for section, option in remove.items():
            adjusted_config.remove_option(section, option)

        with config.open('w') as f:
            adjusted_config.write(f)
        return config

    return _adjust_config

@pytest.fixture(scope='session')
def rauc_bundle(tmp_path_factory):
    """Creates a temporary 512 KB file to be used as a dummy RAUC bundle."""
    bundle = tmp_path_factory.mktemp('data') / 'bundle.raucb'
    bundle.write_bytes(os.urandom(512)*1024)
    return str(bundle)

@pytest.fixture
def bundle_assigned(hawkbit, hawkbit_target_added, rauc_bundle):
    """
    Creates a softwaremodule containing the file from the rauc_bundle fixture as an artifact.
    Creates a distributionset from this softwaremodule. Assigns this distributionset to the target
    created by the hawkbit_target_added fixture. Returns the corresponding action ID of this
    assignment.
    """
    swmodule = hawkbit.add_softwaremodule()
    artifact = hawkbit.add_artifact(rauc_bundle, swmodule)
    distributionset = hawkbit.add_distributionset(swmodule)
    action = hawkbit.assign_target(distributionset)

    yield action

    try:
        hawkbit.cancel_action(action, hawkbit_target_added, force=True)
    except HawkbitError:
        pass

    hawkbit.delete_distributionset(distributionset)
    hawkbit.delete_artifact(artifact, swmodule)
    hawkbit.delete_softwaremodule(swmodule)

@pytest.fixture
def rauc_dbus_install_success(rauc_bundle):
    """
    Creates a RAUC D-Bus dummy interface on the SessionBus mimicing a successful installation on
    Install().
    """
    proc = run_pexpect(f'{sys.executable} -m rauc_dbus_dummy {rauc_bundle}',
                       cwd=os.path.dirname(__file__))
    proc.expect('Interface published')

    yield

    assert proc.isalive()
    assert proc.terminate(force=True)

@pytest.fixture
def rauc_dbus_install_failure(rauc_bundle):
    """
    Creates a RAUC D-Bus dummy interface on the SessionBus mimicing a failing installation on
    Install().
    """
    proc = run_pexpect(f'{sys.executable} -m rauc_dbus_dummy {rauc_bundle} --completed-code=1',
                       cwd=os.path.dirname(__file__), timeout=None)
    proc.expect('Interface published')

    yield

    assert proc.isalive()
    assert proc.terminate(force=True)
