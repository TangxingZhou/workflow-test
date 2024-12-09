import re
import time
import yaml
import argparse
import logging
import subprocess
from jinja2 import Template
from functools import reduce
from kubernetes.client import ApiException
from kubernetes.watch import watch
from src.tests.conftest import init_k8s_client
from src.cloud.k8s.client import K8sClient
from src.cloud.moc.cluster import MOClusterManager
from src.upgrade.consts import *
from src.upgrade.utils import wait_for
from src.upgrade.main import init_logging
from src.upgrade.instance import MOInstance
from src.cloud.k8s.secret import read_secret
from src.cloud.k8s.pod import namespaced_pod_successfully_completed


migrate_cm_template = Template('''
apiVersion: v1
kind: ConfigMap
metadata:
  name: migrate-config
data:
  input.json: |
    {
        "name": "SHARED",
        "endpoint": "http://oss-cn-hangzhou-internal.aliyuncs.com",
        "bucket": "{{ args.bucket }}",
        "key_prefix": "{{ args.bucket_path }}/data",
        "mem_cache": 1073741824,
        "disk_cache": 10737418240,
        "disk_path": "temp"
    }
''')

migrate_pod_template = Template('''
apiVersion: v1
kind: Pod
metadata:
  name: {{ args.migrate_job }}
spec:
  restartPolicy: Never
  # tolerations:
  #   - effect: NoSchedule
  #     key: matrixone.cloud/backup
  #     operator: Exists
  # nodeSelector:
  #   matrixone.cloud/for-component: backup
  containers:
    - name: migrate
      image: registry.cn-hangzhou.aliyuncs.com/mocloud/mo-migrate:{{ args.migrate_tag }}
      command:
        - /mo-migrate
        - {{ action }}
      args:
        - -i
        - /input.json
{%- if action == 'replay' %}
        - -t
        - $(TID)
{%- endif %}
      # resources:
      #   requests:
      #     cpu: "14"
      #     memory: "27Gi"
      #   limits:
      #     cpu: "14"
      #     memory: "27Gi"
      env:
        - name: TID
          value: "{{ args.tid | default('272457') }}"
        - name: AWS_REGION
          value: cn-hangzhou
        - name: AWS_ACCESS_KEY_ID
          valueFrom:
            secretKeyRef:
              key: RESTORE_ACCESS_KEY_ID
              name: aliyun-moc-backup-test
        - name: AWS_SECRET_ACCESS_KEY
          valueFrom:
            secretKeyRef:
              key: RESTORE_SECRET_ACCESS_KEY
              name: aliyun-moc-backup-test
      volumeMounts:
        - name: config
          mountPath: /input.json
          subPath: input.json
  volumes:
    - name: config
      configMap:
        name: migrate-config
        defaultMode: 0644
''')


def parse_args(name, description=None, usage='%(prog)s [options] args'):
    parser = argparse.ArgumentParser(prog=name, description=description, usage=usage)
    parser.add_argument('--cluster-name', action='store', required=True, type=str,
                        dest='cluster_name', help='Name of MO cluster')
    parser.add_argument('--upgrade-version', action='store', required=True, type=str,
                        dest='upgrade_version', help='Version of MO cluster to upgrade to')
    parser.add_argument('--ckp-version', action='store', required=False, type=str, default='v1.2.3-bce127554-2024-11-26',
                        dest='ckp_version', help='Version of MO CKP')
    parser.add_argument('--migrate-tag', action='store', required=False, type=str, default='3288fb5',
                        dest='migrate_tag', help='Migrate image tag')
    parser.add_argument('--migrate-ns', action='store', required=False, type=str, default='moc-upgrade-test',
                        dest='migrate_ns', help='Namespace for migrate job')
    parser.add_argument('--migrate-job', action='store', required=False, type=str, default='mo-migrate',
                        dest='migrate_job', help='Name of migrate job')
    parser.add_argument('--cluster-image-repository', action='store', required=False, type=str,
                        default='registry.cn-hangzhou.aliyuncs.com/mocloud/matrixone',
                        dest='cluster_image_repository', help='Image repository of MO cluster')
    # parser.add_argument('--bucket', action='store', required=False, type=str, default='moc-backup-test',
    #                     dest='bucket', help='S3 bucket')
    # parser.add_argument('--bucket-path', action='store', required=True, type=str,
    #                     dest='bucket_path', help='S3 bucket path')
    # parser.add_argument('--tid', action='store', required=False, type=str, default='',
    #                     dest='tid', help='Table ID of system.rawlog')
    parser.add_argument('--verbose', action='store_true', required=False, default=False, dest='verbose',
                        help='Show the verbose information, off by default')
    parser.set_defaults()
    return parser


