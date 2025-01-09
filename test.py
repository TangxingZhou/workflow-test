import os
import time
# sysbench_envs = {
#     'MOC_INSTANCE_HOST': 'restore-freetier-01.cn-hangzhou.cluster.cn-qa.matrixone.tech',
#     'MOC_INSTANCE_PORT': 6001,
#     'MOC_INSTANCE_USER': '018f7d14_c1fe_76f0_957f_f7c352b4bec6:admin:accountadmin',
#     'MOC_INSTANCE_PASSWORD': 'Admin123'
# }
# for k, v in sysbench_envs.items():
#     os.system(f'echo "export {k}={v}" >> .env')
# print(os.getenv('GITHUB_RUN_NUMBER'))
# if os.getenv('GITHUB_RUN_NUMBER'):
#     print('hello world')
# os.system(f'''echo "mo-host={sysbench_envs.get('MOC_INSTANCE_HOST', '')}" >> $GITHUB_OUTPUT''')
# os.system(f'''echo "mo-port={sysbench_envs.get('MOC_INSTANCE_PORT', '')}" >> $GITHUB_OUTPUT''')
# os.system(f'''echo "mo-user={sysbench_envs.get('MOC_INSTANCE_USER', '')}" >> $GITHUB_OUTPUT''')
# os.system(f'''echo "mo-password={sysbench_envs.get('MOC_INSTANCE_PASSWORD', '')}" >> $GITHUB_OUTPUT''')


# import signal
# import time


# def handler(signum, frame):
#     print(f"Received {signum} signal")


# # 设置信号处理函数
# # for i in range(1, 32):
# #     signal.signal(i, handler)
# signal.signal(signal.SIGINT, handler)
# signal.signal(signal.SIGTERM, handler)
# signal.signal(signal.SIGCONT, handler)
# # signal.signal(signal.SIGSTOP, handler)
# # signal.signal(signal.SIGKILL, handler)
# signal.signal(signal.SIGQUIT, handler)
# signal.signal(signal.SIGTSTP, handler)


# while True:
#     print("Running...")
#     time.sleep(1)

os.system(f'''echo "default-cluster=freetier-01" >> $GITHUB_OUTPUT''')

time.sleep(5 * 60)

