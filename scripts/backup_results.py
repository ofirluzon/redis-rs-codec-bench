#!/usr/bin/env python3
"""Pull snapshots without removing remote results. Host supplied only when ready."""
import argparse
from pathlib import Path
import re
import subprocess
import time

def main():
    p=argparse.ArgumentParser()
    p.add_argument('ssh_host',help='SSH alias or user@hostname; keys/config handled by SSH')
    p.add_argument('remote_directory',help='Absolute results directory')
    p.add_argument('local_directory',type=Path)
    p.add_argument('--every-seconds',type=int,default=0,help='0 pulls once; e.g. 600 pulls periodically')
    args=p.parse_args()
    if not re.fullmatch(r'[A-Za-z0-9_.@-]+',args.ssh_host) or args.ssh_host.startswith('-'):
        p.error('Use an SSH alias or ordinary user@hostname')
    if not re.fullmatch(r'/[A-Za-z0-9_./-]+',args.remote_directory):p.error('Remote directory must be a simple absolute path')
    if args.every_seconds<0:p.error('Interval cannot be negative')
    args.local_directory.mkdir(parents=True,exist_ok=True)
    command=['rsync','-az','--partial',args.ssh_host+':'+args.remote_directory.rstrip('/')+'/',str(args.local_directory)+'/']
    while True:
        result=subprocess.run(command)
        if not args.every_seconds:raise SystemExit(result.returncode)
        if result.returncode:print('Snapshot failed; retrying next interval',flush=True)
        time.sleep(args.every_seconds)

if __name__=='__main__':main()