def mo_root(_unit_client: K8sClient, args: argparse.Namespace):
    cluster = _unit_client.core_matrixone_cloud_v1alpha1_api.read_cluster(args.cluster_name)
    assert cluster[1] == 200, f"Failed to read cluster '{args.cluster_name}'."
    root_secret = read_secret(_unit_client, 'root', args.cluster_name)
    logger.info(
        f"Connect to root account: mysql -h {cluster[0].status.endpoint.address} "
        f"-P {cluster[0].status.endpoint.port} -u {root_secret['username']} -p{root_secret['password']}")
    _mo_root = MOInstance(
        host=cluster[0].status.endpoint.address,
        user=root_secret['username'],
        password=root_secret['password'],
        port=cluster[0].status.endpoint.port
    )
    return _mo_root

def get_table_id(_unit_client: K8sClient, args: argparse.Namespace):
    sql = "select rel_id from mo_catalog.mo_tables where relname = 'rawlog';"
    tid = mo_root(_unit_client, args)._execute(sql)[0]['rel_id']
    logger.info(f'Table ID of system.rawlog is {tid}.')
    return tid

def upgrade_tenants(_unit_client: K8sClient, args: argparse.Namespace):
    sql = "UPGRADE ACCOUNT ALL;"
    logger.info('Start to upgrade tenants.')
    mo_root(_unit_client, args)._execute(sql, False)

def watch_dn_logs(_unit_client: K8sClient, args: argparse.Namespace, event, *keywords):
    logger.info(f'Start to watch logs of pod {args.cluster_name}/default-dn-0')
    _watch = watch.Watch()
    log_stream = _watch.stream(_unit_client.v1_api.read_namespaced_pod_log, 'default-dn-0', args.cluster_name,
                               tail_lines=0)
    for log in log_stream:
        if reduce(lambda x, y: x and y,
                  map(lambda k: True if re.search(re.compile(k), log) else False, keywords)):
            _watch.stop()
            logger.debug(f"Log line '{log}' matches the regex expressions: {', '.join(keywords)}")
            logger.info('CKP completes.')
            event.set()


def containers_image_upgraded(_unit_client: K8sClient, namespace, expected_image, **kwargs):
    for pod in _unit_client.v1_api.list_namespaced_pod(namespace, **kwargs).items:
        logger.debug(f"Containers of pod '{namespace}/{pod.metadata.name}': {', '.join([c.name + '(' + c.image + ')' for c in pod.spec.containers])}")
        for container in pod.spec.containers:
            if container.name == 'main':
                if container.image != expected_image or pod.status.phase != 'Running':
                    logger.debug(f"Container named 'main' of pod '{namespace}/{pod.metadata.name}': "
                                 f"expected image is {expected_image} rather than {container.image}.")
                    return False
    return True


