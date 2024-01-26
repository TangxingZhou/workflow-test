from src.cloud.k8s import K8sClient


class K8sApi:

    def __init__(self, k8s_client=None, name='default', config_file=None):
        if k8s_client is None:
            k8s_client = K8sClient(name, config_file)
        self.k8s_client = k8s_client

    def switch_k8s(self, name='default'):
        self.k8s_client = K8sClient.get_client(name)
        if self.k8s_client is None or self.k8s_client.api_client is None:
            raise Exception('Failed to switch k8s client.')

    def list_pod_for_all_namespaces(self, **kwargs):
        return self.k8s_client.v1_api.list_pod_for_all_namespaces(**kwargs)

    def list_deployment_for_all_namespaces(self, **kwargs):
        return self.k8s_client.apps_v1_api.list_deployment_for_all_namespaces(**kwargs)


if __name__ == '__main__':

    k8s_api = K8sApi()

    print("Listing pods with their IPs:")
    ret = k8s_api.list_pod_for_all_namespaces(watch=False)
    for i in ret.items:
        print("%s\t%s\t%s" % (i.status.pod_ip, i.metadata.namespace, i.metadata.name))
