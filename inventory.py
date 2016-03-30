#!/usr/bin/env python
# Adapted from Mark Mandel's implementation
# https://github.com/ansible/ansible/blob/devel/plugins/inventory/vagrant.py

# Presslabs Note:
# copied from: https://gist.github.com/lorin/4cae51e123b596d5c60d
# original plugin is GPL v3, therefore we can modify and redistribute it under the GPL v3
import argparse
import json
import paramiko
import subprocess
import sys


def parse_args():
    parser = argparse.ArgumentParser(description="Vagrant inventory script")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--list', action='store_true')
    group.add_argument('--host')
    return parser.parse_args()


def list_running_hosts():
    cmd = "vagrant status --machine-readable"
    status = subprocess.check_output(cmd.split()).rstrip()
    hosts = []
    for line in status.splitlines():
        (_, host, key, value) = line.split(',', 3)
        if key == 'state' and value == 'running':
            hosts.append(host)
    return hosts


def get_host_details(host):
    cmd = "vagrant ssh-config {}".format(host)
    p = subprocess.Popen(cmd.split(), stdout=subprocess.PIPE)
    config = paramiko.SSHConfig()
    config.parse(p.stdout)
    c = config.lookup(host)
    return {
        'ansible_ssh_host': c['hostname'],
        'ansible_ssh_port': c['port'],
        'ansible_ssh_user': c['user'],
        'ansible_ssh_private_key_file': c['identityfile'][0],
        'cluster': {
            'name': 'etcd-cluster-1',
            # this is just a hack to test the etcd membership plugin with vagrant
            # state can be one of new, existing
            'state': 'new',
            # valid roles are: present, proxy, absent
            'role': 'absent' if int(host[-1]) > 3 else 'present'
        }
    }


def main():
    args = parse_args()
    if args.list:
        hosts = list_running_hosts()
        json.dump({'vagrant': hosts}, sys.stdout)
    else:
        details = get_host_details(args.host)
        json.dump(details, sys.stdout)

if __name__ == '__main__':
    main()