def cluster_pods_are_running(_controller_client: K8sClient, _unit_client: K8sClient, args: argparse.Namespace, expected_image):
    for k, v in ROOT_CLUSTER_INIT_PODS.items():
        if k == 'local-service':
            continue
        namespace = args.cluster_name
        pods = _unit_client.v1_api.list_namespaced_pod(
            namespace, label_selector=v['label_selector'].format(cluster_name=args.cluster_name)).items
        logger.debug(f"List {namespace}/pods with labels "
                     f"'{v['label_selector'].format(cluster_name=args.cluster_name)}':\n"
                     f"{', '.join([p.metadata.name for p in pods])}")
        if len(pods) != v['replicas']:
            logger.debug(f"Expected number of {namespace}/pods with labels "
                         f"'{v['label_selector'].format(cluster_name=args.cluster_name)}' is {v['replicas']}, not {len(pods)}.")
            return False
        for pod in pods:
            if pod.status.phase != 'Running':
                logger.debug(f"Pod '{namespace}/{pod.metadata.name}' is in phase of '{pod.status.phase}', "
                             f"not running.")
                return False
            logger.debug(f"Containers of pod '{namespace}/{pod.metadata.name}': {', '.join([c.name + '(' + c.image + ')' for c in pod.spec.containers])}")
            for container in pod.spec.containers:
                if container.name == 'main':
                    if container.image != expected_image:
                        logger.debug(f"Container named 'main' of pod '{namespace}/{pod.metadata.name}': "
                                     f"expected image is {expected_image} rather than {container.image}.")
                        return False
    return True


def start_migrate_job(_unit_client: K8sClient, args: argparse.Namespace, action='replay'):
    body = yaml.safe_load(migrate_pod_template.render(args=args, action=action))
    migrate_pod = _unit_client.v1_api.create_namespaced_pod_with_http_info(args.migrate_ns, body)
    assert migrate_pod[1] == 201, f"Failed to create migrate pod {body['metadata']['name']}."
    migrate_commands = ' '.join(body['spec']['containers'][0]['command'] + body['spec']['containers'][0]['args'])
    logger.info(f"Start to make migration with command of mo-migrate as: {migrate_commands}")
    # 等待恢复数据的任务执行成功
    wait_for(namespaced_pod_successfully_completed,
             5, 10 * 60,
             _unit_client, body['metadata']['name'], args.migrate_ns)


def make_ckp(_controller_client: K8sClient, _unit_client: K8sClient, args: argparse.Namespace, *keywords):
    # event = threading.Event()
    # if keywords:
    #     threading.Thread(target=watch_dn_logs, args=(_unit_client, args, event, *keywords), daemon=True).start()
    logger.info('To make CKP.')
    root_cluster = _controller_client.core_matrixone_cloud_v1alpha1_api.read_cluster(args.cluster_name, _preload_content=False)
    assert root_cluster[1] == 200, f"Failed to read root cluster '{args.cluster_name}'."
    body = {
        'spec': {
            'version': args.ckp_version
        }
    }
    patch_root_cluster = _controller_client.core_matrixone_cloud_v1alpha1_api.patch_cluster(args.cluster_name, body)
    assert patch_root_cluster[1] == 200, f"Failed to patch root cluster '{args.cluster_name}'."
    wait_for(containers_image_upgraded,
             3, PODS_BECOME_RUNNING_TIMEOUT,
             _unit_client, args.cluster_name, f'{args.cluster_image_repository}:{args.ckp_version}', label_selector='matrixorigin.io/component=DNSet')
    start = time.time()
    while True:
        try:
            dn_logs = _unit_client.v1_api.read_namespaced_pod_log('default-dn-0', args.cluster_name)
            if re.search(re.compile('migration checkpointed'), dn_logs):
                logger.info('CKP completes.')
                break
        except Exception as e:
            logger.error(e)
        time.sleep(1)
        if time.time() - start >= 10 * 60:
            msg = 'Timeout to await CKP completes.'
            logger.error(msg)
            raise Exception(msg)
    # if keywords:
    #     event.wait(15 * WAIT_ONE_MINUTE)


