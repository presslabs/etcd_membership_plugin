#!/usr/bin/python
import json
import urlparse

from ansible.module_utils.basic import *


# from ansible.module_utils import basic
# basic.MODULE_COMPLEX_ARGS = json.dumps(dict(
#     state="present",
#     cluster_urls="http://127.0.0.3:4001",
#     name="node0",
# ))


try:
    import requests
except ImportError:
    requests = None


def check_requirements(module):
    if requests is None:
        err_msg = "Failed to import requests module"
        module.fail_json(msg=err_msg)


class EtcdException(Exception):
    pass


class StateHandler(object):
    """Manages your node state, transitions your node between states
    Currently nodes are identified via their name or peer_urls.
    Uses the membership api to register/unregister quorum members.
    Not involved in initial cluster bootstrapping.
    Proxy nodes need no registration.
    See: https://github.com/coreos/etcd/blob/master/Documentation/other_apis.md#list-members
    """
    def __init__(self, name, state, cluster_urls, advertised_peer_urls=tuple(), node_id=None):
        """
        @name: name of node
        @state: desired node state
        @cluster_urls: urls of existing cluster members
        """
        self.name = name
        self.state = state
        self.advertised_peer_urls = advertised_peer_urls
        self.node_id = node_id
        self.ctl = EtcdCtl(cluster_urls)
        self.cluster_state = self.ctl.list()
        self._health = None

    def _get_node_data(self):
        node_data = self.cluster_state['members'].get(self.name)
        if node_data is None:
            # unstarted nodes are registered but don't yet have a name
            # match them by peer_urls
            for url in self.advertised_peer_urls:
                node_data = self.cluster_state['urls'].get(url)
                if node_data is not None:
                    if node_data['name'] != '':
                        raise AssertionError(
                            'This is not an unstarted node {}'.format(node_data['id']))
                    break
        return node_data

    def absent(self):
        """Ensures member is removed from cluster if it's still in the peer list.
        If the node is in the unstarted state it will be matched by it's advertised
        peer_urls.
        """
        node_data = self._get_node_data()
        if node_data is None:
            # node not in cluster, nothing to do here
            return False, "Nothing to do here, node {},{} not in cluster.".format(
                self.name, self.advertised_peer_urls)
        health = self.ctl.health()
        if not health['all_good']:
            if node_data['id'] not in self.cluster_state['unstarted']:
                return False, "refusing to remove started node, cluster is not healthy!"

        if self.node_id is not None and self.node_id != node_data['id']:
            raise EtcdException(
                "There's a glitch in the matrix: node_id:{} != {}".format(
                    self.node_id, node_data['id']))
        if self.ctl.remove_member(node_data['id']):
            return True, "removed node {},{}".format(self.name, node_data['id'])
        else:
            return False, "failed to remove node {},{}".format(self.name, node_data['id'])

    def present(self):
        """Ensures member is registered in the cluster"""
        node_data = self._get_node_data()
        if node_data is None:
            if len(self.cluster_state['unstarted']):
                return False, "refusing to add member! cluster has {} unstarted nodes".format(
                    len(self.cluster_state['unstarted']))
            self.ctl.add_member(self.advertised_peer_urls)
            return True, "added member {}".format(self.advertised_peer_urls)
        else:
            return False, "node {} already a member".format(self.advertised_peer_urls)

    def transition(self):
        """Transition node to desired state"""
        self._health = None
        changed, message = getattr(self, self.state)()
        self.cluster_state = self.ctl.list()
        return changed, message

    def get_health(self):
        if self._health is None:
            self._health = self.ctl.health()
        return self._health


class EtcdCtl(object):
    def __init__(self, peers):
        self._peers = peers

        for url in self._peers:
            if urlparse.urlsplit(url).path not in ("", "/"):
                raise EtcdException(
                    "URL must not contain a path: {}".format(url))

    def _request(self, url, data=None, method='get'):
        '''Performs a request attempting all the cluster members in
        turn, ignoring unreachable ones.
        '''
        for peer in self._peers:
            call = getattr(requests, method)
            try:
                full_url = urlparse.urljoin(peer, url)
                resp = call(
                    full_url,
                    data=data,
                    headers={'Content-Type': 'application/json'})
                if resp.status_code >= 400:
                    raise EtcdException(
                        dict(
                            url=full_url,
                            method=method,
                            status=resp.status_code,
                            data=data,
                            response_content=resp.content))
                return resp
            except requests.ConnectionError:
                continue
        raise EtcdException("failed to contact etcd servers, [{}]".format(
            ", ".join(self._peers)
        ))

    def health(self):
        """Query all members about cluster health.
        """
        state = {'all_good': True}
        for member in self.list()['members'].itervalues():
            node_id = member['id']
            if len(member['clientURLs']) == 0:
                state[node_id] = "unstarted"
                state['all_good'] = False
            else:
                for client_url in member['clientURLs']:
                    try:
                        full_url = urlparse.urljoin(client_url, '/health')
                        resp = requests.get(full_url)
                        if resp.status_code == 200:
                            state[node_id] = resp.json()
                        elif resp.status_code == 404:
                            raise Exception("{}: {}".format(resp.content, resp.url))
                        else:
                            state['all_good'] = False
                        break
                    except requests.ConnectionError:
                        # this client_url is unreachable; try the next one
                        pass
                else:
                    # none of the clientURLs were reachable
                    state[node_id] = "unreachable"
                    state['all_good'] = False
        return state

    def list(self):
        data = {'names': [], 'urls': {}, 'unstarted':[], 'members': {}}
        resp = self._request('/v2/members')
        for member in resp.json()['members']:
            if member['name'] == '':
                name = 'unstarted:{}'.format(member['id'])
                data['unstarted'].append(member['id'])
            else:
                name = member['name']
            data['members'][name] = member
            for url in member['peerURLs']:
                data['urls'][url] = member
            data['names'].append(name)
        return data

    def add_member(self, peer_urls):
        return self._request(
            '/v2/members',
            data=json.dumps({'peerURLs': peer_urls}),
            method='post').json()

    def remove_member(self, node_id):
        return self._request(
            '/v2/members/{}'.format(node_id),
            method='delete').ok


def main():
    module = AnsibleModule(
        argument_spec={
            'name': {'required': True, 'type': 'str'},
            'cluster_urls': {
                'required': True, 'type': 'str'},
            'state': {
                'required': True, 'type': 'str',
                'choices': ['proxy', 'absent', 'present']
            },
            'advertised_peer_urls': {
                'required': False, 'type': 'str', 'default': ''},
        })
    check_requirements(module)
    params = module.params
    cluster_urls = params.pop('cluster_urls').strip(',').split(',')
    advertised_peer_urls = params.pop('advertised_peer_urls').strip(',').split(',')
    handler = StateHandler(
        cluster_urls=cluster_urls, advertised_peer_urls=advertised_peer_urls, **params)
    try:
        changed, msg = handler.transition()
        module.exit_json(changed=changed, msg=msg, cluster_state=handler.cluster_state, health=handler.get_health())
    except EtcdException as err:
        module.fail_json(msg=str(err))


if __name__ == '__main__':
    main()
