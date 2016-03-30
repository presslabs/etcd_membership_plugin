# Ansible plugin for managing etcd cluster membership
[![Build Status](https://travis-ci.org/PressLabs/etcd_membership_plugin.svg?branch=master)](https://travis-ci.org/PressLabs/etcd_membership_plugin)

## Installing
Simply copy `library/pl_etcd.py` to your project's `library` directory.

## License
The plugin is licensed under the GPL 3.0 License.

## Example usage
See etcd.yml for an example.

# Development
Create and activate a virtualenv then install the package and the dependencies.

```
pip install -e .
pip install -r requirements.dev
```

## Running the tests
You need to install docker and should be able to connect to the docker daemon as the user you're gonna run the tests with.
You can achieve this by adding your user to the docker group.

Once you have docker setup simply run:

```
py.test
```

## Testing with ansible
If you want to test with ansible you can use the provided Vagrantfile to start a 3 node cluster and run playbooks on that.

```
vagrant up
ansible-playbook -i inventory.py docker.yml

# you can hardcode different values for your nodes in inventory.py
# then run the example playbook
ansible-playbook -i inventory.py etcd.yml
```