def config_migrate(_unit_client: K8sClient, args: argparse.Namespace):
    root_cluster = _unit_client.core_matrixone_cloud_v1alpha1_api.read_cluster(args.cluster_name, _preload_content=False)
    assert root_cluster[1] == 200, f"Failed to read root cluster '{args.cluster_name}'."
    bucket, bucket_path = root_cluster[0]['spec']['managed']['objectStorage']['path'].split('/')
    setattr(args, 'bucket', bucket)
    setattr(args, 'bucket_path', bucket_path)
    cms = _unit_client.v1_api.list_namespaced_config_map(args.migrate_ns)
    body = yaml.safe_load(migrate_cm_template.render(args=args))
    if body['metadata']['name'] in [c.metadata.name for c in cms.items]:
        migrate_cm = unit_client.v1_api.patch_namespaced_config_map_with_http_info('migrate-config', args.migrate_ns, {'data': body['data']})
        assert migrate_cm[1] == 200, f"Failed to patch configmap {body['metadata']['name']}."
    else:
        migrate_cm = unit_client.v1_api.create_namespaced_pod_with_http_info(args.migrate_ns, body)
        assert migrate_cm[1] == 201, f"Failed to create configmap {body['metadata']['name']}."

def make_migrate(_unit_client: K8sClient, args: argparse.Namespace):
    try:
        start_migrate_job(_unit_client, args, 'backup')
    except Exception as e:
        logger.error(e)
        return False
    finally:
        _unit_client.v1_api.delete_namespaced_pod(args.migrate_job, args.migrate_ns)
        wait_for(lambda c, n: len([p.metadata.name for p in c.v1_api.list_namespaced_pod(n).items]) == 0,
                 5, PODS_BECOME_RUNNING_TIMEOUT,
                 _unit_client, f'{args.migrate_ns}')
    try:
        start_migrate_job(_unit_client, args, 'replay')
    except Exception as e:
        logger.error(e)
        return False
    finally:
        _unit_client.v1_api.delete_namespaced_pod(args.migrate_job, args.migrate_ns)
        wait_for(lambda c, n: len([p.metadata.name for p in c.v1_api.list_namespaced_pod(n).items]) == 0,
                 5, PODS_BECOME_RUNNING_TIMEOUT,
                 _unit_client, f'{args.migrate_ns}')


def wait_for_cn_offload(_unit_client: K8sClient, args: argparse.Namespace, label_selector='matrixorigin.io/component=CNSet', timeout=PODS_BECOME_RUNNING_TIMEOUT, interval=5):
    logger.info(f"Wait for CN pods offload with labels: {label_selector}.")
    start = time.time()
    while time.time() - start < timeout if timeout > 0 else True:
        cn_pods = _unit_client.v1_api.list_namespaced_pod(args.cluster_name, label_selector=label_selector)
        if len(cn_pods.items) == 0:
            return
        else:
            for pod in cn_pods.items:
                if pod.metadata.labels.get('pool.matrixorigin.io/phase') == 'Draining' and pod.metadata.deletion_timestamp is None:
                    try:
                        logger.info(f"Delete pod {pod.metadata.name} in phase of 'Draining'.")
                        _unit_client.v1_api.delete_namespaced_pod(pod.metadata.name, args.cluster_name)
                    except ApiException as e:
                        logger.debug(e.body)
        logger.debug(f"Wait for CN pods offload with labels: {label_selector}.")
        time.sleep(interval)
    msg = f"Timeout to await CN pods offload with labels: {label_selector}."
    raise Exception(msg)


def wait_for_ob_idle(_unit_client: K8sClient, args: argparse.Namespace, timeout=15 * 60, interval=10):
    root_cluster = _unit_client.core_matrixone_cloud_v1alpha1_api.read_cluster(args.cluster_name,_preload_content=False)
    assert root_cluster[1] == 200, f"Failed to read root cluster '{args.cluster_name}'."
    bucket, bucket_path = root_cluster[0]['spec']['managed']['objectStorage']['path'].split('/')
    # ret = subprocess.run(['mc', 'ls', '-r', f'oss/{bucket}/{bucket_path}/etl'], shell=False, capture_output=True, text=True)
    time.sleep(30)
    start = time.time()
    while time.time() - start < timeout if timeout > 0 else True:
        ret = subprocess.run(f'./mc ls -r oss/{bucket}/{bucket_path}/etl | grep "statement_info/.*csv"', shell=True,
                             capture_output=True, text=True)
        logger.debug(ret)
        if ret.returncode == 1:
            return
        logger.debug(f"Wait for OB to be idle.")
        time.sleep(interval)
    msg = f"Timeout to await OB to be idle."
    raise Exception(msg)

