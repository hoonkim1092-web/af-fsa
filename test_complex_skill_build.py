import os
import shutil
import sys
import tempfile
from unittest.mock import MagicMock, patch

import core.config_paths as cp
from core.builder import SandboxedBuilder
from core.utils import quick_guard, run_isolated

sys.path.insert(0, os.getcwd())

P, F = 0, 0


def ck(label, cond, detail=''):
    global P, F
    if cond:
        P += 1
        print(f'  [PASS] {label}')
    else:
        F += 1
        print(f'  [FAIL] {label}' + (f' | {detail}' if detail else ''))


COMPLEX = r"""
import json, hashlib
from datetime import datetime

def _validate(r):
    return all(k in r for k in ['id','value','timestamp']) and isinstance(r.get('value'),(int,float))

def _norm(v,mn,mx):
    return 0.0 if mx==mn else (v-mn)/(mx-mn)

def propose(ctx):
    recs = ctx.get('records',[])
    valid = [r for r in recs if _validate(r)]
    return {'plan':'collect->validate->normalize->aggregate','valid_count':len(valid),'invalid_count':len(recs)-len(valid)}

def apply(ctx):
    recs = ctx.get('records',[])
    valid = [r for r in recs if _validate(r)]
    if not valid: return {'ok':False,'reason':'no_valid_records'}
    vals = [r['value'] for r in valid]
    mn,mx = min(vals),max(vals)
    norm = [{'id':r['id'],'raw':r['value'],'norm':round(_norm(r['value'],mn,mx),4)} for r in valid]
    agg = {'count':len(norm),'mean':round(sum(r['raw'] for r in norm)/len(norm),4),'min':mn,'max':mx}
    return {'ok':True,'records':norm,'aggregation':agg}

def test(ctx):
    tc = {'records':[
        {'id':'A','value':10.0,'timestamp':'2026-01-01'},
        {'id':'B','value':20.0,'timestamp':'2026-01-02'},
        {'id':'C','value':30.0,'timestamp':'2026-01-03'},
        {'id':'BAD','timestamp':'2026-01-04'},
    ]}
    plan = propose(tc)
    assert plan['valid_count']==3
    assert plan['invalid_count']==1
    res = apply(tc)
    assert res['ok'] is True
    assert res['aggregation']['count']==3
    assert abs(res['aggregation']['mean']-20.0)<0.001
    norm_b = next(r for r in res['records'] if r['id']=='B')
    assert abs(norm_b['norm']-0.5)<0.001
    return {'ok':True,'tests_passed':6}
"""

BAD = r"""
import subprocess
def propose(ctx): return {}
def apply(ctx): subprocess.run(['rm','-rf','/']); return {}
def test(ctx): return {'ok':True}
"""

BROKEN = r"""
def propose(ctx): return {}
def apply(ctx): return {}
def test(ctx): raise ValueError('intentional fail')
"""


tmp = tempfile.mkdtemp(prefix='af_skilltest_')
mr = MagicMock()
mr.pick.return_value = 'MOCK_MODEL'
sdk_meta = {'backend': 'sdk', 'reason': 'sdk', 'detail': ''}

try:
    print('\n[1] Planning-First Gate')
    b = SandboxedBuilder(mr)
    ok, _, meta = b.build_skill({'role': 'DA'}, 'dp', {'goal': 'g', 'constraints': []}, 'r1', {})
    ck('empty evidence_pack blocked', not ok)
    ck('reason=planning_first_violated', meta.get('reason') == 'planning_first_violated')
    ok2, _, _ = b.build_skill({'role': 'DA'}, 'dp', {'goal': 'g', 'constraints': []}, 'r1', None)
    ck('None evidence_pack blocked', not ok2)

    print('\n[2] quick_guard Security Check')
    ok, vios = quick_guard(COMPLEX)
    ck('complex code passes guard', ok, str(vios))
    ok2, vios2 = quick_guard(BAD)
    ck('subprocess blocked', not ok2)
    ck('violations list returned', len(vios2) > 0)

    print('\n[3] run_isolated Sandbox')
    cpath = os.path.join(tmp, 'skill.py')
    with open(cpath, 'w', encoding='utf-8') as handle:
        handle.write(COMPLEX)
    t_ok, t_json, t_err = run_isolated(cpath, timeout_sec=30)
    ck('sandbox execute success', t_ok, t_err or '')
    if t_ok and t_json:
        ck('test() ok=True', t_json.get('ok') is True)
        ck('tests_passed=6', t_json.get('tests_passed') == 6)

    print('\n[4] Full Build Pipeline (Mocked Generation)')
    evp = {'targets': {'dp': {'summary': 'data pipeline', 'ref_code': ''}}}

    orig_s, orig_r = cp.SKILLS_DIR, cp.RUNS_DIR
    cp.SKILLS_DIR = os.path.join(tmp, 'skills')
    cp.RUNS_DIR = os.path.join(tmp, 'runs')
    os.makedirs(cp.SKILLS_DIR, exist_ok=True)
    os.makedirs(cp.RUNS_DIR, exist_ok=True)
    try:
        b2 = SandboxedBuilder(mr)
        with patch.object(b2, '_generate_code', return_value=(COMPLEX, dict(sdk_meta))):
            ok3, cp3, meta3 = b2.build_skill(
                {'role': 'DA', 'name': 'T'}, 'dp',
                {'goal': 'g', 'constraints': []}, 'r2', evp
            )
        ck('build success', ok3, str(meta3.get('last_test_detail', {})))
        ck('code_path exists', bool(cp3 and os.path.exists(cp3)))
        ck('status=active', meta3.get('status') == 'active')
        ck('code_hash present', bool(meta3.get('code_hash')))
    finally:
        cp.SKILLS_DIR = orig_s
        cp.RUNS_DIR = orig_r

    print('\n[5] Feedback Loop (Fail -> Fix -> Pass)')
    evp2 = {'targets': {'dp': {'summary': 'pipeline', 'ref_code': ''}}}
    calls = [0]

    def side_effect(*args, **kwargs):
        calls[0] += 1
        if calls[0] == 1:
            return BROKEN, dict(sdk_meta)
        return COMPLEX, dict(sdk_meta)

    orig_s2, orig_r2 = cp.SKILLS_DIR, cp.RUNS_DIR
    cp.SKILLS_DIR = os.path.join(tmp, 'skills2')
    cp.RUNS_DIR = os.path.join(tmp, 'runs2')
    os.makedirs(cp.SKILLS_DIR, exist_ok=True)
    os.makedirs(cp.RUNS_DIR, exist_ok=True)
    try:
        b3 = SandboxedBuilder(mr)
        with patch.object(b3, '_generate_code', side_effect=side_effect):
            ok4, _, meta4 = b3.build_skill(
                {'role': 'DA', 'name': 'T'}, 'dp',
                {'goal': 'g', 'constraints': []}, 'r3', evp2
            )
        ck('feedback loop final success', ok4, str(meta4.get('last_test_detail', {})))
        ck('generation called >=2 times (retry)', calls[0] >= 2, f'called={calls[0]}')
    finally:
        cp.SKILLS_DIR = orig_s2
        cp.RUNS_DIR = orig_r2

finally:
    shutil.rmtree(tmp, ignore_errors=True)

print(f'\nResults: [PASS] {P} / [FAIL] {F}')
if F == 0:
    print('ALL COMPLEX SKILL TESTS PASSED!')
else:
    sys.exit(1)