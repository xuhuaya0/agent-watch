"""Prepare an Antigravity sidecar manifest without installing or enabling it."""
import argparse
import json
from pathlib import Path
import sys


def manifest(python, worker, config):
    return {
        'display_name':'Agent Watch replies',
        'description':'Relay explicit panel replies to the configured Antigravity sessions',
        'command':str(python),
        'args':[str(worker),'--config',str(config)],
        'restart_policy':'on-failure',
    }


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',required=True,help='Absolute private control config path on the target computer')
    parser.add_argument('--python',default=sys.executable,help='Native Python executable on the target computer')
    parser.add_argument('--worker',default=str(Path(__file__).with_name('control_worker.py').resolve()))
    parser.add_argument('--output',required=True,help='New staging JSON file; never writes native app settings')
    args=parser.parse_args()
    with Path(args.output).open('x',encoding='utf-8') as stream:
        json.dump(manifest(args.python,args.worker,args.config),stream,ensure_ascii=False,indent=2)
        stream.write('\n')
    print('Prepared sidecar manifest. It has not been installed or enabled.')


if __name__=='__main__':main()