def offload_ob_cn(_controller_client: K8sClient, _unit_client: K8sClient, args: argparse.Namespace):
    logger.info(f'Await OB CN to be idle.')
    ob_sys_pod_body = {
        'metadata': {
            'annotations': {
                'matrixone.cloud/resource-range': '{"min":{"cpu":"4","memory":"12Gi"},"max":{"cpu":"15","memory":"120Gi"}}'
            }
        }
    }
    for p in _unit_client.v1_api.list_namespaced_pod(args.cluster_name, label_selector=f'matrixorigin.io/owner={args.cluster_name}-ob-sys').items:
        logger.info(f'Reset resources range for ob-sys CN pod: {p.metadata.name}.')
        patch_ob_sys_pod = _unit_client.v1_api.patch_namespaced_pod_with_http_info(p.metadata.name, args.cluster_name, ob_sys_pod_body)
        assert patch_ob_sys_pod[1] == 200, f"Failed to patch ob-sys CN pod: {p.metadata.name}."
    wait_for_ob_idle(_unit_client, args)
    ob_sys_cluster = _controller_client.core_matrixone_cloud_v1alpha1_api.read_cluster(f'{args.cluster_name}-ob-sys', _preload_content=False)
    if ob_sys_cluster[1] == 404:
        logger.info(f"Cluster '{args.cluster_name}-ob-sys' is not found.")
    elif ob_sys_cluster[1] == 200:
        ob_sys_cluster[0]['spec']['cnSets'][0]['scalingConfig']['maxReplicas'] = 0
        ob_sys_cluster[0]['spec']['cnSets'][0]['scalingConfig']['minReplicas'] = 0
        ob_sys_body = {
            'spec': {
                'cnSets': ob_sys_cluster[0]['spec']['cnSets']
            }
        }
        logger.info(f'Offload CN of ob-sys cluster {args.cluster_name}-ob-sys.')
        patch_ob_sys_cluster = _controller_client.core_matrixone_cloud_v1alpha1_api.patch_cluster(
            f'{args.cluster_name}-ob-sys', ob_sys_body)
        assert patch_ob_sys_cluster[1] == 200, f"Failed to patch cluster '{args.cluster_name}-ob-sys'."
        logger.info(f'Await CN of ob-sys to offload.')
        wait_for_cn_offload(_unit_client, args, f'matrixorigin.io/component=CNSet')
    else:
        raise Exception(f"Failed to read cluster '{args.cluster_name}-ob-sys'.")


