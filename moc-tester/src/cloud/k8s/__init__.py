from kubernetes import client, config
from kubernetes.client import Configuration, ApiClient
import os


current_dir = os.path.dirname(os.path.abspath(__file__))


class K8sClient:

    _clients = {}

    def __init__(self, name='default', config_file=None):
        try:
            config.load_kube_config(config_file)
            self.api_client = ApiClient()
        except config.config_exception.ConfigException:
            self.api_client = None
        K8sClient._clients[name] = self

    def customize(self, name='default', host="http://localhost", ssl_ca_cert=None, cert_file=None, key_file=None):
        conf = Configuration(host)
        kube_configs_path = os.path.join(os.path.dirname(current_dir), 'kube-configs')
        if not os.path.isdir(kube_configs_path):
            os.makedirs(kube_configs_path)
        if ssl_ca_cert:
            if os.path.isfile(os.path.join(kube_configs_path, f'{ssl_ca_cert}')):
                conf.ssl_ca_cert = os.path.isfile(os.path.join(kube_configs_path, f'{ssl_ca_cert}'))
            else:
                cert_path = os.path.join(kube_configs_path, f'ssl_ca_cert.{name}')
                conf.ssl_ca_cert = cert_path
                with open(cert_path, 'w+') as c_file:
                    c_file.write(ssl_ca_cert)
                os.chmod(cert_path, 0o644)
        if cert_file:
            if os.path.isfile(os.path.join(kube_configs_path, f'{cert_file}')):
                conf.cert_file = os.path.isfile(os.path.join(kube_configs_path, f'{cert_file}'))
            else:
                cert_path = os.path.join(kube_configs_path, f'cert_file.{name}')
                conf.cert_file = cert_path
                with open(cert_path, 'w+') as c_file:
                    c_file.write(cert_file)
                os.chmod(cert_path, 0o644)
        if key_file:
            if os.path.isfile(os.path.join(kube_configs_path, f'{key_file}')):
                conf.cert_file = os.path.isfile(os.path.join(kube_configs_path, f'{key_file}'))
            else:
                cert_path = os.path.join(kube_configs_path, f'key_file.{name}')
                conf.key_file = cert_path
                with open(cert_path, 'w+') as c_file:
                    c_file.write(key_file)
                os.chmod(cert_path, 0o644)
        Configuration.set_default(conf)
        self.api_client = ApiClient()

    @classmethod
    def get_client(cls, name):
        return cls._clients.get(name)

    @property
    def v1_api(self):
        return client.CoreV1Api(self.api_client)

    @property
    def apps_v1_api(self):
        return client.AppsV1Api(self.api_client)
