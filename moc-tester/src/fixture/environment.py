import json
import os


class Environment:
    """
    environment
    """

    def __init__(self):
        self.path = os.path.join(os.environ.get('BASE_DIR')[:-3], 'config.json')

    def get_config(self, key):
        result = self.read_conf()
        val = result.get("active")
        env_ = val.get('env')
        if not env_:
            env_ = os.getenv('env')
        provider = val.get('provider')
        if not provider:
            provider = os.getenv('provider')
        if env_ not in result.keys():
            raise Exception(f"The {env_} environment does not exist.....")
        if provider not in result[env_].keys():
            raise Exception(f"The {provider} provider does not exist....")
        res = result.get(env_).get(provider).get(key)
        if res:
            return res
        else:
            if key in ('env', 'provider', 'region', 'k8s_unit_config', 'k8s_controller_config'):
                return os.environ.get(key)
            raise Exception(f"args {key} no exists...")

    def read_conf(self):
        with open(self.path, encoding="utf-8") as f:
            result = json.load(f)

        return result


if __name__ == '__main__':
    env = Environment()
    print(env)
