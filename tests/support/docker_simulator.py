"""为部署事务测试模拟 Docker 状态；不访问真实 Docker。"""
import json
import os
import pathlib
import sys

state_path = pathlib.Path(os.environ['DEPLOY_TEST_STATE'])
state = json.loads(state_path.read_text(encoding='utf-8'))
args = sys.argv[1:]
containers = state['containers']
result = 0
output = ''
state['calls'].append(args)
if args[0] == 'build':
    result = 1 if state['failure'] == 'build' else 0
elif args[:2] == ['container', 'inspect']:
    result = 0 if args[2] in containers else 1
elif args[0] == 'inspect':
    output = containers[args[-1]]['image']
elif args[0] == 'rename':
    containers[args[2]] = containers.pop(args[1])
elif args[0] in ('stop', 'start'):
    containers[args[1]]['running'] = args[0] == 'start'
elif args[0] == 'rm':
    for name in args[1:]:
        containers.pop(name, None)
elif args[0] == 'run':
    name = args[args.index('--name') + 1]
    environment = dict(args[index + 1].split('=', 1) for index, value in enumerate(args) if value == '-e')
    containers[name] = {'image': 'new-image', 'running': True,
                        'port': args[args.index('-p') + 1], 'environment': environment, 'mounts': []}
    result = 1 if state['failure'] == 'run' else 0
elif args[0] == 'exec':
    node = containers.get(args[1])
    result = 0 if node and node['running'] else 1
    if node and node['image'] == 'new-image' and state['failure'] == 'health':
        result = 1
state_path.write_text(json.dumps(state), encoding='utf-8')
print(output)
raise SystemExit(result)