def offload_cn_and_proxy(_controller_client: K8sClient, _unit_client: K8sClient, args: argparse.Namespace):
    logger.info(f"Start to offload CN and Proxy in cluster of {args.cluster_name}.")
    root_cluster = _controller_client.core_matrixone_cloud_v1alpha1_api.read_cluster(args.cluster_name, _preload_content=False)
    assert root_cluster[1] == 200, f"Failed to read root cluster '{args.cluster_name}'."
    root_cluster[0]['spec']['cnSets'][0]['replicas'] = 0
    root_cluster[0]['spec']['cnSets'][0]['scalingConfig']['maxReplicas'] = 0
    root_cluster[0]['spec']['cnSets'][0]['scalingConfig']['minReplicas'] = 0
    root_cluster[0]['spec']['cnPools'][0]['poolStrategy']['scaleStrategy']['maxIdle'] = 0
    body = {
        'spec': {
            'cnPools': root_cluster[0]['spec']['cnPools'],
            # 'endpoint': {
            #     'proxySpec': {
            #         'replicas': 0,
            #     }
            # },
            'cnSets': root_cluster[0]['spec']['cnSets']
        }
    }
    logger.info(f'Offload CN of root cluster {args.cluster_name}.')
    patch_root_cluster = _controller_client.core_matrixone_cloud_v1alpha1_api.patch_cluster(args.cluster_name, body)
    assert patch_root_cluster[1] == 200, f"Failed to patch root cluster '{args.cluster_name}'."
    sys_cluster = _controller_client.core_matrixone_cloud_v1alpha1_api.read_cluster(f'{args.cluster_name}-sys', _preload_content=False)
    if sys_cluster[1] == 404:
        logger.info(f"Cluster '{args.cluster_name}-sys' is not found.")
    elif sys_cluster[1] == 200:
        sys_cluster[0]['spec']['cnSets'][0]['scalingConfig']['maxReplicas'] = 0
        sys_cluster[0]['spec']['cnSets'][0]['scalingConfig']['minReplicas'] = 0
        sys_body = {
            'spec': {
                'cnSets': sys_cluster[0]['spec']['cnSets']
            }
        }
        logger.info(f'Offload CN of sys cluster {args.cluster_name}-sys.')
        patch_sys_cluster = _controller_client.core_matrixone_cloud_v1alpha1_api.patch_cluster(
            f'{args.cluster_name}-sys', sys_body)
        assert patch_sys_cluster[1] == 200, f"Failed to patch cluster '{args.cluster_name}-sys'."
    else:
        raise Exception(f"Failed to read cluster '{args.cluster_name}-sys'.")
    logger.info(f'Await CN except ob-sys to offload.')
    wait_for_cn_offload(_unit_client, args, f'matrixorigin.io/component=CNSet,matrixorigin.io/owner!={args.cluster_name}-ob-sys')
    body = {
        'spec': {
            'endpoint': {
                'proxySpec': {
                    'replicas': 0,
                }
            }
        }
    }
    logger.info(f'Offload proxy of root cluster {args.cluster_name}.')
    patch_root_cluster = _controller_client.core_matrixone_cloud_v1alpha1_api.patch_cluster(args.cluster_name, body)
    assert patch_root_cluster[1] == 200, f"Failed to patch root cluster '{args.cluster_name}'."
    wait_for(lambda c, n, s: len([p.metadata.name for p in c.v1_api.list_namespaced_pod(n, label_selector=s).items]) == 0,
             5, PODS_BECOME_RUNNING_TIMEOUT,
             _unit_client, f'{args.cluster_name}', 'matrixorigin.io/component=ProxySet')
    offload_ob_cn(_controller_client, _unit_client, args)
    # 等待CN和Proxy下线
    # wait_for(lambda c, n, s: len([p.metadata.name for p in c.v1_api.list_namespaced_pod(n, label_selector=s).items]) == 0,
    #          5, PODS_BECOME_RUNNING_TIMEOUT,
    #          _unit_client, f'{args.cluster_name}', 'matrixorigin.io/component=CNSet')
    # wait_for_cn_offload(_unit_client, args)
    # wait_for(lambda c, n, s: len([p.metadata.name for p in c.v1_api.list_namespaced_pod(n, label_selector=s).items]) == 0,
    #          5, PODS_BECOME_RUNNING_TIMEOUT,
    #          _unit_client, f'{args.cluster_name}', 'matrixorigin.io/component=ProxySet')


def offload_dn_and_log(_controller_client: K8sClient, _unit_client: K8sClient, args: argparse.Namespace):
    logger.info(f"Start to offload DN and Log in cluster of {args.cluster_name}.")
    root_cluster = _controller_client.core_matrixone_cloud_v1alpha1_api.read_cluster(args.cluster_name, _preload_content=False)
    assert root_cluster[1] == 200, f"Failed to read root cluster '{args.cluster_name}'."
    root_cluster[0]['spec']['dnSets'][0]['replicas'] = 0
    body = {
        'spec': {
            'dnSets': root_cluster[0]['spec']['dnSets'],
            'logSet': {
                'replicas': 0
            }
        }
    }
    patch_root_cluster = _controller_client.core_matrixone_cloud_v1alpha1_api.patch_cluster(args.cluster_name, body)
    assert patch_root_cluster[1] == 200, f"Failed to patch root cluster '{args.cluster_name}'."
    # 等待DN和Log下线
    wait_for(lambda c, n, s: len([p.metadata.name for p in c.v1_api.list_namespaced_pod(n, label_selector=s).items]) == 0,
             5, PODS_BECOME_RUNNING_TIMEOUT,
             _unit_client, f'{args.cluster_name}', 'matrixorigin.io/component=DNSet')
    wait_for(lambda c, n, s: len([p.metadata.name for p in c.v1_api.list_namespaced_pod(n, label_selector=s).items]) == 0,
             5, PODS_BECOME_RUNNING_TIMEOUT,
             _unit_client, f'{args.cluster_name}', 'matrixorigin.io/component=LogSet')


