import os

import pytest
from src.cloud.k8s import K8sClient
from src.fixture.internal.k8s_api import K8sApi
from src.fixture.environment import Environment


current_dir = os.path.dirname(os.path.abspath(__file__))


@pytest.fixture(name='env', scope='session')
def env_():
    return Environment()


def init_k8s_client(name):
    env = Environment()
    k8s_config = env.get_config(f'k8s_{name}_config')
    try:
        k8s_host = env.get_config(f'k8s_{name}_host')
    except Exception:
        k8s_host = None
    try:
        k8s_ca_cert = env.get_config(f'k8s_{name}_ca_cert')
    except Exception:
        k8s_ca_cert = None
    try:
        k8s_user_cert = env.get_config(f'k8s_{name}_user_cert')
    except Exception:
        k8s_user_cert = None
    try:
        k8s_user_key = env.get_config(f'k8s_{name}_user_key')
    except Exception:
        k8s_user_key = None
    if k8s_config:
        if os.path.isfile(k8s_config):
            k8s_client = K8sClient(name, k8s_config)
        else:
            kube_configs_path = os.path.join(os.path.dirname(current_dir), 'cloud', 'kube-configs')
            if not os.path.isdir(kube_configs_path):
                os.makedirs(kube_configs_path)
            config_path = os.path.join(kube_configs_path, f'config.{name}')
            with open(config_path, 'w+') as config_file:
                config_file.write(k8s_config)
            os.chmod(config_path, 0o644)
            k8s_client = K8sClient(name, config_path)
    else:
        k8s_client = K8sClient(name)
        if k8s_host and k8s_ca_cert and k8s_user_cert and k8s_user_key:
            k8s_client.customize(name, k8s_host, k8s_ca_cert, k8s_user_cert, k8s_user_key)
    return k8s_client


@pytest.fixture(name='k8s_api', scope='session')
def k8s_api():
    init_k8s_client('controller')
    return K8sApi(init_k8s_client('unit'))


@pytest.fixture(name='k8s_unit_client', scope='session')
def k8s_unit_client():
    return init_k8s_client('unit')


@pytest.fixture(name='k8s_controller_client', scope='session')
def k8s_controller_client():
    return init_k8s_client('controller')
