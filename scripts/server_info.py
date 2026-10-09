"""Optional endpoint INFO sampler; credentials remain in the child environment."""
import os
import shutil
import subprocess
from urllib.parse import urlsplit, unquote

NUMERIC = ['used_cpu_user','used_cpu_sys','used_cpu_user_main_thread','used_cpu_sys_main_thread',
           'total_commands_processed','total_net_input_bytes','total_net_output_bytes',
           'used_memory','used_memory_rss','connected_clients','rejected_connections',
           'instantaneous_ops_per_sec']

def collect(url):
    executable = shutil.which('redis-cli')
    if not executable: return {'unavailable':'redis-cli not installed'}
    target = urlsplit(url)
    if target.scheme not in ['redis','rediss'] or not target.hostname:
        return {'unavailable':'INFO collector supports redis:// and rediss:// endpoints'}
    argv = [executable,'--raw','--no-auth-warning','-h',target.hostname,'-p',str(target.port or 6379)]
    env = dict(os.environ)
    env.pop('REDISCLI_AUTH',None)
    if target.password is not None: env['REDISCLI_AUTH']=unquote(target.password)
    if target.username: argv+=['--user',unquote(target.username)]
    if target.scheme=='rediss': argv+=['--tls']
    argv+=['INFO','ALL']
    try:
        completed=subprocess.run(argv,env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,timeout=3)
        # Do not preserve error strings which could expose endpoint details.
        if completed.returncode: return {'unavailable':'INFO command failed'}
        fields={}
        for line in completed.stdout.splitlines():
            if ':' in line and not line.startswith('#'):
                key,value=line.split(':',1)
                if key in NUMERIC:
                    try: fields[key]=float(value) if '.' in value else int(value)
                    except ValueError: pass
        if not fields: return {'unavailable':'INFO counters absent or not permitted'}
        return {'scope':'one endpoint; does not cover all cluster nodes', 'counters':fields}
    except (OSError,subprocess.TimeoutExpired): return {'unavailable':'INFO sampler failed or timed out'}