def upgrade(_controller_client: K8sClient, _unit_client: K8sClient, args: argparse.Namespace):
    logger.info(f"Start to upgrade cluster of {args.cluster_name} to version of {args.upgrade_version}.")
    root_cluster = _controller_client.core_matrixone_cloud_v1alpha1_api.read_cluster(args.cluster_name, _preload_content=False)
    assert root_cluster[1] == 200, f"Failed to read root cluster '{args.cluster_name}'."
    root_cluster[0]['spec']['cnSets'][0]['replicas'] = 2
    root_cluster[0]['spec']['cnSets'][0]['scalingConfig']['maxReplicas'] = 10
    root_cluster[0]['spec']['cnSets'][0]['scalingConfig']['minReplicas'] = 2
    root_cluster[0]['spec']['cnPools'][0]['poolStrategy']['scaleStrategy']['maxIdle'] = 1
    root_cluster[0]['spec']['dnSets'][0]['replicas'] = 1
    body = {
        'spec': {
            'operatorVersion': 'v1.3.0',
            # 'upgradeStrategy': {
            #     'upgradeSchema': True,
            #     'components': [
            #         {
            #             'name': 'proxy',
            #             'needs': ['cn']
            #         },
            #         {
            #             'name': 'cn',
            #             'needs': ['dn']
            #         },
            #         {
            #             'name': 'dn',
            #             'needs': ['log']
            #         },
            #         {
            #             'name': 'log'
            #         }
            #     ]
            # },
            'cnPools': root_cluster[0]['spec']['cnPools'],
            'endpoint': {
                'proxySpec': {
                    'image': f'{args.cluster_image_repository}:{args.upgrade_version}',
                    'pluginImage': 'registry.cn-hangzhou.aliyuncs.com/mocloud/plugin:0.11.0-17e89d6-2024-12-04',
                    'replicas': 2,
                }
            },
            'cnSets': root_cluster[0]['spec']['cnSets'],
            'logSet': {
                'replicas': 3
            },
            'dnSets': root_cluster[0]['spec']['dnSets'],
            'version': args.upgrade_version
        }
    }
    patch_root_cluster = _controller_client.core_matrixone_cloud_v1alpha1_api.patch_cluster(args.cluster_name, body)
    assert patch_root_cluster[1] == 200, f"Failed to patch root cluster '{args.cluster_name}'."
    time.sleep(2 * 60)
    ob_sys_cluster = _controller_client.core_matrixone_cloud_v1alpha1_api.read_cluster(f'{args.cluster_name}-ob-sys', _preload_content=False)
    if ob_sys_cluster[1] == 404:
        logger.info(f"Cluster '{args.cluster_name}-ob-sys' is not found.")
    elif ob_sys_cluster[1] == 200:
        ob_sys_cluster[0]['spec']['cnSets'][0]['scalingConfig']['maxReplicas'] = 10
        ob_sys_cluster[0]['spec']['cnSets'][0]['scalingConfig']['minReplicas'] = 1
        ob_sys_body = {
            'spec': {
                'cnSets': ob_sys_cluster[0]['spec']['cnSets']
            }
        }
        patch_ob_sys_cluster = _controller_client.core_matrixone_cloud_v1alpha1_api.patch_cluster(
            f'{args.cluster_name}-ob-sys', ob_sys_body)
        assert patch_ob_sys_cluster[1] == 200, f"Failed to patch cluster '{args.cluster_name}-ob-sys'."
    else:
        raise Exception(f"Failed to read cluster '{args.cluster_name}-ob-sys'.")
    sys_cluster = _controller_client.core_matrixone_cloud_v1alpha1_api.read_cluster(f'{args.cluster_name}-sys', _preload_content=False)
    if sys_cluster[1] == 404:
        logger.info(f"Cluster '{args.cluster_name}-sys' is not found.")
    elif sys_cluster[1] == 200:
        sys_cluster[0]['spec']['cnSets'][0]['scalingConfig']['maxReplicas'] = 1
        sys_cluster[0]['spec']['cnSets'][0]['scalingConfig']['minReplicas'] = 1
        sys_body = {
            'spec': {
                'cnSets': sys_cluster[0]['spec']['cnSets']
            }
        }
        patch_sys_cluster = _controller_client.core_matrixone_cloud_v1alpha1_api.patch_cluster(
            f'{args.cluster_name}-sys', sys_body)
        assert patch_sys_cluster[1] == 200, f"Failed to patch cluster '{args.cluster_name}-sys'."
    else:
        raise Exception(f"Failed to read cluster '{args.cluster_name}-sys'.")
    # 等待集群升级完成
    # wait_for(containers_image_upgraded,
    #          10, PODS_BECOME_RUNNING_TIMEOUT,
    #          _unit_client, args.cluster_name, f'{args.cluster_image_repository}:{args.upgrade_version}')
    wait_for(cluster_pods_are_running,
             10, PODS_BECOME_RUNNING_TIMEOUT,
             _controller_client, _unit_client, args, f'{args.cluster_image_repository}:{args.upgrade_version}')
    upgrade_strategy_body = {
        'spec': {
            'upgradeStrategy': {
                'upgradeSchema': True,
                'components': [
                    {
                        'name': 'proxy',
                        'needs': ['cn']
                    },
                    {
                        'name': 'cn',
                        'needs': ['dn']
                    },
                    {
                        'name': 'dn',
                        'needs': ['log']
                    },
                    {
                        'name': 'log'
                    }
                ]
            }
        }
    }
    patch_root_cluster = _controller_client.core_matrixone_cloud_v1alpha1_api.patch_cluster(args.cluster_name, upgrade_strategy_body)
    assert patch_root_cluster[1] == 200, f"Failed to patch root cluster '{args.cluster_name}'."


