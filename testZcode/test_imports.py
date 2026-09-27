import sys
sys.path.insert(0,'src')

failed = []
for m in [
    'openminis.agent.zcode',
    'openminis.agent.zcode.contracts',
    'openminis.agent.zcode.contracts.ids',
    'openminis.agent.zcode.contracts.model',
    'openminis.agent.zcode.contracts.errors',
    'openminis.agent.zcode.contracts.tracing',
    'openminis.agent.zcode.contracts.events',
    'openminis.agent.zcode.agent',
    'openminis.agent.zcode.agent.turn_state',
    'openminis.agent.zcode.agent.turn_machine',
    'openminis.agent.zcode.agent.message_history',
    'openminis.agent.zcode.tool',
    'openminis.agent.zcode.tool.types',
    'openminis.agent.zcode.tool.registry',
    'openminis.agent.zcode.tool.scheduler',
    'openminis.agent.zcode.tool.input_normalization',
    'openminis.agent.zcode.tool.executor',
    'openminis.agent.zcode.tool.executor.types',
    'openminis.agent.zcode.tool.executor.impl',
    'openminis.agent.zcode.tool.executor.batch_runner',
    'openminis.agent.zcode.tool.executor.background_task_registry',
    'openminis.agent.zcode.tool.executor.call_runner',
    'openminis.agent.zcode.tool.executor.hook_flow',
    'openminis.agent.zcode.tool.executor.permission_flow',
    'openminis.agent.zcode.tool.handlers',
    'openminis.agent.zcode.tool.handlers.agent',
    'openminis.agent.zcode.tool.handlers.glob',
    'openminis.agent.zcode.tool.handlers.grep',
    'openminis.agent.zcode.tool.handlers.bash_command_parser',
    'openminis.agent.zcode.tool.handlers.workflow_script_path',
    'openminis.agent.zcode.runtime',
    'openminis.agent.zcode.runtime.types',
    'openminis.agent.zcode.runtime.deps',
    'openminis.agent.zcode.runtime.internal',
    'openminis.agent.zcode.runtime_task',
    'openminis.agent.zcode.runtime_task.registry',
    'openminis.agent.zcode.runtime_task.notification',
    'openminis.agent.zcode.agent_entry',
]:
    try:
        __import__(m)
        print('OK  '+m)
    except Exception as e:
        print('FAIL '+m+' -> '+type(e).__name__+' '+str(e)[:100])
        failed.append(m)
print()
print('Result:', 'ALL OK' if not failed else f'FAILED {len(failed)}')