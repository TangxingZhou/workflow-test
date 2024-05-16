import os

sysbench_envs = {
    'MOC_INSTANCE_HOST': 'restore',
    'MOC_INSTANCE_PORT': 6001,
    'MOC_INSTANCE_USER': 'root',
    'MOC_INSTANCE_PASSWORD': '123'
}
for k, v in sysbench_envs.items():
    os.system(f'echo "export {k}={v}" >> .env')
os.system(f'''echo "mo-host={sysbench_envs.get('MOC_INSTANCE_HOST', '')}" >> $GITHUB_OUTPUT''')
os.system(f'''echo "mo-port={sysbench_envs.get('MOC_INSTANCE_PORT', '')}" >> $GITHUB_OUTPUT''')
os.system(f'''echo "mo-user={sysbench_envs.get('MOC_INSTANCE_USER', '')}" >> $GITHUB_OUTPUT''')
os.system(f'''echo "mo-password={sysbench_envs.get('MOC_INSTANCE_PASSWORD', '')}" >> $GITHUB_OUTPUT''')