if __name__ == '__main__':
    args_parser = parse_args('moc', 'MOC-2.0-升级测试', None)
    parser_args = args_parser.parse_args()
    if os.getenv('GITHUB_RUN_NUMBER'):
        init_logging(parser_args.verbose, 'migrate')
    else:
        init_logging(parser_args.verbose)
    logger = logging.getLogger(__name__)
    logger.info(f"Start to migrate MO cluster {parser_args.cluster_name} to version {parser_args.upgrade_version}.")
    controller_client, unit_client = init_k8s_client('controller'), init_k8s_client('unit')
    _cluster_manager = MOClusterManager(controller_client)
    _cluster = _cluster_manager.get_cluster(parser_args.cluster_name)
    # cluster存在，则必须是root/main
    if _cluster:
        # cluster是root/main，则状态必须是'Active'
        if _cluster.is_root:
            if _cluster.is_active():
                setattr(parser_args, 'tid', get_table_id(unit_client, parser_args))
                offload_cn_and_proxy(controller_client, unit_client, parser_args)
                make_ckp(controller_client, unit_client, parser_args, 'migration checkpointed')
                offload_dn_and_log(controller_client, unit_client, parser_args)
                config_migrate(unit_client, parser_args)
                if make_migrate(unit_client, parser_args) is None:
                   upgrade(controller_client, unit_client, parser_args)
                   time.sleep(3 * 60)
                   upgrade_tenants(unit_client, parser_args)
            else:
                _msg = f"Cluster '{_cluster.name}' is in phase of '{_cluster.phase.value}' not 'Active'."
                logger.error(_msg)
                raise Exception(_msg)
        else:
            _msg = f"Cluster '{_cluster.name}' exists but not root."
            logger.error(_msg)
            raise Exception(_msg)
    else:
        logger.warning(f"Cluster '{parser_args.cluster_name}' is not found.")
