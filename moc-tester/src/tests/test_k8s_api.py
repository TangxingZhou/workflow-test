import pytest


@pytest.mark.p0
# @pytest.mark.skip(reason='测试k8s api')
def test_get_all_pods(k8s_api):
    ret = k8s_api.list_pod_for_all_namespaces(watch=False)
    for i in ret.items:
        print("%s\t%s\t%s" % (i.status.pod_ip, i.metadata.namespace, i.metadata.name))
    k8s_api.switch_k8s('controller')
    ret = k8s_api.list_pod_for_all_namespaces(watch=False)
    for i in ret.items:
        print("%s\t%s\t%s" % (i.status.pod_ip, i.metadata.namespace, i.metadata.name))

@pytest.mark.p0
# @pytest.mark.skip(reason='测试k8s api')
def test_get_all_unit_pods(k8s_unit_client):
    ret = k8s_unit_client.v1_api.list_pod_for_all_namespaces(watch=False)
    for i in ret.items:
        print("%s\t%s\t%s" % (i.status.pod_ip, i.metadata.namespace, i.metadata.name))

@pytest.mark.p0
# @pytest.mark.skip(reason='测试k8s api')
def test_get_all_controller_pods(k8s_controller_client):
    ret = k8s_controller_client.v1_api.list_pod_for_all_namespaces(watch=False)
    for i in ret.items:
        print("%s\t%s\t%s" % (i.status.pod_ip, i.metadata.namespace, i.metadata.name))
