import builtins, sys, os
def sp(*a, **k):
    enc = getattr(sys.stdout, 'encoding', None) or 'utf-8'
    builtins.print(*[str(x).encode(enc, errors='replace').decode(enc) for x in a], **k)
print = sp

sys.path.insert(0, os.getcwd())

import py_compile
py_compile.compile('core/terminal_bridge.py', doraise=True)
print('[PASS] Compile OK')

from core.terminal_bridge import TerminalBridge, _is_command_safe

P, F = 0, 0
def ck(label, cond):
    global P, F
    if cond: P+=1; print(f'  [PASS] {label}')
    else: F+=1; print(f'  [FAIL] {label}')

# 1. Safety checks
print('\n[1] Safety checks')
ok, _ = _is_command_safe('python test.py')
ck('python allowed', ok)
ok, _ = _is_command_safe('pytest -v')
ck('pytest allowed', ok)
ok, _ = _is_command_safe('git status')
ck('git allowed', ok)
ok, _ = _is_command_safe('rm -rf /')
ck('rm -rf blocked', not ok)
ok, _ = _is_command_safe('shutdown /s')
ck('shutdown blocked', not ok)
ok, _ = _is_command_safe('curl https://example.com')
ck('curl allowed', ok)
ok, _ = _is_command_safe('format c:')
ck('format blocked', not ok)
ok, _ = _is_command_safe('dangerous_tool --nuke')
ck('unknown cmd blocked', not ok)
ok, _ = _is_command_safe('')
ck('empty cmd blocked', not ok)

# 2. run_command
print('\n[2] run_command')
tb = TerminalBridge()
r = tb.run_command('python --version')
ck('python --version OK', r['ok'])
stdout_stderr = r.get('stdout', '') + r.get('stderr', '')
ck('stdout has Python', 'Python' in stdout_stderr)
ck('elapsed_ms present', 'elapsed_ms' in r)

r = tb.run_command('python -c "print(42)"')
ck('inline python OK', r['ok'])
ck('output is 42', '42' in r.get('stdout', ''))

# blocked command
r = tb.run_command('rm -rf /')
ck('blocked cmd returns error', not r['ok'])

# 3. run_repl
print('\n[3] run_repl')
r = tb.run_repl(runtime='python', commands=['print(1+1)', 'print("hello")'])
ck('repl returns ok', r['ok'])
ck('outputs has 2 entries', len(r.get('outputs', [])) == 2)
out0 = r.get('outputs', [{}])[0].get('output', '')
out1 = r.get('outputs', [{}])[1].get('output', '')
ck('first output is 2', '2' in out0)
ck('second output is hello', 'hello' in out1)

# 4. Command log
print('\n[4] Command log')
log = tb.get_command_log()
ck('log has entries', len(log) > 0)
ck('log entry has ts', 'ts' in log[-1])

# 5. Timeout check (non-blocking)
print('\n[5] Safety - short timeout')
r = tb.run_command('python -c "import time; time.sleep(0.1); print(123)"', timeout=10)
ck('short sleep completes', r['ok'])
ck('output has 123', '123' in r.get('stdout', ''))

print(f'\nResults: [PASS] {P} / [FAIL] {F}')
if F == 0:
    print('ALL TESTS PASSED!')
else:
    sys.exit(1)
